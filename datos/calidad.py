"""Verificación de calidad de los ticks guardados y el informe HTML."""
from __future__ import annotations

import html
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from datos.almacen import listar_manifiestos
from datos.config import Config
from datos.consulta import _leer_ticks_sin_registro
from datos.horarios import abierto

Tipo = Literal["coherencia", "hueco", "conteo", "salto", "posible_split"]
Gravedad = Literal["info", "aviso", "grave"]

HORAS_HUECO_GRAVE = 3
UMBRAL_CONTEO = 0.20      # día con menos de 20 % de la mediana
MIN_DIAS_CONTEO = 5
VENTANA_SALTO = 200
MIN_VENTANA_SALTO = 50
FACTOR_SALTO = 8.0
TICKS_REVERSION = 5
RAZONES_SPLIT = (2, 3, 4, 5, 10)
TOLERANCIA_SPLIT = 0.03
ORDEN_GRAVEDAD = ("grave", "aviso", "info")


@dataclass
class Problema:
    tipo: Tipo
    simbolo: str
    fuente: str
    desde: datetime
    hasta: datetime
    gravedad: Gravedad
    detalle: dict = field(default_factory=dict)


def _py(ts) -> datetime:
    return pd.Timestamp(ts).to_pydatetime()


def _inicio_dia(dia: date) -> datetime:
    return datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)


def _registro() -> dict:
    from datos import simbolos as sim
    return sim.cargar()


# --- chequeos puros ---------------------------------------------------------

def chequear_coherencia(tabla: pd.DataFrame, simbolo: str,
                        fuente: str = "dukascopy") -> list[Problema]:
    """bid > ask, ts que no crece, duplicados exactos y precios <= 0 (todo grave)."""
    if tabla.empty:
        return []
    marcas: dict[str, pd.Series] = {
        "bid_mayor_que_ask": tabla["bid"] > tabla["ask"],
        "ts_no_creciente": tabla["ts"].diff() < pd.Timedelta(0),
        "duplicados": tabla.duplicated(keep="first"),
        "precio_no_positivo": (tabla["bid"] <= 0) | (tabla["ask"] <= 0),
    }
    motivos = {k: int(v.sum()) for k, v in marcas.items() if v.any()}
    if not motivos:
        return []
    malo = pd.concat([marcas[k] for k in motivos], axis=1).any(axis=1)
    ts_malos = tabla.loc[malo, "ts"]
    return [Problema("coherencia", simbolo, fuente, _py(ts_malos.min()), _py(ts_malos.max()),
                     "grave", {"motivos": motivos})]


def _horas_abiertas(sim, dia: date) -> list[int]:
    """Horas UTC del día esperadas como abiertas (abierto al inicio y a mitad de hora)."""
    inicio = _inicio_dia(dia)
    return [h for h in range(24)
            if abierto(sim.horario, inicio + timedelta(hours=h))
            and abierto(sim.horario, inicio + timedelta(hours=h, minutes=30))]


def chequear_huecos(tabla: pd.DataFrame, sim, dia: date,
                    fuente: str = "dukascopy") -> list[Problema]:
    """Horas UTC abiertas (al inicio y a mitad de hora) sin ningún tick."""
    inicio = _inicio_dia(dia)
    abiertas = set(_horas_abiertas(sim, dia))
    con_ticks = set()
    if not tabla.empty:
        con_ticks = {int(h) for h in tabla["ts"].dt.tz_convert("UTC").dt.hour.unique()}
    vacias = []
    for h in range(24):
        if h not in con_ticks and h in abiertas:
            vacias.append(h)
    tramos: list[list[int]] = []
    for h in vacias:
        if tramos and tramos[-1][-1] == h - 1:
            tramos[-1].append(h)
        else:
            tramos.append([h])
    return [Problema("hueco", sim.simbolo, fuente, inicio + timedelta(hours=t[0]),
                     inicio + timedelta(hours=t[-1] + 1),
                     "grave" if len(t) >= HORAS_HUECO_GRAVE else "aviso",
                     {"horas": len(t)}) for t in tramos]


def chequear_saltos(tabla: pd.DataFrame, simbolo: str,
                    fuente: str = "dukascopy") -> list[Problema]:
    """Saltos del mid mayores a 8 veces la volatilidad que revierten más de la mitad."""
    n = len(tabla)
    if n < MIN_VENTANA_SALTO + 2:
        return []
    mid = ((tabla["bid"] + tabla["ask"]) / 2).to_numpy(dtype=float)
    d = pd.Series(np.diff(mid, prepend=np.nan))
    std_prev = d.rolling(VENTANA_SALTO, min_periods=MIN_VENTANA_SALTO).std().shift(1)
    grande = ((d.abs() > FACTOR_SALTO * std_prev) & (std_prev > 0)).to_numpy()
    dv = d.to_numpy()
    revierte = np.zeros(n, dtype=bool)
    acumulado = np.zeros(n)
    for k in range(1, TICKS_REVERSION + 1):
        sig = np.full(n, np.nan)
        sig[:n - k] = dv[k:]
        acumulado = acumulado + np.nan_to_num(sig)
        revierte |= (np.sign(acumulado) == -np.sign(dv)) & (np.abs(acumulado) > np.abs(dv) / 2)
    idx = np.flatnonzero(grande & revierte)
    if idx.size == 0:
        return []
    grupos: list[list[int]] = []
    for i in idx:
        if grupos and i == grupos[-1][-1] + 1:
            grupos[-1].append(int(i))
        else:
            grupos.append([int(i)])
    ts = tabla["ts"].reset_index(drop=True)
    return [Problema("salto", simbolo, fuente, _py(ts[g[0]]), _py(ts[g[-1]]), "aviso",
                     {"ticks": len(g), "salto_max": float(np.abs(dv[g]).max())})
            for g in grupos]


def chequear_conteo(conteos: dict[date, int], simbolo: str, fuente: str = "dukascopy",
                    sim=None) -> list[Problema]:
    """Días con menos del 20 % de la mediana de ticks (hacen falta 5 días).
    Con `sim`, se compara ticks por hora abierta (los días parciales, como el domingo,
    no se marcan); los días sin horas abiertas se saltan."""
    if sim is not None:
        valores = {}
        for d, n in conteos.items():
            horas = len(_horas_abiertas(sim, d))
            if horas:
                valores[d] = n / horas
    else:
        valores = dict(conteos)
    if len(valores) < MIN_DIAS_CONTEO:
        return []
    mediana = statistics.median(valores.values())
    if mediana <= 0:
        return []
    return [Problema("conteo", simbolo, fuente, _inicio_dia(d), _inicio_dia(d) + timedelta(days=1),
                     "aviso", {"ticks": int(conteos[d]), "por_hora_abierta": float(v),
                               "mediana": float(mediana)})
            for d, v in sorted(valores.items()) if v < UMBRAL_CONTEO * mediana]


def chequear_split(cierres: pd.DataFrame, simbolo: str,
                   fuente: str = "dukascopy") -> list[Problema]:
    """`cierres`: índice = día, columnas `apertura` y `cierre_anterior`.
    Marca cocientes a menos de 3 % de 2, 3, 4, 5 o 10 (o de sus inversos)."""
    objetivos = [float(r) for r in RAZONES_SPLIT] + [1.0 / r for r in RAZONES_SPLIT]
    salida = []
    for dia, fila in cierres.iterrows():
        if not fila["cierre_anterior"] or not fila["apertura"]:
            continue
        razon = fila["apertura"] / fila["cierre_anterior"]
        for obj in objetivos:
            if abs(razon / obj - 1) <= TOLERANCIA_SPLIT:
                salida.append(Problema(
                    "posible_split", simbolo, fuente, _inicio_dia(dia),
                    _inicio_dia(dia) + timedelta(days=1), "aviso",
                    {"cociente": float(razon), "razon": float(obj)}))
                break
    return salida


# --- verificación completa --------------------------------------------------

def _dias(cfg: Config, simbolo: str, fuente: str):
    """(días ok, días vacíos) de ticks del símbolo, ordenados: [(día, manifiesto)]."""
    ok, vacios = [], []
    for m in listar_manifiestos(cfg.data_dir, "ticks", fuente, simbolo):
        (ok if m.estado == "ok" else vacios).append((date.fromisoformat(m.periodo), m))
    return sorted(ok, key=lambda x: x[0]), sorted(vacios, key=lambda x: x[0])


def chequear_dias_vacios(dias_vacios: list[date], sim, fuente: str = "dukascopy",
                         primer_ok: date | None = None) -> list[Problema]:
    """Día `vacio` con horas abiertas en la plantilla = hueco grave (p. ej., caída de la
    fuente). Solo cuentan los días posteriores al primer día ok: los vacíos de antes son
    el tramo anterior al inicio del símbolo, donde la descarga se paró."""
    salida = []
    for d in dias_vacios:
        if primer_ok is None or d <= primer_ok:
            continue
        horas = _horas_abiertas(sim, d)
        if horas:
            inicio = _inicio_dia(d)
            salida.append(Problema(
                "hueco", sim.simbolo, fuente, inicio + timedelta(hours=horas[0]),
                inicio + timedelta(hours=horas[-1] + 1), "grave",
                {"motivo": "dia_vacio_en_horario_abierto"}))
    return salida


def verificar(cfg: Config, simbolos, catalogo, desde: date | None = None) -> list[Problema]:
    """Revisa los días ok de cada símbolo y los días vacíos que deberían tener datos.
    Devuelve los problemas NUEVOS: los que el catálogo no tenía ya (sin catálogo, todos
    cuentan como nuevos)."""
    nuevos: list[Problema] = []

    def _anotar(problemas: list[Problema]) -> None:
        for p in problemas:
            if catalogo is None or catalogo.anotar_problema(
                    p.tipo, p.simbolo, p.fuente, p.desde, p.hasta, p.gravedad, p.detalle):
                nuevos.append(p)

    for sim in simbolos:
        for fuente in sim.fuentes:
            todos_ok, todos_vacios = _dias(cfg, sim.simbolo, fuente)
            primer_ok = todos_ok[0][0] if todos_ok else None
            _anotar(chequear_dias_vacios([d for d, _m in todos_vacios
                                          if desde is None or d >= desde],
                                         sim, fuente, primer_ok))
            dias = [(d, m) for d, m in todos_ok if desde is None or d >= desde]
            if not dias:
                continue
            _anotar(chequear_conteo({d: m.filas for d, m in dias}, sim.simbolo, fuente, sim))
            es_accion = sim.clase in ("accion", "etf")
            cierres: dict[date, dict] = {}
            for d, _m in dias:
                tabla = _leer_ticks_sin_registro(cfg, sim.simbolo, d, d + timedelta(days=1),
                                                 fuente)
                _anotar(chequear_coherencia(tabla, sim.simbolo, fuente))
                _anotar(chequear_huecos(tabla, sim, d, fuente))
                _anotar(chequear_saltos(tabla, sim.simbolo, fuente))
                if es_accion and not tabla.empty:
                    mid = (tabla["bid"] + tabla["ask"]) / 2
                    cierres[d] = {"apertura": float(mid.iloc[0]), "cierre": float(mid.iloc[-1])}
            if es_accion:
                filas, prev = {}, None
                for d in sorted(cierres):
                    if prev is not None and (d - prev).days <= 5:
                        filas[d] = {"apertura": cierres[d]["apertura"],
                                    "cierre_anterior": cierres[prev]["cierre"]}
                    prev = d
                if filas:
                    _anotar(chequear_split(pd.DataFrame.from_dict(filas, orient="index"),
                                           sim.simbolo, fuente))
    return nuevos


# --- informe ----------------------------------------------------------------

def _tabla_html(cabecera: list[str], filas: list[list]) -> str:
    th = "".join(f"<th>{html.escape(str(c))}</th>" for c in cabecera)
    tr = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in f) + "</tr>"
                 for f in filas)
    return f"<table><tr>{th}</tr>{tr}</table>"


def escribir_informe(cfg: Config, problemas: list[Problema],
                     resumen_descarga: dict | None = None) -> Path:
    """Escribe `_informes/AAAA-MM-DD.html` (sobrescribe el del día). Nunca incluye el DSN."""
    hoy = datetime.now(timezone.utc).date()
    registro = _registro()
    bajado: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    por_clase: dict[str, int] = defaultdict(int)
    for m in listar_manifiestos(cfg.data_dir):
        if m.tipo == "ticks":
            bajado[(m.simbolo, m.fuente)][0 if m.estado == "ok" else 1] += 1
        s = registro.get(m.simbolo)
        por_clase[s.clase if s else "desconocida"] += m.bytes

    partes = [f"<h1>Informe de datos {hoy.isoformat()}</h1>",
              "<h2>Bajado</h2>"]
    partes.append(_tabla_html(["Símbolo", "Fuente", "Días ok", "Días vacíos"],
                              [[s, f, v[0], v[1]] for (s, f), v in sorted(bajado.items())])
                  if bajado else "<p>No hay ticks guardados.</p>")
    faltantes = (resumen_descarga or {}).get("faltantes")
    if faltantes is not None:
        partes.append("<h2>Faltante</h2>")
        partes.append(_tabla_html(["Símbolo", "Días sin bajar"], sorted(faltantes.items()))
                      if faltantes else "<p>No falta ningún día.</p>")
    partes.append("<h2>Tamaño por clase</h2>")
    partes.append(_tabla_html(["Clase", "GB"], [[c, f"{b / 1e9:.3f}"]
                                                for c, b in sorted(por_clase.items())])
                  if por_clase else "<p>Sin datos.</p>")
    partes.append("<h2>Problemas nuevos</h2>")
    if not problemas:
        partes.append("<p>Ninguno.</p>")
    for g in ORDEN_GRAVEDAD:
        grupo = [p for p in problemas if p.gravedad == g]
        if grupo:
            partes.append(f"<h3>{g} ({len(grupo)})</h3>")
            partes.append(_tabla_html(
                ["Tipo", "Símbolo", "Fuente", "Desde", "Hasta", "Detalle"],
                [[p.tipo, p.simbolo, p.fuente, p.desde.isoformat(), p.hasta.isoformat(),
                  str(p.detalle)] for p in grupo]))
    cuerpo = "\n".join(partes)
    texto = ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
             f"<title>Informe de datos {hoy.isoformat()}</title>"
             "<style>body{font-family:sans-serif;margin:2em}table{border-collapse:collapse}"
             "td,th{border:1px solid #999;padding:4px 8px;text-align:left}</style></head>"
             f"<body>{cuerpo}</body></html>")
    destino = Path(cfg.data_dir) / "_informes" / f"{hoy.isoformat()}.html"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    return destino
