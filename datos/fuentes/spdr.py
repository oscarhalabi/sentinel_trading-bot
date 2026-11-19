"""Tenencias del ETF GLD (SPDR Gold Shares) desde su CSV histórico.

El formato del CSV es PROVISIONAL hasta la Task 12 (se ajusta con el archivo real). Todo el mapeo
de columnas está en `COLUMNAS`.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import ESQUEMA_ETF, escribir, escribir_crudo, ruta_relativa
from datos.config import Config
from datos.contexto_conf import cargar_contexto
from datos.fuentes.base import ClienteHttp, ClienteRequests
from datos.fuentes.forexfactory import TIMEOUT
from datos.tiempos import publicacion_etf

log = logging.getLogger(__name__)

URL_GLD = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"  # provisional
# Nombre esperado de cada columna (ya sin espacios sobrantes y en minúsculas). Provisional.
COLUMNAS = {"fecha": "date", "toneladas": "tonnes", "valor_usd": "total value us$"}
_FORMATOS_FECHA = ("%d-%b-%Y", "%Y-%m-%d")


def _numero(texto: str | None) -> float | None:
    t = (texto or "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _fecha(texto: str | None) -> date | None:
    t = (texto or "").strip()
    for f in _FORMATOS_FECHA:
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None


def normalizar_gld(csv_bytes: bytes, etf_conf: dict) -> pa.Table:
    """Esquema `etf`. Las filas sin fecha o sin toneladas (p. ej. HOLIDAY) se saltan y se cuentan."""
    texto = csv_bytes.decode("utf-8-sig")
    lector = csv.DictReader(io.StringIO(texto))
    por_nombre = {(c or "").strip().lower(): c for c in (lector.fieldnames or [])}
    for clave, esperado in COLUMNAS.items():
        if esperado not in por_nombre:
            raise ValueError(f"GLD: falta la columna {esperado!r} ({clave}) en el CSV de SPDR; "
                             f"el formato pudo haber cambiado. Columnas: {list(por_nombre)}")
    filas, saltadas = [], 0
    for r in lector:
        dia = _fecha(r.get(por_nombre[COLUMNAS["fecha"]]))
        ton = _numero(r.get(por_nombre[COLUMNAS["toneladas"]]))
        if dia is None or ton is None:
            saltadas += 1
            continue
        filas.append({"etf": "GLD", "fecha": dia, "toneladas": ton,
                      "valor_usd": _numero(r.get(por_nombre[COLUMNAS["valor_usd"]])),
                      "ts_conocido": publicacion_etf(dia, etf_conf)})
    if saltadas:
        log.warning("GLD: %d filas sin fecha o sin toneladas se saltaron", saltadas)
    return pa.Table.from_pylist(filas, schema=ESQUEMA_ETF)


def _mezclar(raiz: Path, nueva: pa.Table) -> int:
    """Une con lo guardado por `fecha`: nunca borra; añade las fechas nuevas.

    Si una fecha guardada cambió en el CSV se conserva la guardada y se avisa en el log.
    Solo escribe si hay algo nuevo. Devuelve las filas añadidas.
    """
    ruta = raiz / ruta_relativa("etf", "spdr", "GLD", None)
    previa = pq.read_table(ruta) if ruta.exists() else None
    guardadas = {f["fecha"]: f for f in previa.to_pylist()} if previa is not None else {}
    nuevas = []
    for f in nueva.to_pylist():
        g = guardadas.get(f["fecha"])
        if g is None:
            nuevas.append(f)
        elif (g["toneladas"], g["valor_usd"]) != (f["toneladas"], f["valor_usd"]):
            log.warning("GLD: el valor del %s cambió en el CSV; se conserva lo guardado", f["fecha"])
    if not nuevas:
        return 0
    union = pa.Table.from_pylist(nuevas, schema=ESQUEMA_ETF)
    if previa is not None:
        union = pa.concat_tables([previa, union])
    escribir(raiz, union, "etf", "spdr", "GLD", None)
    return len(nuevas)


def bajar_gld(cfg: Config, http: ClienteHttp | None = None) -> int:
    """Baja el CSV, guarda el crudo y mezcla (solo añadir). Devuelve las filas nuevas."""
    http = http or ClienteRequests()
    try:
        estado, cuerpo = http.get(URL_GLD, TIMEOUT)
    except Exception as e:  # noqa: BLE001 - se re-lanza sin detalles de la URL
        raise RuntimeError(f"spdr: error de red al bajar GLD: {type(e).__name__}") from None
    if estado != 200:
        raise RuntimeError(f"spdr: HTTP {estado} al bajar GLD")
    raiz = Path(cfg.data_dir)
    ts = datetime.now(timezone.utc)
    escribir_crudo(raiz, cuerpo, "spdr", "spdr_gld", ts, "csv")
    return _mezclar(raiz, normalizar_gld(cuerpo, cargar_contexto()["etf"]["GLD"]))
