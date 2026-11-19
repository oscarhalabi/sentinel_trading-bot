"""Consulta de datos de mercado para investigación y laboratorio.

Garantías:
- Cada vela lleva `ts` (apertura) y `ts_cierre`. Una vela solo "existe" a partir de su
  `ts_cierre`: así nadie usa un dato antes de que se conozca (sin ver el futuro).
- Al juntar varios mercados, cada instante toma la última vela YA CERRADA de cada uno.
- Datos de contexto (calendario, series, COT, ETF, corporativos): `contexto()` devuelve lo que
  se sabía en un instante y `unir_contexto()` lo pega a cada vela por su `ts_cierre`.
- Los últimos 12 meses están en reserva: se recortan salvo `incluir_reservado=True`, y
  cada acceso explícito se apunta en `DATA_DIR/_registro/reserva.jsonl`.

Lee los Parquet directamente con DuckDB. No necesita Postgres.
"""
from __future__ import annotations

import getpass
import heapq
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import (
    ESQUEMA_CALENDARIO,
    ESQUEMA_CORPORATIVO,
    ESQUEMA_COT,
    ESQUEMA_ETF,
    ESQUEMA_M1,
    ESQUEMA_SERIE,
    ESQUEMA_TICKS,
)
from datos.config import Config, cargar_config
from datos.contexto_conf import cargar_contexto

TEMPORALIDADES = {"m1": 1, "m5": 5, "m15": 15, "m30": 30, "h1": 60, "h4": 240, "d1": None}

NUEVA_YORK = ZoneInfo("America/New_York")
CARPETA_REGISTRO = "_registro"
ARCHIVO_RESERVA = "reserva.jsonl"

Fecha = date | datetime


class ReservaBloqueada(PermissionError):
    """El rango pedido cae entero dentro de la reserva (no es que falten datos)."""


# --- fechas y reserva -------------------------------------------------------

def _utc(valor: Fecha) -> datetime:
    """date -> 00:00 UTC; datetime sin zona -> se toma como UTC; con zona -> a UTC."""
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            return valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc)
    if isinstance(valor, date):
        return datetime.combine(valor, time(0), tzinfo=timezone.utc)
    raise TypeError(f"Se esperaba date o datetime, no {type(valor).__name__}")


def corte_reserva(cfg: Config, hoy: date | None = None) -> datetime:
    """Inicio de la reserva (UTC): DATOS_CORTE_RESERVA o hoy menos 365 días."""
    if cfg.corte_reserva is not None:
        return _utc(cfg.corte_reserva)
    hoy = hoy or datetime.now(timezone.utc).date()
    return _utc(hoy - timedelta(days=365))


def _quien() -> str:
    try:
        usuario = getpass.getuser()
    except Exception:
        usuario = "?"
    script = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "?"
    return f"{usuario}:{script}"


def _registrar_acceso(cfg: Config, simbolo: str, desde: datetime, hasta: datetime) -> None:
    """Añade una línea a `_registro/reserva.jsonl` (el catálogo la importa después)."""
    carpeta = Path(cfg.data_dir) / CARPETA_REGISTRO
    carpeta.mkdir(parents=True, exist_ok=True)
    linea = {
        "momento": datetime.now(timezone.utc).isoformat(),
        "quien": _quien(),
        "simbolo": simbolo,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
    }
    with open(carpeta / ARCHIVO_RESERVA, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")


def _aplicar_reserva(cfg: Config, simbolo: str, desde: datetime, hasta: datetime,
                     incluir_reservado: bool) -> datetime | None:
    """Devuelve el corte a respetar (None si se incluye la reserva) y registra el acceso."""
    if incluir_reservado:
        _registrar_acceso(cfg, simbolo, desde, hasta)
        return None
    corte = corte_reserva(cfg)
    if desde >= corte:
        raise ReservaBloqueada(
            f"El rango {desde:%Y-%m-%d} a {hasta:%Y-%m-%d} está entero en la reserva "
            f"(desde {corte:%Y-%m-%d}). Usa incluir_reservado=True si de verdad hace falta; "
            "el acceso quedará registrado.")
    return corte


# --- lectura de Parquet -----------------------------------------------------

def _archivos(raiz: Path, tipo: str, fuente: str, simbolo: str,
              desde: datetime, hasta: datetime) -> list[str]:
    """Parquet del símbolo que pueden tener datos en [desde, hasta), sin recorrer el resto."""
    base = Path(raiz) / tipo / f"fuente={fuente}" / f"simbolo={simbolo}"
    if not base.is_dir() or hasta <= desde:
        return []
    ultimo = hasta - timedelta(microseconds=1)
    if tipo == "ticks":
        primero, fin = f"{desde:%Y-%m-%d}", f"{ultimo:%Y-%m-%d}"
    else:
        primero, fin = f"{desde:%Y-%m}", f"{ultimo:%Y-%m}"
    rutas = []
    for anio in range(desde.year, ultimo.year + 1):
        carpeta = base / f"anio={anio:04d}"
        if not carpeta.is_dir():
            continue
        for p in sorted(carpeta.glob("*.parquet")):
            if primero <= p.stem <= fin:
                rutas.append(p.as_posix())
    return rutas


def _leer(raiz: Path, tipo: str, fuente: str, simbolo: str,
          desde: datetime, hasta: datetime) -> pd.DataFrame:
    """Filas con ts en [desde, hasta), ordenadas por ts, como DataFrame con ts en UTC."""
    esquema = ESQUEMA_TICKS if tipo == "ticks" else ESQUEMA_M1
    rutas = _archivos(raiz, tipo, fuente, simbolo, desde, hasta)
    if not rutas:
        return esquema.empty_table().to_pandas()
    columnas = ", ".join(esquema.names)
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")
        tabla = con.sql(
            f"SELECT {columnas} FROM read_parquet(?, hive_partitioning=false) "
            "WHERE ts >= ? AND ts < ? ORDER BY ts",
            params=[rutas, desde, hasta]).to_arrow_table()
    finally:
        con.close()
    return tabla.cast(esquema).to_pandas()


def _vacia(esquema: pa.Schema) -> pa.Table:
    extra = pa.schema([("fuente", pa.string()), ("simbolo", pa.string()), ("anio", pa.int64())])
    return pa.unify_schemas([esquema, extra]).empty_table()


def conectar(cfg: Config | None = None,
             incluir_reservado: bool = False) -> duckdb.DuckDBPyConnection:
    """Conexión DuckDB con las vistas `ticks` y `m1` sobre DATA_DIR (para consultas libres).

    Las vistas incluyen las columnas de partición `fuente`, `simbolo` y `anio`; filtrar por
    ellas evita leer carpetas que no hacen falta. Por defecto las vistas cortan en la
    reserva: `ticks` solo tiene `ts` anteriores al corte y `m1` solo velas que cierran
    (ts + 1 minuto) como mucho en el corte. Con `incluir_reservado=True` se ve todo y el
    acceso queda registrado (símbolo "*", rango completo).
    """
    cfg = cfg or cargar_config()
    if incluir_reservado:
        _registrar_acceso(cfg, "*", datetime.min.replace(tzinfo=timezone.utc),
                          datetime.max.replace(tzinfo=timezone.utc))
        filtros = {"ticks": "", "m1": ""}
    else:
        corte = f"TIMESTAMPTZ '{corte_reserva(cfg).isoformat()}'"
        filtros = {"ticks": f" WHERE ts < {corte}",
                   "m1": f" WHERE ts + INTERVAL 1 MINUTE <= {corte}"}
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    for tipo, esquema in (("ticks", ESQUEMA_TICKS), ("m1", ESQUEMA_M1)):
        carpeta = Path(cfg.data_dir) / tipo
        patron = (carpeta / "**" / "*.parquet").as_posix()
        if carpeta.is_dir() and any(carpeta.rglob("*.parquet")):
            ruta = patron.replace("'", "''")
            origen = (f"read_parquet('{ruta}', hive_partitioning=true, "
                      "hive_types={'fuente': VARCHAR, 'simbolo': VARCHAR, 'anio': BIGINT})")
        else:
            con.register(f"_{tipo}_vacia", _vacia(esquema))
            origen = f"_{tipo}_vacia"
        con.execute(f"CREATE VIEW {tipo} AS SELECT * FROM {origen}{filtros[tipo]}")
    return con


# --- agregación -------------------------------------------------------------

def _agregar(m1: pd.DataFrame, inicio: pd.Series, cierre: pd.Series) -> pd.DataFrame:
    """Agrupa velas M1 por bloque (inicio, cierre). Bloques sin datos no salen."""
    df = m1.assign(_ts=inicio, _cierre=cierre,
                   _peso=m1["spread_medio"] * m1["ticks"])
    g = df.groupby(["_ts", "_cierre"], sort=True)
    r = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), ask_close=("ask_close", "last"),
              spread_min=("spread_min", "min"), _peso=("_peso", "sum"),
              spread_max=("spread_max", "max"), ticks=("ticks", "sum"),
              bid_vol=("bid_vol", "sum"), ask_vol=("ask_vol", "sum")).reset_index()
    total = r["ticks"].astype("float64")
    r["spread_medio"] = np.where(total > 0, r["_peso"] / total.where(total > 0, 1.0), np.nan)
    r = r.rename(columns={"_ts": "ts", "_cierre": "ts_cierre"})
    r["ticks"] = r["ticks"].astype("int32")
    return r[["ts", "ts_cierre", *ESQUEMA_M1.names[1:]]]


def _bloques_dia_ny(ts: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Día de trading D: de las 17:00 de Nueva York de D-1 a las 17:00 de D (en UTC)."""
    local = ts.dt.tz_convert(NUEVA_YORK).dt.tz_localize(None)  # hora de pared en NY
    dia = (local + pd.Timedelta(hours=7)).dt.normalize()
    dias = pd.DatetimeIndex(dia.unique())
    a_utc = lambda d: (d + pd.Timedelta(hours=17)).tz_localize(NUEVA_YORK).tz_convert("UTC")  # noqa: E731
    inicios = dict(zip(dias, a_utc(dias - pd.Timedelta(days=1))))
    cierres = dict(zip(dias, a_utc(dias)))
    return dia.map(inicios), dia.map(cierres)


def _velas(m1: pd.DataFrame, tf: str, corte_diario: str) -> pd.DataFrame:
    if tf == "m1":
        r = m1.copy()
        r.insert(1, "ts_cierre", r["ts"] + pd.Timedelta(minutes=1))
        return r
    if tf == "d1":
        if corte_diario == "ny":
            inicio, cierre = _bloques_dia_ny(m1["ts"])
        else:
            inicio = m1["ts"].dt.floor("D")
            cierre = inicio + pd.Timedelta(days=1)
    else:
        paso = pd.Timedelta(minutes=TEMPORALIDADES[tf])
        inicio = m1["ts"].dt.floor(paso)  # múltiplos de tf desde 1970-01-01 UTC
        cierre = inicio + paso
    return _agregar(m1, inicio, cierre)


# --- API --------------------------------------------------------------------

def _rango(desde: Fecha, hasta: Fecha) -> tuple[datetime, datetime]:
    """Pasa el rango a UTC y exige que `desde` sea anterior a `hasta`."""
    d, h = _utc(desde), _utc(hasta)
    if d >= h:
        raise ValueError(f"Rango vacío o al revés: desde ({d.isoformat()}) debe ser "
                         f"anterior a hasta ({h.isoformat()}).")
    return d, h


def _validar_tf(tf: str, corte_diario: str) -> None:
    if tf not in TEMPORALIDADES:
        raise ValueError(f"Temporalidad desconocida: {tf!r}. Opciones: {', '.join(TEMPORALIDADES)}")
    if corte_diario not in ("ny", "utc"):
        raise ValueError(f"corte_diario debe ser 'ny' o 'utc', no {corte_diario!r}")


def cargar(simbolo: str, desde: Fecha, hasta: Fecha, tf: str = "m1",
           fuente: str = "dukascopy", incluir_reservado: bool = False,
           corte_diario: Literal["ny", "utc"] = "ny", cfg: Config | None = None) -> pd.DataFrame:
    """Velas completas: `ts` (apertura) >= desde y `ts_cierre` <= hasta.

    Una vela que aún no ha cerrado en `hasta` no sale (sería ver el futuro). Sin
    `incluir_reservado`, tampoco salen velas que cierran después del corte de la reserva.
    """
    _validar_tf(tf, corte_diario)
    d, h = _rango(desde, hasta)
    cfg = cfg or cargar_config()
    corte = _aplicar_reserva(cfg, simbolo, d, h, incluir_reservado)

    # Nunca se lee nada posterior a `hasta` (ni al corte de la reserva).
    limite = h if corte is None else min(h, corte)
    m1 = _leer(cfg.data_dir, "m1", fuente, simbolo, d, limite)
    if m1.empty:
        return _velas(m1, "m1", corte_diario).iloc[0:0].reset_index(drop=True)

    velas = _velas(m1, tf, corte_diario)
    dentro = (velas["ts"] >= pd.Timestamp(d)) & (velas["ts_cierre"] <= pd.Timestamp(limite))
    return velas[dentro].reset_index(drop=True)


def _leer_ticks_sin_registro(cfg: Config, simbolo: str, desde: Fecha, hasta: Fecha,
                             fuente: str = "dukascopy") -> pd.DataFrame:
    """Ticks en [desde, hasta) SIN aplicar la reserva ni registrar. Solo para controles
    internos de calidad: la investigación debe usar `ticks()`."""
    return _leer(cfg.data_dir, "ticks", fuente, simbolo, _utc(desde), _utc(hasta))


def ticks(simbolo: str, desde: Fecha, hasta: Fecha, fuente: str = "dukascopy",
          incluir_reservado: bool = False, cfg: Config | None = None) -> pd.DataFrame:
    """Ticks con `ts` en [desde, hasta), respetando la reserva."""
    d, h = _rango(desde, hasta)
    cfg = cfg or cargar_config()
    corte = _aplicar_reserva(cfg, simbolo, d, h, incluir_reservado)
    if corte is not None:
        h = min(h, corte)
    return _leer(cfg.data_dir, "ticks", fuente, simbolo, d, h)


def cargar_varios(simbolos: list[str], desde: Fecha, hasta: Fecha, tf: str,
                  fuente: str = "dukascopy", rellenar: Literal["ultimo_conocido"] | None = None,
                  incluir_reservado: bool = False, cfg: Config | None = None) -> pd.DataFrame:
    """Varios símbolos en formato largo (`simbolo`, `ts_cierre`, columnas), por (ts_cierre, simbolo).

    Con `rellenar="ultimo_conocido"`, la rejilla es la unión de los `ts_cierre` y cada
    símbolo toma en cada instante la última vela con `ts_cierre` <= ese instante (nunca
    una posterior). La columna `relleno` dice si el valor se arrastró de antes. Los
    instantes anteriores al primer dato de un símbolo no salen para ese símbolo.
    """
    if rellenar not in (None, "ultimo_conocido"):
        raise ValueError(f"rellenar debe ser None o 'ultimo_conocido', no {rellenar!r}")
    cfg = cfg or cargar_config()
    por_simbolo = {s: cargar(s, desde, hasta, tf=tf, fuente=fuente,
                             incluir_reservado=incluir_reservado, cfg=cfg) for s in simbolos}

    partes = []
    if rellenar is None:
        for s, df in por_simbolo.items():
            partes.append(df.assign(simbolo=s))
    else:
        tiempos = [df["ts_cierre"] for df in por_simbolo.values() if not df.empty]
        if tiempos:
            rejilla = pd.DataFrame({"ts_cierre": pd.concat(tiempos).drop_duplicates()
                                    .sort_values().reset_index(drop=True)})
            for s, df in por_simbolo.items():
                if df.empty:
                    continue
                datos = df.rename(columns={"ts_cierre": "_cierre_dato"})
                r = pd.merge_asof(rejilla, datos, left_on="ts_cierre", right_on="_cierre_dato",
                                  direction="backward")
                r = r[r["_cierre_dato"].notna()]
                r = r.assign(simbolo=s, relleno=r["ts_cierre"] != r["_cierre_dato"])
                partes.append(r.drop(columns="_cierre_dato"))

    columnas = ["simbolo", "ts_cierre", "ts", *ESQUEMA_M1.names[1:]]
    if rellenar is not None:
        columnas.append("relleno")
    if not partes:
        base = _velas(ESQUEMA_M1.empty_table().to_pandas(), "m1", "ny").assign(simbolo="")
        if rellenar is not None:
            base = base.assign(relleno=False)
        return base[columnas].iloc[0:0].reset_index(drop=True)
    largo = pd.concat(partes, ignore_index=True)[columnas]
    return largo.sort_values(["ts_cierre", "simbolo"], kind="stable").reset_index(drop=True)


# --- contexto (Fase 2C) -----------------------------------------------------
#
# Regla de oro: en un instante t solo se ve lo que se sabía en t. Cada tipo guarda cuándo
# se conoció cada fila (`ts_conocido`) y, si tiene versiones, cuándo se guardó cada versión
# (`ts_version`). Aquí se filtra por esos instantes; nunca por la fecha del dato.

TIPOS_CONTEXTO = ("calendario", "serie", "cot", "etf", "corporativo")
_ESQUEMAS_CONTEXTO = {"calendario": ESQUEMA_CALENDARIO, "serie": ESQUEMA_SERIE,
                      "cot": ESQUEMA_COT, "etf": ESQUEMA_ETF,
                      "corporativo": ESQUEMA_CORPORATIVO}
# Columna por la que se filtra [desde, hasta) en cada tipo.
_COLUMNA_RANGO = {"calendario": "ts_evento", "serie": "periodo", "cot": "fecha_informe",
                  "etf": "fecha", "corporativo": "fecha_ex"}
_CLAVE_COT = ["mercado", "informe", "fecha_informe", "campo"]
_ORDEN_IMPACTO = {"bajo": 0, "medio": 1, "alto": 2}
_PREVISIONES = ("ff", "api", "mejor")
_FUENTE_FF = "forexfactory"
_EMPAREJAR_FF = pd.Timedelta(minutes=90)
# Prioridad de informe COT cuando un campo está en varios (p. ej. interes_abierto).
_PRIORIDAD_COT = ("legacy", "disaggregated", "tff")
_SIN_FIN = np.iinfo(np.int64).max
_COLUMNAS_EXTRA_CAL = ["sorpresa", "revision_anterior", "fuente_prevision"]


def _latencia(latencia: str | timedelta | None) -> pd.Timedelta:
    """'5s', '1min' o timedelta. None → `latencia_por_defecto` de contexto.json."""
    if latencia is None:
        latencia = cargar_contexto()["latencia_por_defecto"]
    lat = pd.Timedelta(latencia)
    if lat < pd.Timedelta(0):
        raise ValueError(f"La latencia no puede ser negativa: {latencia!r}")
    return lat


def _archivos_contexto(raiz: Path, tipo: str, fuentes: list[str] | None,
                       claves: list[str] | None, anios: range | None) -> list[Path]:
    """Parquet de un tipo de contexto, recortando por fuente, clave (simbolo=) y año."""
    base = Path(raiz) / tipo
    if not base.is_dir():
        return []
    rutas = []
    for dir_f in sorted(base.glob("fuente=*")):
        if fuentes and dir_f.name.split("=", 1)[1] not in fuentes:
            continue
        for dir_s in sorted(dir_f.glob("simbolo=*")):
            if claves and dir_s.name.split("=", 1)[1] not in claves:
                continue
            for p in sorted(dir_s.glob("*.parquet")):
                completo = p.name == "completo.parquet"
                del_anio = p.stem.startswith("anio=") and (
                    anios is None or int(p.stem.split("=", 1)[1]) in anios)
                if completo or del_anio:
                    rutas.append(p)
    return rutas


def _leer_contexto(cfg: Config, tipo: str, fuentes: list[str] | None = None,
                   claves: list[str] | None = None, anios: range | None = None) -> pd.DataFrame:
    esquema = _ESQUEMAS_CONTEXTO[tipo]
    rutas = _archivos_contexto(cfg.data_dir, tipo, fuentes, claves, anios)
    if not rutas:
        return esquema.empty_table().to_pandas()
    tabla = pa.concat_tables(pq.read_table(p).select(esquema.names).cast(esquema) for p in rutas)
    return tabla.to_pandas()


def _a_utc(fechas: pd.Series) -> pd.Series:
    """Columna de fechas (date) → medianoche UTC, para comparar con el rango."""
    return pd.to_datetime(fechas).dt.tz_localize("UTC") if len(fechas) else \
        pd.Series(pd.DatetimeIndex([], tz="UTC"), index=fechas.index)


def _en_rango(df: pd.DataFrame, tipo: str, d: datetime, h: datetime) -> pd.DataFrame:
    col = df[_COLUMNA_RANGO[tipo]]
    if tipo != "calendario":
        col = _a_utc(col)
    return df[(col >= pd.Timestamp(d)) & (col < pd.Timestamp(h))]


def _clave_evento(df: pd.DataFrame) -> pd.Series:
    """Clave de un evento entre versiones (la misma regla que usan los escritores):
    fuente + id de la fuente, o fuente + evento + divisa + día de Nueva York."""
    if df.empty:
        return pd.Series([], index=df.index, dtype=object)
    dia = df["ts_evento"].dt.tz_convert(NUEVA_YORK).dt.date.astype(str)
    por_nombre = "ev:" + df["evento"].astype(str) + "|" + df["divisa"].astype(str) + "|" + dia
    por_id = "id:" + df["id_fuente"].astype(str)
    return df["fuente"].astype(str) + "/" + por_id.where(df["id_fuente"].notna(), por_nombre)


def _ultima_version(df: pd.DataFrame, clave: list[str], orden: str) -> pd.DataFrame:
    """Una fila por clave: la de mayor `orden` (ts_version o ts_conocido)."""
    return (df.sort_values(orden, kind="stable")
            .drop_duplicates(subset=clave, keep="last"))


def _momento_real(df: pd.DataFrame) -> pd.Series:
    """Desde cuándo se conoce el dato real. Un evento sin hora exacta (solo la fecha) se
    da por conocido al final de su día de Nueva York: antes no sabemos a qué hora salió."""
    fin_dia = (df["ts_evento"].dt.tz_convert(NUEVA_YORK).dt.normalize()
               + pd.DateOffset(days=1)).dt.tz_convert("UTC")
    exacta = df["hora_exacta"].fillna(False).astype(bool)
    tarde = df["ts_conocido"].where(df["ts_conocido"] >= fin_dia, fin_dia)
    return df["ts_conocido"].where(exacta, tarde)


def _prevision_ff(eventos: pd.DataFrame, fotos: pd.DataFrame,
                  t: pd.Timestamp | None) -> pd.Series:
    """Previsión de FF para cada evento: la de la última foto anterior al evento (y a t),
    del mismo evento y divisa con la hora a ±90 min. NaN si no hay."""
    vacia = pd.Series(np.nan, index=eventos.index, dtype="float64")
    if eventos.empty or fotos.empty:
        return vacia
    fotos = fotos[fotos["prevision"].notna()]
    if t is not None:
        fotos = fotos[fotos["ts_version"] <= t]
    # Se cruza por evento, divisa y día UTC (el del evento ±1: a ±90 min no hay más), para no
    # cruzar cada evento con todas las fotos de años de ese mismo nombre.
    izq = eventos[["evento", "divisa", "ts_evento"]].assign(_fila=eventos.index)
    dia = izq["ts_evento"].dt.floor("D")
    izq = pd.concat([izq.assign(_dia=dia + pd.Timedelta(days=k)) for k in (-1, 0, 1)],
                    ignore_index=True)
    der = fotos[["evento", "divisa", "ts_evento", "ts_version", "prevision"]].rename(
        columns={"ts_evento": "_ts_ff", "prevision": "_prev_ff"})
    der = der.assign(_dia=der["_ts_ff"].dt.floor("D"))
    m = izq.merge(der, on=["evento", "divisa", "_dia"], how="inner")
    m = m[((m["_ts_ff"] - m["ts_evento"]).abs() <= _EMPAREJAR_FF)
          & (m["ts_version"] < m["ts_evento"])]
    if m.empty:
        return vacia
    ultima = m.sort_values("ts_version", kind="stable").drop_duplicates("_fila", keep="last")
    return vacia.combine_first(ultima.set_index("_fila")["_prev_ff"]).reindex(eventos.index)


def _elegir_prevision(eventos: pd.DataFrame, fotos: pd.DataFrame, modo: str,
                      t: pd.Timestamp | None) -> tuple[pd.Series, pd.Series]:
    """(prevision, fuente_prevision) según el modo 'ff', 'api' o 'mejor'."""
    propia = eventos["prevision"].astype("float64")
    etiqueta_propia = pd.Series(np.where(eventos["fuente"] == _FUENTE_FF, "ff", "api"),
                                index=eventos.index, dtype=object)
    ff = (_prevision_ff(eventos, fotos, t) if modo != "api"
          else pd.Series(np.nan, index=eventos.index, dtype="float64"))
    if modo == "api":
        prev, etiqueta = propia, etiqueta_propia
    elif modo == "ff":
        prev, etiqueta = ff, pd.Series("ff", index=eventos.index, dtype=object)
    else:
        usa_ff = ff.notna()
        prev = ff.where(usa_ff, propia)
        etiqueta = etiqueta_propia.where(~usa_ff, "ff")
    etiqueta = etiqueta.where(prev.notna(), None)
    return prev, etiqueta


def _leer_fotos_ff(cfg: Config, divisas: list[str] | None, anios: range,
                   ya_leido: pd.DataFrame | None) -> pd.DataFrame:
    if ya_leido is not None:
        return ya_leido[ya_leido["fuente"] == _FUENTE_FF]
    return _leer_contexto(cfg, "calendario", [_FUENTE_FF], divisas, anios)


def _aplicar_horizonte(df: pd.DataFrame, horizonte_dias: float | None) -> pd.DataFrame:
    """Recalcula `ts_anunciado = ts_evento − horizonte` en las filas históricas (previsión no
    guardada en su momento). Los eventos no programados usan horizonte 0."""
    if horizonte_dias is None or df.empty:
        return df
    historica = ~df["prevision_es_pit"].fillna(False).astype(bool)
    programado = df["programado"].fillna(True).astype(bool)
    horizonte = pd.Series(np.where(programado, horizonte_dias, 0.0), index=df.index)
    nuevo = df["ts_evento"] - pd.to_timedelta(horizonte, unit="D")
    df = df.copy()
    df["ts_anunciado"] = nuevo.where(historica, df["ts_anunciado"]).astype(df["ts_evento"].dtype)
    return df


def _contexto_calendario(cfg: Config, d: datetime, h: datetime, t: pd.Timestamp,
                         claves, fuente, lat, prevision, horizonte_dias) -> pd.DataFrame:
    anios = range(d.year - 1, h.year + 2)   # una versión reprogramada puede cambiar de año
    df = _leer_contexto(cfg, "calendario", [fuente] if fuente else None, claves, anios)
    df = _aplicar_horizonte(df, horizonte_dias)
    columnas = [*ESQUEMA_CALENDARIO.names, *_COLUMNAS_EXTRA_CAL]
    guardadas = df[df["ts_version"] <= t]
    if guardadas.empty:
        return df.reindex(columns=columnas).iloc[0:0].reset_index(drop=True)
    guardadas = guardadas.assign(_clave=_clave_evento(guardadas))
    ev = _ultima_version(guardadas, ["_clave"], "ts_version")
    ev = ev[ev["ts_anunciado"] <= t]
    ev = _en_rango(ev, "calendario", d, h).copy()

    # El dato real (y lo que se publica con él) solo desde ts_conocido + latencia.
    conocido = (_momento_real(ev) + lat) <= t
    for col in ("real", "anterior_revisado"):
        ev[col] = ev[col].where(conocido)

    reusar = df if fuente in (None, _FUENTE_FF) else None
    fotos = df.iloc[0:0] if prevision == "api" else _leer_fotos_ff(cfg, claves, anios, reusar)
    ev["prevision"], ev["fuente_prevision"] = _elegir_prevision(ev, fotos, prevision, t)
    ev["sorpresa"] = ev["real"] - ev["prevision"]
    ev["revision_anterior"] = ev["anterior_revisado"] - ev["anterior"]
    ev = ev.sort_values(["ts_evento", "fuente", "divisa", "evento"], kind="stable")
    return ev[columnas].reset_index(drop=True)


def _contexto_serie(df: pd.DataFrame, t: pd.Timestamp, lat: pd.Timedelta,
                    excluir_estimadas: bool) -> pd.DataFrame:
    if excluir_estimadas:
        df = df[~df["publicacion_estimada"].fillna(False).astype(bool)]
    vigente = ((df["ts_conocido"] + lat <= t)
               & (df["ts_hasta"].isna() | (t < df["ts_hasta"] + lat)))
    df = _ultima_version(df[vigente], ["fuente", "serie", "periodo"], "ts_conocido")
    return df.sort_values(["serie", "periodo", "fuente"], kind="stable")


def _contexto_cot(df: pd.DataFrame, t: pd.Timestamp, lat: pd.Timedelta) -> pd.DataFrame:
    df = df[(df["ts_version"] <= t) & (df["ts_conocido"] + lat <= t)]
    df = _ultima_version(df, _CLAVE_COT, "ts_version")
    return df.sort_values(["mercado", "informe", "fecha_informe", "campo"], kind="stable")


def contexto(tipo: str, desde: Fecha, hasta: Fecha, conocido_en: Fecha | None = None,
             claves: list[str] | None = None, fuente: str | None = None,
             latencia: str | timedelta | None = None,
             prevision: Literal["ff", "api", "mejor"] = "mejor",
             horizonte_dias: float | None = None, excluir_estimadas: bool = False,
             incluir_reservado: bool = False, cfg: Config | None = None) -> pd.DataFrame:
    """Lo que se sabía en `conocido_en` (por defecto `hasta`) de un tipo de contexto.

    - `desde`/`hasta` filtran por la fecha del dato: `ts_evento` (calendario), `periodo`
      (serie), `fecha_informe` (cot), `fecha` (etf), `fecha_ex` (corporativo).
    - `claves`: divisas (calendario), series, mercados, ETF o símbolos. `fuente`: una sola.
    - `latencia` ('5s' o timedelta; por defecto la de contexto.json): un dato publicado en
      `ts_conocido` se ve desde `ts_conocido + latencia`.
    - calendario: por evento, la última versión con `ts_version ≤ conocido_en`; el evento
      sale si `ts_anunciado ≤ conocido_en`; `real`, `sorpresa`, `anterior_revisado` y
      `revision_anterior` van vacíos hasta que se conoce el dato. `prevision` elige la
      previsión ('ff': última foto de Forex Factory anterior al evento, a ±90 min; 'api':
      la de la propia fila; 'mejor': FF si hay, si no la de la API) y `fuente_prevision`
      dice cuál se usó. `horizonte_dias` recalcula el anuncio de las filas históricas.
    - serie: la versión vigente en ese instante; `excluir_estimadas` quita las publicaciones
      estimadas de las cargas en bloque de ALFRED.
    - Reserva: sin `incluir_reservado` nada conocido después del corte se ve; si todo el
      rango está en la reserva, `ReservaBloqueada`. Con `incluir_reservado` se registra.
    """
    if tipo not in TIPOS_CONTEXTO:
        raise ValueError(f"Tipo de contexto desconocido: {tipo!r}. Opciones: "
                         f"{', '.join(TIPOS_CONTEXTO)}")
    if prevision not in _PREVISIONES:
        raise ValueError(f"prevision debe ser 'ff', 'api' o 'mejor', no {prevision!r}")
    d, h = _rango(desde, hasta)
    t = _utc(conocido_en) if conocido_en is not None else h
    cfg = cfg or cargar_config()
    lat = _latencia(latencia)
    nombre = f"{tipo}:{','.join(claves) if claves else '*'}"
    corte = _aplicar_reserva(cfg, nombre, d, h, incluir_reservado)
    if corte is not None:
        t = min(t, corte)   # lo conocido después del corte es reserva
    t = pd.Timestamp(t)

    if tipo == "calendario":
        return _contexto_calendario(cfg, d, h, t, claves, fuente, lat, prevision,
                                    horizonte_dias)
    fuentes = [fuente] if fuente else None
    anios = range(d.year - 1, h.year + 2) if tipo == "cot" else None
    df = _en_rango(_leer_contexto(cfg, tipo, fuentes, claves, anios), tipo, d, h)
    if tipo == "serie":
        df = _contexto_serie(df, t, lat, excluir_estimadas)
    elif tipo == "cot":
        df = _contexto_cot(df, t, lat)
    else:
        df = df[df["ts_conocido"] + lat <= t].sort_values(
            [_COLUMNA_RANGO[tipo], "ts_conocido"], kind="stable")
    return df.reset_index(drop=True)


# --- unir_contexto ------------------------------------------------------------

def _ns(serie: pd.Series) -> np.ndarray:
    """Instantes UTC → enteros en nanosegundos (NaT → int64 mínimo)."""
    return pd.DatetimeIndex(serie).as_unit("ns").asi8


def _escalon(conocido: pd.Series, periodo: pd.Series, valores: pd.DataFrame) -> pd.DataFrame:
    """Último valor conocido en cada instante: el del periodo más reciente ya publicado, en
    su última versión. Una revisión de un periodo viejo no sustituye al último periodo.
    Devuelve una fila por instante de publicación (`_conocido`) con las columnas de `valores`."""
    df = valores.assign(_conocido=conocido, _periodo=pd.to_datetime(periodo))
    df = df.sort_values(["_conocido", "_periodo"], kind="stable").reset_index(drop=True)
    maximo = df["_periodo"].cummax()
    fila = pd.Series(np.where(df["_periodo"] == maximo, df.index, -1), index=df.index)
    fila = fila.where(fila >= 0).ffill().astype(int)
    r = df.loc[fila.values, list(valores.columns)].reset_index(drop=True)
    r["_conocido"] = df["_conocido"].array
    return r.drop_duplicates("_conocido", keep="last")


def _pegar(base: pd.DataFrame, escalon: pd.DataFrame) -> pd.DataFrame:
    """merge_asof hacia atrás: cada vela toma el escalón publicado en o antes de su cierre."""
    if escalon.empty:
        return base.assign(**{c: np.nan for c in escalon.columns if c != "_conocido"})
    escalon = escalon.assign(_conocido=escalon["_conocido"].dt.as_unit(
        base["ts_cierre"].dtype.unit))
    return pd.merge_asof(base, escalon.sort_values("_conocido"), left_on="ts_cierre",
                         right_on="_conocido", direction="backward",
                         allow_exact_matches=True).drop(columns="_conocido")


def _hasta_limite(df: pd.DataFrame, conocido: pd.Series, limite: pd.Timestamp | None):
    if limite is None:
        return df, conocido
    dentro = conocido <= limite
    return df[dentro], conocido[dentro]


def _unir_serie(base, cfg, claves, lat, limite):
    df = _leer_contexto(cfg, "serie", None, claves or None)
    for sid, g in df.groupby("serie", sort=True):
        g, conocido = _hasta_limite(g, g["ts_conocido"] + lat, limite)
        base = _pegar(base, _escalon(conocido, g["periodo"],
                                     g[["valor"]].rename(columns={"valor": sid})))
    return base


def _unir_cot(base, cfg, claves, lat, limite, anios):
    df = _leer_contexto(cfg, "cot", None, claves or None, anios)
    campos = cargar_contexto()["cot"]["campos"]
    for (mercado, campo), g in df[df["campo"].isin(campos)].groupby(["mercado", "campo"]):
        informe = next(i for i in _PRIORIDAD_COT if i in set(g["informe"]))
        g = g[g["informe"] == informe]
        # Una versión se ve cuando ya se publicó el informe y ya se guardó esa versión.
        publicado = g["ts_conocido"] + lat
        conocido = publicado.where(publicado >= g["ts_version"], g["ts_version"])
        g, conocido = _hasta_limite(g, conocido, limite)
        nombre = f"cot_{mercado}_{campo}"
        base = _pegar(base, _escalon(conocido, g["fecha_informe"],
                                     g[["valor"]].rename(columns={"valor": nombre})))
    return base


def _unir_etf(base, cfg, claves, lat, limite):
    df = _leer_contexto(cfg, "etf", None, claves or None)
    for etf, g in df.groupby("etf", sort=True):
        g, conocido = _hasta_limite(g, g["ts_conocido"] + lat, limite)
        valores = g[["toneladas", "valor_usd"]].rename(
            columns={"toneladas": f"{etf}_toneladas", "valor_usd": f"{etf}_valor_usd"})
        base = _pegar(base, _escalon(conocido, g["fecha"], valores))
    return base


def _barrido(desde: np.ndarray, hasta: np.ndarray, prioridad: list[tuple],
             tiempos: np.ndarray) -> np.ndarray:
    """Para cada instante de `tiempos`, la fila de menor `prioridad` entre las vigentes
    (`desde ≤ t < hasta`); -1 si ninguna. Recorre las filas una vez (no una vez por vela):
    el resultado solo cambia en los bordes de los intervalos, y cada vela busca el suyo."""
    validas = np.flatnonzero(desde < hasta)
    if not len(validas) or not len(tiempos):
        return np.full(len(tiempos), -1)
    orden = validas[np.argsort(desde[validas], kind="stable")]
    puntos = np.unique(np.concatenate([desde[validas], hasta[validas]]))
    mejor = np.full(len(puntos), -1)
    monton: list[tuple] = []
    j = 0
    for k, p in enumerate(puntos):
        while j < len(orden) and desde[orden[j]] <= p:
            i = orden[j]
            heapq.heappush(monton, (*prioridad[i], hasta[i], i))
            j += 1
        while monton and monton[0][-2] <= p:
            heapq.heappop(monton)
        mejor[k] = monton[0][-1] if monton else -1
    pos = np.searchsorted(puntos, tiempos, side="right") - 1
    return np.where(pos >= 0, mejor[np.maximum(pos, 0)], -1)


def _unir_calendario(base, cfg, fuentes, lat, prevision, impacto_min, divisas, limite, anios):
    df = _leer_contexto(cfg, "calendario", fuentes or None, divisas, anios)
    fotos = (df.iloc[0:0] if prevision == "api"
             else _leer_fotos_ff(cfg, divisas, anios, df if not fuentes else None))
    df = df[df["ts_version"].notna()]
    df = df.assign(_clave=_clave_evento(df)).sort_values(["_clave", "ts_version"], kind="stable")
    df = df.drop_duplicates(["_clave", "ts_version"], keep="last")
    # Cada versión vale desde que se guardó hasta que llega la siguiente de su evento. Se
    # calcula con TODAS las versiones, antes de filtrar: si la última versión baja de impacto
    # o pierde la hora exacta, la anterior deja de valer igualmente.
    df = df.assign(_siguiente=df.groupby("_clave")["ts_version"].shift(-1))
    minimo = _ORDEN_IMPACTO[impacto_min]
    rango = df["impacto"].map(_ORDEN_IMPACTO).fillna(-1).astype(int)
    df = df[(rango >= minimo) & df["hora_exacta"].fillna(False).astype(bool)]
    if divisas:
        df = df[df["divisa"].isin(divisas)]
    df = df.reset_index(drop=True)
    sin_siguiente = df["_siguiente"].isna().to_numpy()
    fin_version = np.where(sin_siguiente, _SIN_FIN,
                           _ns(df["_siguiente"].fillna(df["ts_version"])))
    impacto = df["impacto"].map(_ORDEN_IMPACTO).to_numpy()
    tiempos = _ns(base["ts_cierre"])
    tope = _SIN_FIN if limite is None else int(pd.Timestamp(limite).as_unit("ns").value)
    # Sin ts_anunciado (o sin ts_conocido) nunca se ve: mejor tarde que ver el futuro.
    anunciado = np.where(df["ts_anunciado"].isna().to_numpy(), _SIN_FIN,
                         np.maximum(_ns(df["ts_version"]), _ns(df["ts_anunciado"])))
    evento_ns = _ns(df["ts_evento"])

    # Próximo evento: anunciado, aún no ocurrido, en su versión vigente.
    desde = np.where(anunciado <= tope, anunciado, _SIN_FIN)
    hasta = np.minimum(fin_version, evento_ns)
    prio = list(zip(evento_ns, -impacto))
    i_prox = _barrido(desde, hasta, prio, tiempos)

    # Último evento con dato real conocido (ts_conocido + latencia), en su versión vigente.
    con_real = (df["real"].notna() & df["ts_conocido"].notna()).to_numpy()
    real_ns = _ns(df["ts_conocido"] + lat)
    desde = np.maximum(anunciado, real_ns)
    desde = np.where(con_real & (desde <= tope), desde, _SIN_FIN)
    prio = list(zip(-real_ns, -impacto))
    i_ult = _barrido(desde, fin_version, prio, tiempos)

    prev, _ = _elegir_prevision(df, fotos, prevision, None)
    sorpresa = (df["real"] - prev).to_numpy()
    nombres = df["evento"].to_numpy(dtype=object)
    cierre = tiempos.astype("float64")
    hay_p, hay_u = i_prox >= 0, i_ult >= 0
    minuto = 60e9
    return base.assign(
        min_hasta_proximo=np.where(hay_p, (evento_ns[i_prox] - cierre) / minuto, np.nan),
        proximo_evento=np.where(hay_p, nombres[i_prox], None),
        min_desde_ultimo=np.where(hay_u, (cierre - evento_ns[i_ult]) / minuto, np.nan),
        sorpresa_ultimo=np.where(hay_u, sorpresa[i_ult], np.nan),
    )


def unir_contexto(precios: pd.DataFrame, tipos: dict[str, list[str]],
                  latencia: str | timedelta | None = None,
                  prevision: Literal["ff", "api", "mejor"] = "mejor",
                  impacto_min: Literal["bajo", "medio", "alto"] = "alto",
                  divisas: list[str] | None = None, incluir_reservado: bool = False,
                  cfg: Config | None = None) -> pd.DataFrame:
    """Pega a cada vela el contexto conocido en su `ts_cierre` (nunca uno posterior).

    `tipos` dice qué pegar: {'serie': [ids], 'cot': [mercados], 'etf': [etfs],
    'calendario': [fuentes]}; una lista vacía es "todas".
    - serie: una columna por serie (`<id>`); cot: `cot_<mercado>_<campo>` con los campos de
      contexto.json (si un campo está en varios informes, manda legacy); etf:
      `<etf>_toneladas` y `<etf>_valor_usd`. Valor = el del periodo más reciente ya
      publicado, en la versión vigente en el cierre.
    - calendario (eventos con hora exacta, impacto ≥ `impacto_min`, divisas `divisas` o
      todas): `min_hasta_proximo` y `proximo_evento` (próximo evento ya anunciado),
      `min_desde_ultimo` y `sorpresa_ultimo` (último evento con dato real conocido).
    - Reserva: sin `incluir_reservado` no se pega nada conocido después del corte.
    """
    if "ts_cierre" not in precios.columns:
        raise ValueError("`precios` debe tener la columna ts_cierre")
    tipo_ts = precios["ts_cierre"].dtype
    if not isinstance(tipo_ts, pd.DatetimeTZDtype) or str(tipo_ts.tz) not in ("UTC", "+00:00"):
        raise ValueError(f"ts_cierre debe ser un instante con zona UTC, no {tipo_ts}")
    desconocidos = set(tipos) - {"serie", "cot", "etf", "calendario"}
    if desconocidos:
        raise ValueError(f"Tipos que unir_contexto no sabe pegar: {sorted(desconocidos)}")
    if prevision not in _PREVISIONES:
        raise ValueError(f"prevision debe ser 'ff', 'api' o 'mejor', no {prevision!r}")
    if impacto_min not in _ORDEN_IMPACTO:
        raise ValueError(f"impacto_min debe ser bajo, medio o alto, no {impacto_min!r}")
    cfg = cfg or cargar_config()
    lat = _latencia(latencia)
    if precios.empty:
        return precios.copy()

    base = precios.assign(_orden=np.arange(len(precios))).sort_values("ts_cierre", kind="stable")
    primero, ultimo = base["ts_cierre"].iloc[0], base["ts_cierre"].iloc[-1]
    limite = _aplicar_reserva(cfg, "unir:" + ",".join(sorted(tipos)), primero.to_pydatetime(),
                              (ultimo + pd.Timedelta(microseconds=1)).to_pydatetime(),
                              incluir_reservado)
    limite = None if limite is None else pd.Timestamp(limite)
    anios = range(primero.year - 1, ultimo.year + 2)
    for tipo, claves in tipos.items():
        if tipo == "serie":
            base = _unir_serie(base, cfg, claves, lat, limite)
        elif tipo == "cot":
            base = _unir_cot(base, cfg, claves, lat, limite, anios)
        elif tipo == "etf":
            base = _unir_etf(base, cfg, claves, lat, limite)
        else:
            base = _unir_calendario(base, cfg, claves, lat, prevision, impacto_min, divisas,
                                    limite, anios)
    base = base.sort_values("_orden", kind="stable").drop(columns="_orden")
    base.index = precios.index
    return base
