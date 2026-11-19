"""Verificación de calidad de los datos de contexto (calendario, series, COT).

Devuelve `Problema` (los mismos de `calidad.py`) y las anota en el catálogo igual que
`verificar`: solo cuentan como nuevos los que el catálogo no tenía.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from datos.almacen import listar_crudos, momento_crudo, nombre_crudo
from datos.calidad import Problema
from datos.config import Config
from datos.consulta import (_FUENTE_FF, _clave_evento, _leer_contexto, _prevision_ff,
                            _ultima_version)
from datos.horarios import es_habil_eeuu
from datos.prueba_calendario import EVENTO_A_ALFRED, _dato_alfred, _serie_alfred

TOLERANCIA_REAL = 1e-6      # relativa, para datos "nivel" (el valor tal cual de la serie)
FACTOR_HUECO_FF = 1.5       # hueco = más de 1,5 veces el ritmo de fotos de FF
_NOMBRES_FF = ("ff_thisweek", "ff_nextweek")
_FUENTES_API = ("fmp", "eodhd", "te")


def _py(ts) -> datetime:
    return pd.Timestamp(ts).to_pydatetime()


def _dia_utc(dia: date) -> datetime:
    return datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)


def _inicio(desde: date | None) -> pd.Timestamp | None:
    return pd.Timestamp(_dia_utc(desde)) if desde else None


# --- calendario --------------------------------------------------------------

def chequear_fotos_ff(cfg: Config, desde: date | None = None) -> list[Problema]:
    """Una foto de FF cada `ff_cada_horas`: un hueco mayor a 1,5 veces eso es un aviso."""
    momentos = sorted({momento_crudo(m) for m in listar_crudos(cfg.data_dir, _FUENTE_FF)
                       if nombre_crudo(m) in _NOMBRES_FF})
    if desde:
        momentos = [t for t in momentos if t >= _dia_utc(desde)]
    limite = timedelta(hours=cfg.ff_cada_horas * FACTOR_HUECO_FF)
    return [Problema("ff_foto_faltante", "ff", _FUENTE_FF, a, b, "aviso",
                     {"horas": round((b - a).total_seconds() / 3600, 2)})
            for a, b in zip(momentos, momentos[1:]) if b - a > limite]


def chequear_reprogramados(cal: pd.DataFrame) -> list[Problema]:
    """Eventos cuyo `ts_evento` cambió entre versiones (misma clave de evento)."""
    if cal.empty:
        return []
    cal = cal.assign(_clave=_clave_evento(cal))
    salida = []
    for clave, g in cal.groupby("_clave"):
        if g["ts_evento"].nunique() < 2:
            continue
        g = g.sort_values("ts_version", kind="stable")
        ts = g["ts_evento"].tolist()
        for i in range(1, len(ts)):
            if ts[i] != ts[i - 1]:
                f = g.iloc[i]
                salida.append(Problema(
                    "evento_reprogramado", str(f["evento"]), str(f["fuente"]),
                    _py(ts[i - 1]), _py(ts[i]), "info",
                    {"divisa": f["divisa"], "version": _py(f["ts_version"]).isoformat()}))
    return salida


def chequear_sin_mapear(cal: pd.DataFrame) -> list[Problema]:
    """Eventos que quedaron con categoría 'otros' (no están en eventos.json)."""
    if cal.empty:
        return []
    otros = cal[cal["categoria"] == "otros"]
    salida = []
    for (fuente, nombre), g in otros.groupby(["fuente", "evento"]):
        salida.append(Problema("evento_sin_mapear", str(nombre), str(fuente),
                               _py(g["ts_evento"].min()), _py(g["ts_evento"].max()), "info",
                               {"filas": int(len(g))}))
    return salida


def _real_api_vs_alfred(cfg: Config, cal: pd.DataFrame) -> list[Problema]:
    """Primer dato real de la API de cada evento mapeado frente a la primera publicación
    de ALFRED. Datos "nivel": tolerancia relativa 1e-6. Los derivados (cambio, %) se
    calculan a partir de la serie y la API redondea, así que usan la tolerancia absoluta
    del mapa `EVENTO_A_ALFRED`."""
    api = cal[cal["fuente"].isin(_FUENTES_API) & cal["real"].notna()
              & (cal["divisa"] == "USD") & cal["evento"].isin(list(EVENTO_A_ALFRED))]
    if api.empty:
        return []
    series = {}
    salida = []
    primeras = api.sort_values("ts_version", kind="stable").assign(_clave=_clave_evento(api))
    primeras = primeras.drop_duplicates("_clave", keep="first")
    for _, ev in primeras.iterrows():
        sid, modo, tol_abs = EVENTO_A_ALFRED[ev["evento"]]
        if sid not in series:
            series[sid] = _serie_alfred(cfg.data_dir, sid)
        if series[sid] is None:
            continue
        dia_ny = ev["ts_evento"].tz_convert("America/New_York").date()
        dato = _dato_alfred(series[sid], dia_ny, modo)
        if dato is None or dato[1] is None:
            continue
        alfred, real = dato[1], float(ev["real"])
        dif = abs(real - alfred)
        if modo == "nivel":
            malo = dif > TOLERANCIA_REAL * max(abs(alfred), abs(real))
        else:
            malo = dif > tol_abs
        if malo:
            salida.append(Problema(
                "real_api_vs_alfred", str(ev["evento"]), str(ev["fuente"]),
                _py(ev["ts_evento"]), _py(ev["ts_evento"]), "grave",
                {"serie": sid, "real_api": real, "alfred": float(alfred)}))
    return salida


def _prevision_api_vs_ff(cal: pd.DataFrame) -> list[Problema]:
    """Por evento: mediana y p90 de |previsión API − previsión FF| (emparejados a ±90 min)."""
    api = cal[cal["fuente"].isin(_FUENTES_API)]
    ff = cal[cal["fuente"] == _FUENTE_FF]
    if api.empty or ff.empty:
        return []
    api = _ultima_version(api.assign(_clave=_clave_evento(api)), ["_clave"],
                          "ts_version").reset_index(drop=True)
    api = api[api["prevision"].notna()].reset_index(drop=True)
    if api.empty:
        return []
    api["_ff"] = _prevision_ff(api, ff, None)
    api = api[api["_ff"].notna()]
    salida = []
    for (fuente, nombre), g in api.groupby(["fuente", "evento"]):
        dif = (g["prevision"] - g["_ff"]).abs()
        salida.append(Problema(
            "prevision_api_vs_ff", str(nombre), str(fuente),
            _py(g["ts_evento"].min()), _py(g["ts_evento"].max()), "info",
            {"emparejados": int(len(g)), "mediana": float(dif.median()),
             "p90": float(dif.quantile(0.9))}))
    return salida


# --- series, ALFRED y COT -----------------------------------------------------

def chequear_alfred_fechas(serie: pd.DataFrame, sid: str) -> list[Problema]:
    """Una versión de ALFRED con `ts_hasta` <= `ts_conocido` es incoherente (grave)."""
    if serie.empty:
        return []
    malas = serie[serie["ts_hasta"].notna() & (serie["ts_hasta"] <= serie["ts_conocido"])]
    if malas.empty:
        return []
    return [Problema("alfred_fechas", sid, "alfred", _py(malas["ts_conocido"].min()),
                     _py(malas["ts_hasta"].max()), "grave", {"versiones": int(len(malas))})]


def chequear_huecos_serie(serie: pd.DataFrame, sid: str, fuente: str = "fred") -> list[Problema]:
    """Días hábiles de EE. UU. sin dato entre el primero y el último, juntos en rangos."""
    if serie.empty:
        return []
    tiene = {pd.Timestamp(p).date() for p in serie["periodo"].dropna()}
    d, fin = min(tiene), max(tiene)
    faltan: list[date] = []
    while d <= fin:
        if d not in tiene and es_habil_eeuu(d):
            faltan.append(d)
        d += timedelta(days=1)
    tramos: list[list[date]] = []
    for d in faltan:
        # contiguo si entre el último del tramo y d solo hay fines de semana o festivos
        if tramos and all(not es_habil_eeuu(tramos[-1][-1] + timedelta(days=k))
                          for k in range(1, (d - tramos[-1][-1]).days)):
            tramos[-1].append(d)
        else:
            tramos.append([d])
    return [Problema("serie_hueco", sid, fuente, _dia_utc(t[0]), _dia_utc(t[-1]), "aviso",
                     {"dias_habiles": len(t)}) for t in tramos]


def chequear_semanas_cot(cot: pd.DataFrame) -> list[Problema]:
    """Martes de informe que faltan entre el primero y el último de cada mercado e informe."""
    if cot.empty:
        return []
    salida = []
    for (mercado, informe), g in cot.groupby(["mercado", "informe"]):
        fechas = {pd.Timestamp(f).date() for f in g["fecha_informe"].dropna()}
        d, fin = min(fechas), max(fechas)
        d += timedelta(days=(1 - d.weekday()) % 7)   # primer martes desde el primero
        while d <= fin:
            if d not in fechas:
                salida.append(Problema("cot_semana_faltante", str(mercado), "cftc",
                                       _dia_utc(d), _dia_utc(d), "aviso", {"informe": informe}))
            d += timedelta(days=7)
    return salida


# --- verificación completa ----------------------------------------------------

def verificar_contexto(cfg: Config, catalogo, desde: date | None = None) -> list[Problema]:
    """Revisa calendario, series, ALFRED y COT. Devuelve los problemas NUEVOS (los que el
    catálogo no tenía; sin catálogo, todos)."""
    nuevos: list[Problema] = []

    def _anotar(problemas: list[Problema]) -> None:
        for p in problemas:
            if catalogo is None or catalogo.anotar_problema(
                    p.tipo, p.simbolo, p.fuente, p.desde, p.hasta, p.gravedad, p.detalle):
                nuevos.append(p)

    corte = _inicio(desde)
    _anotar(chequear_fotos_ff(cfg, desde))

    cal = _leer_contexto(cfg, "calendario")
    if corte is not None and not cal.empty:
        cal = cal[cal["ts_evento"] >= corte]
    _anotar(chequear_reprogramados(cal))
    _anotar(chequear_sin_mapear(cal))
    _anotar(_real_api_vs_alfred(cfg, cal))
    _anotar(_prevision_api_vs_ff(cal))

    series = _leer_contexto(cfg, "serie")
    for (fuente, sid), g in series.groupby(["fuente", "serie"]):
        if fuente == "alfred":
            _anotar(chequear_alfred_fechas(g, sid))
        elif fuente == "fred":
            if corte is not None:
                g = g[pd.to_datetime(g["periodo"]).dt.tz_localize("UTC") >= corte]
            _anotar(chequear_huecos_serie(g, sid, fuente))

    cot = _leer_contexto(cfg, "cot")
    if corte is not None and not cot.empty:
        cot = cot[pd.to_datetime(cot["fecha_informe"]).dt.tz_localize("UTC") >= corte]
    _anotar(chequear_semanas_cot(cot))
    return nuevos
