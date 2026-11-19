"""Orden `contexto probar-calendario`: compara APIs de calendario antes de pagar una.

Para cada fuente baja unos meses de prueba (por defecto enero de 2008 y enero de 2024), los
normaliza como carga histórica EN MEMORIA (no toca el calendario guardado) y escribe un informe
HTML con: primer año con datos, % con hora exacta, % con previsión, coincidencia de hora y de dato
real con ALFRED en eventos de EE. UU., divisas cubiertas y errores o límites encontrados.

Los crudos sí se guardan, con el prefijo `prueba_` (para poder revisarlos); `reprocesar` los salta.
El mapeo evento → serie de ALFRED y las tolerancias son PROVISIONALES (Task 12).
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from datos.almacen import escribir_crudo, ruta_relativa
from datos.config import Config
from datos.fuentes.base import ClienteHttp
from datos.fuentes.calendario_api import FUENTES_CALENDARIO, _meses, nombre_crudo_calendario
from datos.fuentes.forexfactory import NY

FUENTES_PRUEBA = ("fmp", "eodhd", "te")
DIVISAS_PRUEBA = ("USD", "EUR", "JPY")
TOLERANCIA_HORA = timedelta(minutes=90)   # la misma ventana que para emparejar FF con la API
SIN_CLAVE = "no probada (falta clave)"
NO_IMPLEMENTADA = "no implementada"

# Evento normalizado → (serie de ALFRED, cómo sacar el dato, tolerancia).
#   nivel:  el valor de la serie (p. ej. tasa de paro en %).
#   cambio: valor - valor del periodo anterior, en miles → personas (PAYEMS está en miles).
#   pct:    variación % respecto al periodo anterior.
# Tolerancia: diferencia máxima para decir que el dato coincide (redondeos de la fuente).
EVENTO_A_ALFRED = {
    "US_NFP": ("PAYEMS", "cambio", 1000.0),
    "US_DESEMPLEO": ("UNRATE", "nivel", 0.051),
    "US_IPC": ("CPIAUCSL", "pct", 0.051),
    "US_IPC_SUBYACENTE": ("CPILFESL", "pct", 0.051),
    "US_PCE": ("PCEPILFE", "pct", 0.051),
    "US_VENTAS_MINORISTAS": ("RSAFS", "pct", 0.051),
    "US_SOLICITUDES_DESEMPLEO": ("ICSA", "nivel", 1000.0),
}


@dataclass
class ResultadoPrueba:
    fuente: str
    estado: str                       # probada / no probada (falta clave) / no implementada
    filas: dict[str, int] = field(default_factory=dict)   # por mes probado (USD, EUR, JPY)
    primer_anio: int | None = None
    pct_hora_exacta: float | None = None
    pct_prevision: float | None = None
    alfred: dict | str | None = None  # conteos, o un texto si no se pudo comparar
    divisas: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


def _sin_claves(cfg: Config, texto: str) -> str:
    for clave in (cfg.fred_api_key, cfg.fmp_api_key, cfg.eodhd_api_key, cfg.te_api_key):
        if clave:
            texto = texto.replace(clave, "***")
    return texto


# --- comparación con ALFRED ------------------------------------------------

def _serie_alfred(raiz: Path, sid: str) -> pd.DataFrame | None:
    ruta = raiz / ruta_relativa("serie", "alfred", sid, None)
    return pq.read_table(ruta).to_pandas() if ruta.is_file() else None


def _dato_alfred(serie: pd.DataFrame, dia_ny: date, modo: str):
    """(ts_conocido, dato) de la versión de ALFRED publicada ese día, o None si no hay."""
    publicadas = serie[~serie["publicacion_estimada"]
                       & (serie["ts_conocido"].dt.tz_convert(NY).dt.date == dia_ny)]
    if publicadas.empty:
        return None
    fila = publicadas.sort_values("periodo").iloc[-1]   # el periodo nuevo de ese día
    t, valor = fila["ts_conocido"], fila["valor"]
    if modo == "nivel" or pd.isna(valor):
        return t, None if pd.isna(valor) else float(valor)
    anteriores = serie[serie["periodo"] < fila["periodo"]]
    if anteriores.empty:
        return t, None
    previo = anteriores[anteriores["periodo"] == anteriores["periodo"].max()]
    vigente = previo[(previo["ts_conocido"] <= t)
                     & (previo["ts_hasta"].isna() | (previo["ts_hasta"] > t))]
    if vigente.empty or pd.isna(vigente["valor"].iloc[-1]):
        return t, None
    antes = float(vigente["valor"].iloc[-1])
    if modo == "cambio":
        return t, (float(valor) - antes) * 1000.0
    return t, (float(valor) / antes - 1.0) * 100.0 if antes else None


def comparar_con_alfred(eventos: pd.DataFrame, raiz: Path) -> dict | str:
    """Cuenta en eventos de EE. UU. mapeados cuántos coinciden en hora y dato real con ALFRED."""
    us = eventos[(eventos["divisa"] == "USD") & eventos["evento"].isin(list(EVENTO_A_ALFRED))]
    series = {sid: _serie_alfred(raiz, sid) for sid, _, _ in EVENTO_A_ALFRED.values()}
    if all(s is None for s in series.values()):
        return ("ALFRED no está bajado: no se puede comparar (corre "
                "`python -m datos contexto bajar alfred` y repite la prueba).")
    r = {"mapeados": len(us), "comparables": 0, "hora_coincide": 0,
         "real_comparables": 0, "real_coincide": 0}
    for _, ev in us.iterrows():
        sid, modo, tolerancia = EVENTO_A_ALFRED[ev["evento"]]
        if series[sid] is None:
            continue
        dato = _dato_alfred(series[sid], ev["ts_evento"].tz_convert(NY).date(), modo)
        if dato is None:
            continue
        t, valor = dato
        r["comparables"] += 1
        if ev["hora_exacta"] and abs(ev["ts_evento"] - t) <= TOLERANCIA_HORA:
            r["hora_coincide"] += 1
        if valor is not None and not pd.isna(ev["real"]):
            r["real_comparables"] += 1
            if abs(float(ev["real"]) - valor) <= tolerancia:
                r["real_coincide"] += 1
    return r


# --- prueba de una fuente --------------------------------------------------

def _probar_fuente(cfg: Config, nombre: str, rangos: list[tuple[date, date]],
                   http: ClienteHttp | None, ts: datetime) -> ResultadoPrueba:
    if not getattr(cfg, f"{nombre}_api_key", ""):
        return ResultadoPrueba(nombre, SIN_CLAVE)
    if nombre not in FUENTES_CALENDARIO:
        return ResultadoPrueba(nombre, NO_IMPLEMENTADA)
    src = FUENTES_CALENDARIO[nombre](cfg, http)
    raiz = Path(cfg.data_dir)
    res = ResultadoPrueba(nombre, "probada")
    tablas = []
    for desde, hasta in rangos:
        for ini, fin in _meses(desde, hasta):
            mes = f"{ini:%Y-%m}"
            res.filas[mes] = 0
            try:
                paginas = src.paginas_eventos(ini, fin)
            except Exception as e:  # noqa: BLE001 - el error es parte del informe
                res.errores.append(f"{mes}: {_sin_claves(cfg, str(e))}")
                continue
            for i, cuerpo in enumerate(paginas):
                escribir_crudo(raiz, cuerpo, nombre,
                               "prueba_" + nombre_crudo_calendario(nombre, mes, i, True), ts,
                               "json")
                try:
                    t = src.normalizar_eventos(cuerpo, ts, True)
                except Exception as e:  # noqa: BLE001 - respuesta con otra forma
                    res.errores.append(f"{mes}: respuesta ilegible ({type(e).__name__}: "
                                       f"{_sin_claves(cfg, str(e))})")
                    continue
                t = t.filter(pc.is_in(t["divisa"], pa.array(DIVISAS_PRUEBA)))
                res.filas[mes] += t.num_rows
                tablas.append(t)
    df = pa.concat_tables(tablas).to_pandas() if tablas else pd.DataFrame()
    if not df.empty:
        res.primer_anio = int(df["ts_evento"].dt.year.min())
        res.pct_hora_exacta = 100.0 * df["hora_exacta"].mean()
        res.pct_prevision = 100.0 * df["prevision"].notna().mean()
        res.divisas = sorted(df["divisa"].unique())
        res.alfred = comparar_con_alfred(df, raiz)
    else:
        res.alfred = "Sin eventos: nada que comparar."
    return res


# --- informe ---------------------------------------------------------------

def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x:.0f} %"


def _parte(n: int, total: int) -> str:
    return "n/d" if not total else f"{n} de {total} ({100.0 * n / total:.0f} %)"


def _celdas(r: ResultadoPrueba) -> list[str]:
    if r.estado != "probada":
        return [r.fuente, r.estado] + [""] * (len(COLUMNAS) - 2)
    if isinstance(r.alfred, dict):
        a = r.alfred
        hora = _parte(a["hora_coincide"], a["comparables"])
        real = _parte(a["real_coincide"], a["real_comparables"])
        hora += f" (eventos mapeados: {a['mapeados']})"
    else:
        hora = real = r.alfred or ""
    faltan = [d for d in DIVISAS_PRUEBA if d not in r.divisas]
    divisas = ", ".join(r.divisas) or "ninguna"
    if faltan:
        divisas += f" (faltan: {', '.join(faltan)})"
    filas = ", ".join(f"{mes}: {n}" for mes, n in r.filas.items())
    return [r.fuente, r.estado, filas, str(r.primer_anio or "sin datos"), _pct(r.pct_hora_exacta),
            _pct(r.pct_prevision), hora, real, divisas, "; ".join(r.errores) or "ninguno", ""]


COLUMNAS = ["Fuente", "Estado", "Filas por mes (USD, EUR, JPY)", "Primer año con datos",
            "% con hora exacta", "% con previsión", "Coincide la hora con ALFRED",
            "Coincide el dato real con ALFRED", "Divisas cubiertas", "Errores o límites",
            "Precio y límites del plan (anotar a mano)"]


def escribir_informe(cfg: Config, resultados: dict[str, ResultadoPrueba],
                     rangos: list[tuple[date, date]], hoy: date) -> Path:
    """`_informes/prueba-calendario-AAAA-MM-DD.html` (sobrescribe el del día). Sin claves."""
    e = html.escape
    probados = ", ".join(f"{d.isoformat()} a {h.isoformat()}" for d, h in rangos)
    filas = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in _celdas(r)) + "</tr>"
                    for r in resultados.values())
    texto = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>Prueba de APIs de calendario {hoy.isoformat()}</title>
<style>body{{font-family:sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #999;padding:4px 8px;vertical-align:top}}</style></head><body>
<h1>Prueba de APIs de calendario ({e(hoy.isoformat())})</h1>
<p>Meses probados: {e(probados)}. Divisas miradas: {e(", ".join(DIVISAS_PRUEBA))}.
Todo se normalizó como carga histórica y no se guardó en el calendario.</p>
<p>Coincidencia con ALFRED: solo eventos de EE. UU. que tienen serie asignada. La hora coincide
si la diferencia es de {int(TOLERANCIA_HORA.total_seconds() // 60)} minutos o menos; el dato real
se compara con el de la versión de ALFRED publicada ese día (tolerancias provisionales).</p>
<table><tr>{"".join(f"<th>{e(c)}</th>" for c in COLUMNAS)}</tr>{filas}</table>
</body></html>
"""
    destino = Path(cfg.data_dir) / "_informes" / f"prueba-calendario-{hoy.isoformat()}.html"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_sin_claves(cfg, texto), encoding="utf-8")
    return destino


def probar_calendario(cfg: Config, fuentes: list[str], rangos: list[tuple[date, date]],
                      http: ClienteHttp | None = None,
                      hoy: date | None = None) -> tuple[Path, dict[str, ResultadoPrueba]]:
    """Prueba cada fuente y escribe el informe. Devuelve (ruta del informe, resultados)."""
    ts = datetime.now(timezone.utc)
    hoy = hoy or ts.date()
    resultados = {f: _probar_fuente(cfg, f, rangos, http, ts) for f in fuentes}
    return escribir_informe(cfg, resultados, rangos, hoy), resultados
