"""Fuente FRED / ALFRED: series macro con todas sus versiones y "cuándo se conoció" cada dato."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import pyarrow as pa

from datos.almacen import ESQUEMA_SERIE, Manifiesto, escribir, escribir_crudo, nombre_crudo
from datos.config import Config, ConfigInvalida
from datos.contexto_conf import cargar_contexto
from datos.fuentes.base import ClienteHttp, ClienteRequests
from datos.tiempos import (detectar_carga_en_bloque, estimar_publicacion, publicacion_alfred,
                           publicacion_serie_mercado)

URL_OBSERVACIONES = "https://api.stlouisfed.org/fred/series/observations"
TIMEOUT = 60.0
_VENTANA_ALFRED = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}


def _observaciones(cuerpo: bytes) -> list[dict]:
    return json.loads(cuerpo).get("observations", [])


def _valor(texto: str) -> float | None:
    return None if texto in (".", "", None) else float(texto)


def _tabla(filas: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(filas, schema=ESQUEMA_SERIE)


def normalizar_alfred(cuerpo: bytes, serie_conf: dict) -> pa.Table:
    """Una fila por (periodo, versión). `ts_hasta` es cuando llega la versión siguiente."""
    obs = _observaciones(cuerpo)
    if not obs:
        return _tabla([])
    df = pd.DataFrame({
        "periodo": [o["date"] for o in obs],
        "realtime_start": [o["realtime_start"] for o in obs],
        "valor": [_valor(o["value"]) for o in obs],
    }).sort_values(["periodo", "realtime_start"]).reset_index(drop=True)
    en_bloque = detectar_carga_en_bloque(df)
    filas = []
    for i, r in df.iterrows():
        periodo = date.fromisoformat(r.periodo)
        if en_bloque[i]:
            conocido = estimar_publicacion(periodo, serie_conf)
        else:
            conocido = publicacion_alfred(date.fromisoformat(r.realtime_start), serie_conf)
        filas.append({"serie": serie_conf["id"], "periodo": periodo,
                      "valor": None if pd.isna(r.valor) else float(r.valor),
                      "ts_conocido": conocido, "ts_hasta": None,
                      "fuente": "alfred", "publicacion_estimada": bool(en_bloque[i])})
    # ts_hasta: el ts_conocido de la versión siguiente del mismo periodo.
    for a, b in zip(filas, filas[1:]):
        if a["periodo"] == b["periodo"]:
            a["ts_hasta"] = b["ts_conocido"]
    return _tabla(filas)


def normalizar_fred(cuerpo: bytes, serie_conf: dict) -> pa.Table:
    """Serie de mercado sin versiones: se conoce según su regla de retraso."""
    filas = []
    for o in _observaciones(cuerpo):
        dia = date.fromisoformat(o["date"])
        filas.append({"serie": serie_conf["id"], "periodo": dia, "valor": _valor(o["value"]),
                      "ts_conocido": publicacion_serie_mercado(dia, serie_conf.get("retraso")),
                      "ts_hasta": None, "fuente": "fred", "publicacion_estimada": False})
    return _tabla(filas)


def _url(cfg: Config, serie_id: str, alfred: bool, con_clave: bool) -> str:
    params = {"series_id": serie_id, "file_type": "json"}
    if alfred:
        params.update(_VENTANA_ALFRED)
    if con_clave:
        params["api_key"] = cfg.fred_api_key
    return f"{URL_OBSERVACIONES}?{urlencode(params)}"


def procesar_crudo(cfg: Config, cuerpo: bytes, meta: Manifiesto) -> int:
    """Normaliza un crudo guardado (`alfred_<ID>` o `fred_<ID>`) y escribe la serie entera.

    Es el mismo camino que usa la descarga en vivo; `reprocesar` lo usa sin red.
    Devuelve las filas escritas.
    """
    fuente, _, sid = nombre_crudo(meta).partition("_")
    if fuente not in ("alfred", "fred") or not sid:
        raise ValueError(f"Crudo de FRED con nombre desconocido: {meta.ruta}")
    alfred = fuente == "alfred"
    ctx = cargar_contexto()
    conf = next((s for s in ctx["series_alfred" if alfred else "series_fred"] if s["id"] == sid),
                None)
    if conf is None:
        raise ValueError(f"La serie {sid} ya no está en contexto.json ({fuente})")
    tabla = (normalizar_alfred if alfred else normalizar_fred)(cuerpo, conf)
    escribir(Path(cfg.data_dir), tabla, "serie", fuente, sid, None,
             origen={"url": _url(cfg, sid, alfred, False)})
    return tabla.num_rows


def bajar_series(cfg: Config, http: ClienteHttp | None = None,
                 ids: list[str] | None = None) -> dict[str, int]:
    """Baja y guarda las series de contexto.json. Devuelve las filas por serie.

    Si alguna serie falla, las demás se guardan igual y al final se lanza RuntimeError
    con la lista de las que fallaron.
    """
    if not cfg.fred_api_key:
        raise ConfigInvalida("Falta FRED_API_KEY en el .env (clave gratuita de FRED).")
    ctx = cargar_contexto()
    http = http or ClienteRequests()
    series = [(s, True) for s in ctx["series_alfred"]] + [(s, False) for s in ctx["series_fred"]]
    if ids is not None:
        series = [(s, a) for s, a in series if s["id"] in ids]
    filas: dict[str, int] = {}
    fallos: list[str] = []
    for conf, alfred in series:
        sid = conf["id"]
        try:
            estado, cuerpo = http.get(_url(cfg, sid, alfred, True), TIMEOUT)
            if estado != 200:
                raise RuntimeError(f"HTTP {estado}")
            fuente = "alfred" if alfred else "fred"
            # El cuerpo de la respuesta no lleva la clave; la URL guardada tampoco.
            m = escribir_crudo(Path(cfg.data_dir), cuerpo, fuente, f"{fuente}_{sid}",
                               datetime.now(timezone.utc), "json")
            filas[sid] = procesar_crudo(cfg, cuerpo, m)
        except Exception as e:  # noqa: BLE001 - seguimos con las demás series
            fallos.append(f"{sid} ({str(e).replace(cfg.fred_api_key, '***')})")
    if fallos:
        raise RuntimeError("Fallaron estas series de FRED: " + ", ".join(fallos))
    return filas
