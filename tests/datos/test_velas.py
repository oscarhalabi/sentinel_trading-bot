"""Pruebas de las velas M1 (sin red ni Postgres)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from datos.__main__ import main
from datos.almacen import ESQUEMA_M1, ESQUEMA_TICKS, escribir, leer_manifiesto, marcar_vacio
from datos.config import Config
from datos.velas import construir_m1, velas_de_ticks
from tests.datos.ayudas import ticks_sinteticos


def _cfg(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, pg_dsn="", disco_min_gb=0, corte_reserva=None)


def _t(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _tabla(filas):
    """filas: (ts, bid, ask, bid_vol, ask_vol)."""
    cols = list(zip(*filas))
    return pa.table({
        "ts": pa.array(cols[0], type=pa.timestamp("us", tz="UTC")),
        "bid": pa.array(cols[1], type=pa.float64()),
        "ask": pa.array(cols[2], type=pa.float64()),
        "bid_vol": pa.array(cols[3], type=pa.float32()),
        "ask_vol": pa.array(cols[4], type=pa.float32()),
    }, schema=ESQUEMA_TICKS)


def test_m1_ohlc_y_spread():
    # Desordenados a propósito: open/close se toman por ts, no por posición.
    t = _tabla([
        (_t(2026, 1, 5, 10, 0, 40), 2001.0, 2001.5, 2.0, 3.0),
        (_t(2026, 1, 5, 10, 0, 10), 2000.0, 2000.2, 1.0, 1.0),
        (_t(2026, 1, 5, 10, 0, 20), 2003.0, 2003.4, 1.0, 1.0),
        (_t(2026, 1, 5, 10, 1, 5), 2002.0, 2002.2, 4.0, 4.0),
        (_t(2026, 1, 5, 10, 1, 15), 1999.0, 1999.6, 0.5, 0.5),
        (_t(2026, 1, 5, 10, 1, 50), 2000.5, 2000.6, 1.0, 2.0),
    ])
    r = velas_de_ticks(t)
    assert r.schema.equals(ESQUEMA_M1)
    f = r.to_pylist()
    assert len(f) == 2
    a, b = f
    assert a["ts"] == _t(2026, 1, 5, 10, 0)
    assert (a["open"], a["high"], a["low"], a["close"]) == (2000.0, 2003.0, 2000.0, 2001.0)
    assert a["ask_close"] == 2001.5
    assert a["spread_min"] == pytest.approx(0.2)
    assert a["spread_max"] == pytest.approx(0.5)
    assert a["spread_medio"] == pytest.approx((0.2 + 0.4 + 0.5) / 3)
    assert a["ticks"] == 3 and a["bid_vol"] == 4.0 and a["ask_vol"] == 5.0
    assert b["ts"] == _t(2026, 1, 5, 10, 1)
    assert (b["open"], b["high"], b["low"], b["close"]) == (2002.0, 2002.0, 1999.0, 2000.5)
    assert b["ask_close"] == 2000.6
    assert b["ticks"] == 3


def test_minuto_sin_ticks_no_existe():
    t = pa.concat_tables([
        ticks_sinteticos(_t(2026, 1, 5, 10, 0), 3),
        ticks_sinteticos(_t(2026, 1, 5, 10, 3), 3),
    ])
    r = velas_de_ticks(t)
    assert [x.as_py() for x in r["ts"]] == [_t(2026, 1, 5, 10, 0), _t(2026, 1, 5, 10, 3)]


def _dia(cfg, dia: date, precio=2000.0):
    escribir(cfg.data_dir, ticks_sinteticos(_t(dia.year, dia.month, dia.day, 10, 0), 5, precio),
             "ticks", "dukascopy", "XAUUSD", dia)


def test_m1_solo_rehace_meses_cambiados(tmp_path):
    cfg = _cfg(tmp_path)
    _dia(cfg, date(2026, 1, 5))
    _dia(cfg, date(2026, 2, 3))
    assert construir_m1(cfg) == 2
    assert construir_m1(cfg) == 0
    _dia(cfg, date(2026, 1, 5), precio=2010.0)
    assert construir_m1(cfg) == 1
    m = leer_manifiesto(tmp_path, "m1/fuente=dukascopy/simbolo=XAUUSD/anio=2026/2026-01.parquet")
    assert m.periodo == "2026-01" and len(m.origen) == 1
    assert construir_m1(cfg) == 0


def test_m1_incluye_dias_de_dos_meses(tmp_path):
    cfg = _cfg(tmp_path)
    _dia(cfg, date(2026, 1, 31))
    _dia(cfg, date(2026, 2, 1))
    assert construir_m1(cfg) == 2
    for mes in ("2026-01", "2026-02"):
        p = tmp_path / f"m1/fuente=dukascopy/simbolo=XAUUSD/anio=2026/{mes}.parquet"
        assert pq.read_table(p).num_rows == 1


def test_m1_ignora_dias_vacios_y_filtra(tmp_path):
    cfg = _cfg(tmp_path)
    marcar_vacio(tmp_path, "ticks", "dukascopy", "XAUUSD", date(2026, 3, 2))
    _dia(cfg, date(2026, 1, 5))
    _dia(cfg, date(2026, 2, 3))
    assert construir_m1(cfg, desde=date(2026, 2, 1)) == 1
    assert construir_m1(cfg, simbolos=["EURUSD"]) == 0
    assert construir_m1(cfg) == 1  # enero; marzo solo tiene un día vacío


def test_cli_velas(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _dia(cfg, date(2026, 1, 5))
    assert main(["velas", "--simbolo", "XAUUSD", "--desde", "2026-01-01"], config=cfg) == 0
    assert "1" in capsys.readouterr().out
