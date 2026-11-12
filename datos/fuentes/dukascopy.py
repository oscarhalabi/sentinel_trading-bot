"""Fuente Dukascopy: ticks de un día UTC (24 archivos .bi5 por hora)."""
from __future__ import annotations

import lzma
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as hora_del_dia, timedelta, timezone
from typing import Callable

import pyarrow as pa
import pyarrow.compute as pc

from datos.almacen import ESQUEMA_TICKS
from datos.fuentes.base import ClienteHttp, ClienteRequests, InstrumentoFuente, ResultadoDia
from datos.simbolos import Simbolo

_REGISTRO = struct.Struct(">IIIff")  # ms, ask, bid, ask_vol, bid_vol (20 bytes)
_RANGOS_PRECIO = {
    "fx_mayor": (0.0001, 1000), "fx_cruce": (0.0001, 1000), "fx_exotico": (0.0001, 1000),
    "metal": (0.5, 20000), "indice": (50, 200000), "energia": (1, 1000),
    "materia_prima": (0.01, 100000), "accion": (0.01, 100000), "etf": (0.01, 100000),
    "cripto": (0.0001, 1000000),
}


def url_hora(codigo: str, momento: datetime) -> str:
    """URL del archivo de una hora. Dukascopy numera los meses de 00 a 11."""
    return (f"https://datafeed.dukascopy.com/datafeed/{codigo}/{momento.year}/"
            f"{momento.month - 1:02d}/{momento.day:02d}/{momento.hour:02d}h_ticks.bi5")


def parsear_bi5(datos: bytes, hora: datetime, decimales: int) -> pa.Table:
    """Convierte el contenido de un .bi5 en tabla de ticks. Lanza ValueError si está dañado."""
    if not datos:
        return ESQUEMA_TICKS.empty_table()
    try:
        crudo = lzma.decompress(datos)
    except lzma.LZMAError as e:
        raise ValueError(f"LZMA inválido: {e}") from e
    if len(crudo) % _REGISTRO.size:
        raise ValueError(f"tamaño {len(crudo)} no es múltiplo de {_REGISTRO.size}")
    filas = list(_REGISTRO.iter_unpack(crudo))
    escala = 10 ** decimales
    base = hora.astimezone(timezone.utc)
    return pa.table({
        "ts": pa.array([base + timedelta(milliseconds=f[0]) for f in filas],
                       type=ESQUEMA_TICKS.field("ts").type),
        "bid": pa.array([f[2] / escala for f in filas], type=pa.float64()),
        "ask": pa.array([f[1] / escala for f in filas], type=pa.float64()),
        "bid_vol": pa.array([f[4] for f in filas], type=pa.float32()),
        "ask_vol": pa.array([f[3] for f in filas], type=pa.float32()),
    }, schema=ESQUEMA_TICKS)


def escala_razonable(sim: Simbolo, tabla: pa.Table) -> str | None:
    """Devuelve un mensaje si los precios no parecen tener la escala correcta."""
    if tabla.num_rows == 0:
        return None
    bien = pc.sum(pc.cast(pc.less_equal(tabla["bid"], tabla["ask"]), pa.int64())).as_py()
    if bien / tabla.num_rows < 0.99:
        return f"{sim.simbolo}: bid > ask en más del 1 % de los ticks"
    rango = _RANGOS_PRECIO.get(sim.clase)
    if rango:
        mediana = pc.approximate_median(tabla["bid"]).as_py()
        if not rango[0] <= mediana <= rango[1]:
            return (f"{sim.simbolo}: mediana de bid {mediana:g} fuera del rango "
                    f"{rango[0]:g}-{rango[1]:g} de la clase {sim.clase} (¿decimales mal?)")
    return None


class _Limitador:
    """Deja pasar como máximo `por_segundo` peticiones por segundo entre todos los hilos."""

    def __init__(self, por_segundo: float, dormir: Callable[[float], None]) -> None:
        self._intervalo = 1.0 / por_segundo
        self._dormir = dormir
        self._cerrojo = threading.Lock()
        self._proximo = 0.0

    def esperar(self) -> None:
        with self._cerrojo:
            ahora = time.monotonic()
            salida = max(ahora, self._proximo)
            self._proximo = salida + self._intervalo
        if salida > ahora:
            self._dormir(salida - ahora)


class Dukascopy:
    nombre = "dukascopy"

    def __init__(self, http: ClienteHttp | None = None, max_paralelo: int = 4,
                 max_por_segundo: float = 20.0, reintentos: int = 4, espera_base: float = 1.0,
                 dormir: Callable[[float], None] = time.sleep) -> None:
        self._http = http if http is not None else ClienteRequests()
        self._max_paralelo = max_paralelo
        self._reintentos = reintentos
        self._espera_base = espera_base
        self._dormir = dormir
        self._limitador = _Limitador(max_por_segundo, dormir)

    def listar_instrumentos(self) -> list[InstrumentoFuente]:
        raise NotImplementedError("El listado de instrumentos de Dukascopy se hace en la tarea 5.")

    def _bajar_hora(self, codigo: str, hora: datetime, decimales: int) -> pa.Table:
        """Baja una hora con reintentos. Lanza RuntimeError con el motivo si no se logra."""
        url = url_hora(codigo, hora)
        motivo = "sin intentos"
        for intento in range(1 + self._reintentos):
            espera = self._espera_base * 2 ** intento
            self._limitador.esperar()
            try:
                estado, cuerpo = self._http.get(url, 30.0)
                if estado == 200:
                    return parsear_bi5(cuerpo, hora, decimales)
                if estado == 404:
                    return ESQUEMA_TICKS.empty_table()
                motivo = f"HTTP {estado}"
                if estado in (429, 503):
                    espera *= 3
            except Exception as e:  # cualquier fallo se reintenta
                motivo = f"{type(e).__name__}: {e}"
            if intento < self._reintentos:
                self._dormir(espera)
        raise RuntimeError(motivo)

    def bajar_dia(self, sim: Simbolo, dia: date) -> ResultadoDia:
        codigo = sim.fuentes.get("dukascopy")
        if not codigo:
            return ResultadoDia("error", None, f"{sim.simbolo}: no tiene código de dukascopy")
        inicio = datetime.combine(dia, hora_del_dia(0), tzinfo=timezone.utc)
        horas = [inicio + timedelta(hours=h) for h in range(24)]

        def tarea(hora: datetime):
            try:
                return self._bajar_hora(codigo, hora, sim.decimales), None
            except Exception as e:
                return None, f"{hora.strftime('%Y-%m-%dT%H:%MZ')}: {e}"

        with ThreadPoolExecutor(self._max_paralelo) as pool:
            resultados = list(pool.map(tarea, horas))
        for _, error in resultados:
            if error:
                return ResultadoDia("error", None, error)
        tablas = [t for t, _ in resultados if t.num_rows]
        if not tablas:
            return ResultadoDia("vacio", None, None)
        tabla = pa.concat_tables(tablas).sort_by("ts")
        return ResultadoDia("ok", tabla, None)
