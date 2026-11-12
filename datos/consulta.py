"""Consulta de datos de mercado para investigación y laboratorio.

Garantías:
- Cada vela lleva `ts` (apertura) y `ts_cierre`. Una vela solo "existe" a partir de su
  `ts_cierre`: así nadie usa un dato antes de que se conozca (sin ver el futuro).
- Al juntar varios mercados, cada instante toma la última vela YA CERRADA de cada uno.
- Los últimos 12 meses están en reserva: se recortan salvo `incluir_reservado=True`, y
  cada acceso explícito se apunta en `DATA_DIR/_registro/reserva.jsonl`.

Lee los Parquet directamente con DuckDB. No necesita Postgres.
"""
from __future__ import annotations

import getpass
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

from datos.almacen import ESQUEMA_M1, ESQUEMA_TICKS
from datos.config import Config, cargar_config

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
                   "m1": f" WHERE ts < {corte} AND ts + INTERVAL 1 MINUTE <= {corte}"}
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
