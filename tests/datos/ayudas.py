"""Ayudas compartidas por las pruebas de datos."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa

from datos.almacen import ESQUEMA_M1, ESQUEMA_TICKS


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ticks_sinteticos(inicio: datetime, n: int, precio: float = 2000.0,
                     paso_ms: int = 500) -> pa.Table:
    """Ticks de prueba con el esquema de ticks (ask = bid + 0.2)."""
    inicio = _utc(inicio)
    ts = [inicio + timedelta(milliseconds=paso_ms * i) for i in range(n)]
    return pa.table({
        "ts": pa.array(ts, type=pa.timestamp("us", tz="UTC")),
        "bid": pa.array([precio] * n, type=pa.float64()),
        "ask": pa.array([precio + 0.2] * n, type=pa.float64()),
        "bid_vol": pa.array([1.0] * n, type=pa.float32()),
        "ask_vol": pa.array([1.0] * n, type=pa.float32()),
    }, schema=ESQUEMA_TICKS)


def m1_sintetica(inicio: datetime, n_minutos: int, precio: float = 2000.0) -> pa.Table:
    """Velas M1 de prueba con el esquema M1."""
    inicio = _utc(inicio)
    n = n_minutos
    ts = [inicio + timedelta(minutes=i) for i in range(n)]
    f = lambda v: pa.array([v] * n, type=pa.float64())  # noqa: E731
    return pa.table({
        "ts": pa.array(ts, type=pa.timestamp("us", tz="UTC")),
        "open": f(precio), "high": f(precio + 0.5), "low": f(precio - 0.5),
        "close": f(precio), "ask_close": f(precio + 0.2),
        "spread_min": f(0.2), "spread_medio": f(0.2), "spread_max": f(0.2),
        "ticks": pa.array([10] * n, type=pa.int32()),
        "bid_vol": f(10.0), "ask_vol": f(10.0),
    }, schema=ESQUEMA_M1)
