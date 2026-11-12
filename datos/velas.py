"""Velas M1 a partir de los ticks guardados: un Parquet por (fuente, símbolo, mes)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import ESQUEMA_M1, escribir, leer_manifiesto, listar_manifiestos, ruta_relativa
from datos.config import Config

_SQL = """
SELECT
    date_trunc('minute', ts) AS ts,
    arg_min(bid, ts) AS open,
    max(bid) AS high,
    min(bid) AS low,
    arg_max(bid, ts) AS close,
    arg_max(ask, ts) AS ask_close,
    min(ask - bid) AS spread_min,
    avg(ask - bid) AS spread_medio,
    max(ask - bid) AS spread_max,
    count(*) AS ticks,
    sum(bid_vol) AS bid_vol,
    sum(ask_vol) AS ask_vol
FROM ticks
GROUP BY 1
ORDER BY 1
"""


def velas_de_ticks(tabla_ticks: pa.Table) -> pa.Table:
    """Agrupa ticks en velas de 1 minuto. Solo salen los minutos que tienen ticks."""
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC'")
        con.register("ticks", tabla_ticks)
        resultado = con.sql(_SQL).arrow()
    finally:
        con.close()
    if hasattr(resultado, "read_all"):  # según la versión, devuelve un lector
        resultado = resultado.read_all()
    return resultado.select(ESQUEMA_M1.names).cast(ESQUEMA_M1)


def _meses_de_ticks(cfg: Config, fuente: str, simbolos: list[str] | None,
                    desde: date | None) -> dict[tuple[str, str], dict[str, str]]:
    """(símbolo, 'AAAA-MM') -> {ruta de ticks: sha256}, solo de días `ok`."""
    meses: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for m in listar_manifiestos(cfg.data_dir, "ticks", fuente):
        if m.estado != "ok" or not m.sha256:
            continue
        if simbolos and m.simbolo not in simbolos:
            continue
        mes = m.periodo[:7]
        if desde and mes < f"{desde:%Y-%m}":
            continue
        meses[(m.simbolo, mes)][m.ruta] = m.sha256
    return meses


def construir_m1(cfg: Config, fuente: str = "dukascopy", simbolos: list[str] | None = None,
                 desde: date | None = None) -> int:
    """Rehace las M1 de los meses cuyos ticks cambiaron. Devuelve los meses escritos."""
    escritos = 0
    meses = _meses_de_ticks(cfg, fuente, simbolos, desde)
    for (simbolo, mes), origen in sorted(meses.items()):
        periodo = date.fromisoformat(mes + "-01")
        previo = leer_manifiesto(cfg.data_dir, str(ruta_relativa("m1", fuente, simbolo, periodo)))
        if previo and previo.estado == "ok" and previo.origen == origen:
            continue
        tablas = [pq.read_table(Path(cfg.data_dir) / ruta) for ruta in sorted(origen)]
        ticks = pa.concat_tables(tablas)
        escribir(cfg.data_dir, velas_de_ticks(ticks), "m1", fuente, simbolo, periodo, origen)
        escritos += 1
    return escritos
