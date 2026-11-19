"""Fuente Forex Factory: foto del calendario semanal (JSON público) con versiones de solo añadir."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import (ESQUEMA_CALENDARIO, Manifiesto, escribir, escribir_crudo, momento_crudo,
                           nombre_crudo, ruta_relativa)
from datos.config import Config
from datos.contexto_conf import cargar_contexto, cargar_eventos, normalizar_evento
from datos.fuentes.base import ClienteHttp, ClienteRequests
from datos.versiones import versionar

URLS_FF = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
_NOMBRES_CRUDO = ("ff_thisweek", "ff_nextweek")
TIMEOUT = 30.0
NY = ZoneInfo("America/New_York")
_IMPACTO = {"high": "alto", "medium": "medio", "low": "bajo"}
_ORDEN_IMPACTO = {"bajo": 0, "medio": 1, "alto": 2}
_MULT = {"": Decimal(1), "%": Decimal(1), "K": Decimal(10) ** 3, "M": Decimal(10) ** 6,
         "B": Decimal(10) ** 9, "T": Decimal(10) ** 12}
_NUMERO = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*([%KMBTkmbt]?)$")
_CLAVE = ["evento", "divisa", "dia_ny"]
_CAMPOS = ["prevision", "real", "anterior", "ts_evento", "impacto", "hora_exacta"]


def _numero(texto) -> tuple[float | None, str]:
    """('250K' → (250000.0, 'K')); vacío o ilegible → (None, '')."""
    m = _NUMERO.match(str(texto or "").strip())
    if not m:
        return None, ""
    unidad = m.group(2).upper()
    try:
        return float(Decimal(m.group(1)) * _MULT[unidad]), unidad
    except InvalidOperation:
        return None, ""


def _momento(texto: str) -> tuple[datetime, bool]:
    """(ts_evento en UTC, hora_exacta). Sin hora o a medianoche de NY → día sin hora exacta.

    Heurística: el JSON no distingue "All Day"/"Tentative"; se supone que 00:00 NY no es una hora real.
    """
    dt = datetime.fromisoformat(texto)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    local = dt.astimezone(NY)
    if len(texto) <= 10 or (local.hour, local.minute, local.second) == (0, 0, 0):
        medianoche = datetime(local.year, local.month, local.day, tzinfo=NY)
        return medianoche.astimezone(timezone.utc), False
    return dt.astimezone(timezone.utc), True


def normalizar_ff(cuerpo: bytes, ts_foto: datetime, mapa_eventos: dict,
                  divisas: list[str], impacto_min: str = "medio") -> pa.Table:
    """Convierte el JSON de FF al esquema de calendario. Filtra por divisa e impacto mínimo."""
    ts_foto = ts_foto.astimezone(timezone.utc)
    filas = []
    for it in json.loads(cuerpo):
        divisa = it.get("country")
        if divisa not in divisas:
            continue
        festivo = str(it.get("impact", "")).lower() == "holiday"
        impacto = _IMPACTO.get(str(it.get("impact", "")).lower(), "bajo")
        # Los festivos se conservan siempre (mercados cerrados, poca liquidez).
        if not festivo and _ORDEN_IMPACTO[impacto] < _ORDEN_IMPACTO[impacto_min]:
            continue
        ts_evento, exacta = _momento(it["date"])
        prevision, u1 = _numero(it.get("forecast"))
        anterior, u2 = _numero(it.get("previous"))
        real, u3 = _numero(it.get("actual"))
        evento, categoria = normalizar_evento("forexfactory", it["title"], mapa_eventos)
        if festivo:
            categoria = "festivo"
        filas.append({
            "ts_evento": ts_evento, "ts_anunciado": ts_foto, "ts_conocido": ts_evento,
            "ts_version": ts_foto, "ts_foto": ts_foto, "fuente": "forexfactory",
            "id_fuente": None, "pais": None, "divisa": divisa, "evento": evento,
            "evento_fuente": it["title"], "categoria": categoria, "impacto": impacto,
            "unidad": u1 or u2 or u3, "programado": True, "hora_exacta": exacta,
            # Una foto tomada después del evento no trae una previsión previa al dato.
            "prevision_es_pit": ts_foto < ts_evento, "real": real, "prevision": prevision,
            "anterior": anterior, "anterior_revisado": None,
        })
    return pa.Table.from_pylist(filas, schema=ESQUEMA_CALENDARIO)


def _con_dia(df: pd.DataFrame) -> pd.DataFrame:
    """Añade `dia_ny` (columna de ayuda solo para versionar; no se guarda)."""
    df = df.copy()
    df["dia_ny"] = df["ts_evento"].dt.tz_convert(NY).dt.date
    return df


def _mezclar(raiz: Path, nuevas: pd.DataFrame, divisa: str, anio: int,
             ts_foto: datetime) -> int:
    """Mezcla las filas de una divisa y año con lo ya guardado. Devuelve filas nuevas."""
    rel = ruta_relativa("calendario", "forexfactory", divisa, datetime(anio, 1, 1).date())
    ruta = raiz / rel
    if ruta.exists():
        previas = pq.read_table(ruta).to_pandas()
    else:
        previas = ESQUEMA_CALENDARIO.empty_table().to_pandas()
    previas_d = _con_dia(previas)
    nuevas = _con_dia(nuevas).drop_duplicates(subset=_CLAVE, keep="last")
    if not previas_d.empty:   # ts_anunciado = el de la primera versión del evento
        primera = (previas_d.sort_values("ts_version", kind="stable")
                   .groupby(_CLAVE)["ts_anunciado"].first())
        llaves = list(zip(*(nuevas[c] for c in _CLAVE)))
        nuevas["ts_anunciado"] = [primera.get(k, a) for k, a in zip(llaves, nuevas["ts_anunciado"])]
    res = versionar(previas_d, nuevas, _CLAVE, _CAMPOS, ts_foto).drop(columns="dia_ny")
    agregadas = len(res) - len(previas)
    if agregadas:
        tabla = pa.Table.from_pandas(res[ESQUEMA_CALENDARIO.names], schema=ESQUEMA_CALENDARIO,
                                     preserve_index=False)
        escribir(raiz, tabla, "calendario", "forexfactory", divisa, datetime(anio, 1, 1).date())
    return agregadas


def procesar_crudo(cfg: Config, cuerpo: bytes, meta: Manifiesto) -> int:
    """Normaliza una foto guardada de FF y la mezcla en el calendario. Devuelve filas nuevas.

    La hora de la foto es la del crudo. Es el mismo camino que usa la descarga en vivo;
    `reprocesar` lo usa sin red.
    """
    if nombre_crudo(meta) not in _NOMBRES_CRUDO:
        raise ValueError(f"Crudo de Forex Factory con nombre desconocido: {meta.ruta}")
    ts_foto = momento_crudo(meta)
    ctx = cargar_contexto()
    df = normalizar_ff(cuerpo, ts_foto, cargar_eventos(), ctx["divisas_calendario"],
                       ctx["impacto_min"]).to_pandas()
    if df.empty:
        return 0
    df["_anio"] = df["ts_evento"].dt.year
    nuevas = 0
    for (divisa, anio), grupo in df.groupby(["divisa", "_anio"]):
        nuevas += _mezclar(Path(cfg.data_dir), grupo.drop(columns="_anio"), divisa, int(anio),
                           ts_foto)
    return nuevas


def guardar_foto(cfg: Config, http: ClienteHttp | None = None,
                 ahora: datetime | None = None) -> int:
    """Baja las dos semanas de FF, guarda el crudo y mezcla en el calendario. Devuelve filas nuevas.

    Si una URL falla, se procesa la otra y al final se lanza RuntimeError con la URL que falló.
    """
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    http = http or ClienteRequests()
    raiz = Path(cfg.data_dir)
    nuevas_filas, fallos = 0, []
    for url, nombre in zip(URLS_FF, _NOMBRES_CRUDO):
        try:
            estado, cuerpo = http.get(url, TIMEOUT)
            if estado != 200:
                raise RuntimeError(f"HTTP {estado}")
            m = escribir_crudo(raiz, cuerpo, "forexfactory", nombre, ahora, "json")
            nuevas_filas += procesar_crudo(cfg, cuerpo, m)
        except Exception as e:  # noqa: BLE001 - seguimos con la otra URL
            fallos.append(f"{url} ({e})")
    if fallos:
        raise RuntimeError("Falló la descarga de Forex Factory: " + "; ".join(fallos))
    return nuevas_filas
