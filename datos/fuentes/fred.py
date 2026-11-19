"""Fuente FRED / ALFRED: series macro con todas sus versiones y "cuándo se conoció" cada dato."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import (ESQUEMA_SERIE, Manifiesto, escribir, escribir_crudo, nombre_crudo,
                           ruta_relativa)
from datos.config import Config, ConfigInvalida
from datos.contexto_conf import cargar_contexto
from datos.fuentes.base import ClienteHttp, ClienteRequests
from datos.tiempos import (detectar_carga_en_bloque, estimar_publicacion, publicacion_alfred,
                           publicacion_serie_mercado)

URL_OBSERVACIONES = "https://api.stlouisfed.org/fred/series/observations"
TIMEOUT = 60.0
_VENTANA_ALFRED = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}

log = logging.getLogger(__name__)


def _observaciones(cuerpo: bytes) -> list[dict]:
    return json.loads(cuerpo).get("observations", [])


def comprobar_completa(cuerpo: bytes, serie_id: str) -> None:
    """Error si la respuesta dice tener más observaciones (`count`) de las que trae: está
    truncada (p. ej. por el límite de filas de la API) y no se puede guardar como si fuera toda."""
    datos = json.loads(cuerpo)
    total, recibidas = datos.get("count"), len(datos.get("observations", []))
    if total is not None and int(total) > recibidas:
        raise RuntimeError(f"Respuesta truncada de {serie_id}: dice {total} observaciones y "
                           f"trae {recibidas}; no se guarda nada")


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


def _mezclar(raiz: Path, nueva: pa.Table, fuente: str, sid: str, origen: dict) -> int:
    """Une con lo guardado por (periodo, ts_conocido): nunca quita filas, solo añade las nuevas.

    Si una fila guardada cambió en la respuesta se conserva la guardada y se avisa en el log.
    En ALFRED, `ts_hasta` se recalcula con todas las versiones (la siguiente de cada periodo).
    Escribe solo si hay algo nuevo (o si aún no existe el archivo). Devuelve las filas añadidas.
    """
    ruta = raiz / ruta_relativa("serie", fuente, sid, None)
    if not ruta.exists():
        escribir(raiz, nueva, "serie", fuente, sid, None, origen=origen)
        return nueva.num_rows
    previa = pq.read_table(ruta).select(ESQUEMA_SERIE.names).cast(ESQUEMA_SERIE).to_pylist()
    guardadas = {(f["periodo"], f["ts_conocido"]): f for f in previa}
    nuevas = []
    for f in nueva.to_pylist():
        g = guardadas.get((f["periodo"], f["ts_conocido"]))
        if g is None:
            nuevas.append(f)
        elif g["valor"] != f["valor"]:
            log.warning("%s: el valor de %s (conocido %s) cambió; se conserva lo guardado",
                        sid, f["periodo"], f["ts_conocido"])
    if not nuevas:
        return 0
    filas = sorted(previa + nuevas, key=lambda f: (f["periodo"], f["ts_conocido"]))
    if fuente == "alfred":
        for a, b in zip(filas, filas[1:]):
            a["ts_hasta"] = b["ts_conocido"] if a["periodo"] == b["periodo"] else None
        if filas:
            filas[-1]["ts_hasta"] = None
    escribir(raiz, _tabla(filas), "serie", fuente, sid, None, origen=origen)
    return len(nuevas)


def procesar_crudo(cfg: Config, cuerpo: bytes, meta: Manifiesto) -> int:
    """Normaliza un crudo guardado (`alfred_<ID>` o `fred_<ID>`) y lo une con lo guardado
    (nunca quita filas: una respuesta vacía no borra el historial). Una respuesta truncada
    (`count` mayor que lo recibido) da error sin escribir nada.

    Es el mismo camino que usa la descarga en vivo; `reprocesar` lo usa sin red.
    Devuelve las filas nuevas.
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
    comprobar_completa(cuerpo, sid)
    tabla = (normalizar_alfred if alfred else normalizar_fred)(cuerpo, conf)
    return _mezclar(Path(cfg.data_dir), tabla, fuente, sid,
                    {"url": _url(cfg, sid, alfred, False)})


def bajar_series(cfg: Config, http: ClienteHttp | None = None,
                 ids: list[str] | None = None) -> dict[str, int]:
    """Baja y guarda las series de contexto.json. Devuelve las filas nuevas por serie.

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
            comprobar_completa(cuerpo, sid)   # truncada: ni siquiera se guarda el crudo
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
