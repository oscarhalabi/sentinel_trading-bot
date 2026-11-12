"""Pruebas de la consulta: sin ver el futuro, con reserva y sin Postgres."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from datos import consulta
from datos.almacen import escribir
from datos.config import cargar_config
from datos.consulta import ReservaBloqueada, cargar, cargar_varios, corte_reserva, ticks
from tests.datos.ayudas import m1_sintetica, ticks_sinteticos

UN_MIN = pd.Timedelta(minutes=1)


def _t(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, corte: str | None = None):
    entorno = {"DATA_DIR": str(tmp_path)}
    if corte:
        entorno["DATOS_CORTE_RESERVA"] = corte
    return cargar_config(entorno)


def _guardar_m1(raiz: Path, tabla: pa.Table, simbolo: str = "XAUUSD") -> None:
    """Escribe la M1 partida por meses, como lo hace el almacén."""
    df = tabla.to_pandas()
    for (anio, mes), _ in df.groupby([df["ts"].dt.year, df["ts"].dt.month]):
        ini = pd.Timestamp(anio, mes, 1, tz="UTC")
        fin = ini + pd.offsets.MonthBegin(1)
        trozo = tabla.filter(pa.compute.and_(
            pa.compute.greater_equal(tabla["ts"], pa.scalar(ini.to_pydatetime(), tabla["ts"].type)),
            pa.compute.less(tabla["ts"], pa.scalar(fin.to_pydatetime(), tabla["ts"].type))))
        escribir(raiz, trozo, "m1", "dukascopy", simbolo, date(anio, mes, 1))


def _m1_con_precios(inicio: datetime, precios: list[float]) -> pa.Table:
    """M1 donde cada minuto tiene open=close=p, high=p+1, low=p-1."""
    tabla = m1_sintetica(inicio, len(precios))
    p = pa.array(precios, type=pa.float64())
    tabla = tabla.set_column(tabla.schema.get_field_index("open"), "open", p)
    tabla = tabla.set_column(tabla.schema.get_field_index("close"), "close", p)
    tabla = tabla.set_column(tabla.schema.get_field_index("high"), "high",
                             pa.compute.add(p, 1.0))
    tabla = tabla.set_column(tabla.schema.get_field_index("low"), "low",
                             pa.compute.subtract(p, 1.0))
    return tabla


def test_ts_cierre_m1(tmp_path):
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4, 10), 30))
    df = cargar("XAUUSD", date(2024, 3, 4), date(2024, 3, 5), cfg=_cfg(tmp_path))
    assert len(df) == 30
    assert ((df["ts_cierre"] - df["ts"]) == UN_MIN).all()
    assert str(df["ts"].dt.tz) == "UTC"


def test_h1_desde_m1(tmp_path):
    precios = [float(i) for i in range(1, 121)]
    _guardar_m1(tmp_path, _m1_con_precios(_t(2024, 3, 4, 10), precios))
    df = cargar("XAUUSD", date(2024, 3, 4), date(2024, 3, 5), tf="h1", cfg=_cfg(tmp_path))
    assert len(df) == 2
    assert list(df["ts"]) == [pd.Timestamp(_t(2024, 3, 4, 10)), pd.Timestamp(_t(2024, 3, 4, 11))]
    assert ((df["ts_cierre"] - df["ts"]) == pd.Timedelta(hours=1)).all()
    assert list(df["open"]) == [1.0, 61.0]
    assert list(df["close"]) == [60.0, 120.0]
    assert list(df["high"]) == [61.0, 121.0]
    assert list(df["low"]) == [0.0, 60.0]
    assert list(df["ticks"]) == [600, 600]


def test_h1_no_devuelve_vela_que_empieza_antes_de_desde(tmp_path):
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4, 10), 120))
    df = cargar("XAUUSD", _t(2024, 3, 4, 10, 30), date(2024, 3, 5), tf="h1", cfg=_cfg(tmp_path))
    assert list(df["ts"]) == [pd.Timestamp(_t(2024, 3, 4, 11))]


def test_d1_corte_nueva_york_en_cambio_de_horario(tmp_path):
    inicio = _t(2024, 3, 8)
    n = int((_t(2024, 3, 12) - inicio).total_seconds() // 60)
    _guardar_m1(tmp_path, m1_sintetica(inicio, n))
    df = cargar("XAUUSD", date(2024, 3, 7), date(2024, 3, 13), tf="d1", cfg=_cfg(tmp_path))
    inicios = {t.date(): t.hour for t in df["ts"]}
    # Antes del cambio (EST, UTC-5) la vela empieza a las 22:00 UTC.
    assert inicios[date(2024, 3, 8)] == 22
    # Después del cambio (EDT, UTC-4) empieza a las 21:00 UTC.
    assert inicios[date(2024, 3, 10)] == 21
    assert inicios[date(2024, 3, 11)] == 21
    # La vela que abre el 8-mar a las 22:00 UTC cierra el 9-mar a las 22:00 UTC.
    fila = df[df["ts"] == pd.Timestamp(_t(2024, 3, 8, 22))].iloc[0]
    assert fila["ts_cierre"] == pd.Timestamp(_t(2024, 3, 9, 22))
    # La vela del día 10 (de sábado 17:00 a domingo 17:00 en NY) dura 23 h por el cambio.
    fila = df[df["ts"] == pd.Timestamp(_t(2024, 3, 9, 22))].iloc[0]
    assert fila["ts_cierre"] == pd.Timestamp(_t(2024, 3, 10, 21))
    assert fila["ticks"] == 23 * 60 * 10


def test_d1_corte_utc(tmp_path):
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4), 2 * 24 * 60))
    df = cargar("XAUUSD", date(2024, 3, 4), date(2024, 3, 6), tf="d1", corte_diario="utc",
                cfg=_cfg(tmp_path))
    assert list(df["ts"]) == [pd.Timestamp(_t(2024, 3, 4)), pd.Timestamp(_t(2024, 3, 5))]
    assert ((df["ts_cierre"] - df["ts"]) == pd.Timedelta(days=1)).all()


def test_corte_reserva_por_defecto_y_fijo(tmp_path):
    assert corte_reserva(_cfg(tmp_path), hoy=date(2026, 11, 12)) == _t(2025, 11, 12)
    assert corte_reserva(_cfg(tmp_path, "2025-01-01"), hoy=date(2026, 11, 12)) == _t(2025, 1, 1)


def test_reserva_bloquea_por_defecto(tmp_path):
    cfg = _cfg(tmp_path, "2025-01-01")
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 12, 31, 22), 4 * 60))
    df = cargar("XAUUSD", date(2024, 12, 1), date(2025, 6, 1), cfg=cfg)
    assert len(df) == 120
    assert (df["ts"] < pd.Timestamp(_t(2025, 1, 1))).all()
    assert (df["ts_cierre"] <= pd.Timestamp(_t(2025, 1, 1))).all()
    # Las velas agregadas que cierran después del corte tampoco salen.
    d1 = cargar("XAUUSD", date(2024, 12, 1), date(2025, 6, 1), tf="d1", corte_diario="utc", cfg=cfg)
    assert d1.empty or (d1["ts_cierre"] <= pd.Timestamp(_t(2025, 1, 1))).all()
    with pytest.raises(ReservaBloqueada):
        cargar("XAUUSD", date(2025, 1, 1), date(2025, 6, 1), cfg=cfg)
    with pytest.raises(ReservaBloqueada):
        ticks("XAUUSD", date(2025, 2, 1), date(2025, 3, 1), cfg=cfg)
    # Nada de esto deja registro.
    assert not (tmp_path / "_registro" / "reserva.jsonl").exists()


def test_acceso_a_reserva_queda_registrado(tmp_path):
    cfg = _cfg(tmp_path, "2025-01-01")
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 12, 31, 22), 4 * 60))
    registro = tmp_path / "_registro" / "reserva.jsonl"
    df = cargar("XAUUSD", date(2024, 12, 1), date(2025, 6, 1), incluir_reservado=True, cfg=cfg)
    assert len(df) == 240
    lineas = registro.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 1
    cargar("XAUUSD", date(2025, 1, 1), date(2025, 2, 1), incluir_reservado=True, cfg=cfg)
    lineas = registro.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    linea = json.loads(lineas[0])
    assert set(linea) == {"momento", "quien", "simbolo", "desde", "hasta"}
    assert linea["simbolo"] == "XAUUSD"
    assert datetime.fromisoformat(linea["desde"]) == _t(2024, 12, 1)
    assert datetime.fromisoformat(linea["hasta"]) == _t(2025, 6, 1)
    assert datetime.fromisoformat(linea["momento"]).tzinfo is not None
    assert ":" in linea["quien"]


def test_ticks_respeta_reserva_y_lector_interno_no_registra(tmp_path):
    cfg = _cfg(tmp_path, "2025-01-01")
    for dia in (date(2024, 12, 31), date(2025, 1, 1)):
        escribir(tmp_path, ticks_sinteticos(datetime(dia.year, dia.month, dia.day, 10), 20),
                 "ticks", "dukascopy", "XAUUSD", dia)
    df = ticks("XAUUSD", date(2024, 12, 31), date(2025, 1, 2), cfg=cfg)
    assert len(df) == 20 and (df["ts"] < pd.Timestamp(_t(2025, 1, 1))).all()
    assert list(df.columns)[:3] == ["ts", "bid", "ask"]
    todo = consulta._leer_ticks_sin_registro(cfg, "XAUUSD", date(2024, 12, 31), date(2025, 1, 2))
    assert len(todo) == 40
    assert not (tmp_path / "_registro").exists()


def test_cargar_varios_nunca_usa_dato_posterior(tmp_path):
    cfg = _cfg(tmp_path)
    # A cada minuto desde las 10:00; B cada 3 minutos desde las 10:01 (desfasada).
    _guardar_m1(tmp_path, _m1_con_precios(_t(2024, 3, 4, 10), [float(i) for i in range(30)]), "AAA")
    b = m1_sintetica(_t(2024, 3, 4, 10, 1), 30)
    b = b.filter(pa.array([i % 3 == 0 for i in range(30)]))
    _guardar_m1(tmp_path, b, "BBB")
    df = cargar_varios(["AAA", "BBB"], date(2024, 3, 4), date(2024, 3, 5), tf="m1",
                       rellenar="ultimo_conocido", cfg=cfg)
    assert set(df["simbolo"]) == {"AAA", "BBB"}
    assert "relleno" in df.columns
    # El dato usado (abre en ts, cierra en ts + 1 min) nunca cierra después de la fila.
    assert ((df["ts"] + UN_MIN) <= df["ts_cierre"]).all()
    # Orden por (ts_cierre, simbolo).
    ordenado = df.sort_values(["ts_cierre", "simbolo"], kind="stable").reset_index(drop=True)
    pd.testing.assert_frame_equal(df.reset_index(drop=True), ordenado)
    # B tiene filas rellenas y su valor viene de la última vela cerrada.
    bb = df[df["simbolo"] == "BBB"]
    assert bb["relleno"].any()
    assert not bb[~bb["relleno"]]["ts_cierre"].ne(bb[~bb["relleno"]]["ts"] + UN_MIN).any()
    # La rejilla de B es la unión de los ts_cierre (desde su primer dato).
    rejilla = sorted(df["ts_cierre"].unique())
    assert list(bb["ts_cierre"]) == [t for t in rejilla if t >= bb["ts_cierre"].min()]


def test_cargar_varios_sin_relleno_es_formato_largo(tmp_path):
    cfg = _cfg(tmp_path)
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4, 10), 5), "AAA")
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4, 10, 2), 5), "BBB")
    df = cargar_varios(["AAA", "BBB"], date(2024, 3, 4), date(2024, 3, 5), tf="m1", cfg=cfg)
    assert len(df) == 10 and "relleno" not in df.columns
    assert list(df.columns[:2]) == ["simbolo", "ts_cierre"]


def test_conectar_vistas(tmp_path):
    cfg = _cfg(tmp_path)
    con = consulta.conectar(cfg)
    try:
        assert con.execute("select count(*) from ticks").fetchone()[0] == 0
        assert con.execute("select count(*) from m1").fetchone()[0] == 0
    finally:
        con.close()
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4, 10), 7))
    con = consulta.conectar(cfg)
    try:
        assert con.execute(
            "select count(*) from m1 where simbolo = 'XAUUSD' and fuente = 'dukascopy'"
        ).fetchone()[0] == 7
    finally:
        con.close()


def test_consulta_no_necesita_postgres(tmp_path, monkeypatch):
    import psycopg2

    def _prohibido(*a, **k):
        raise AssertionError("la consulta no debe conectar a Postgres")

    monkeypatch.setattr(psycopg2, "connect", _prohibido)
    monkeypatch.delenv("DATOS_PG_DSN", raising=False)
    cfg = _cfg(tmp_path)
    assert cfg.pg_dsn == ""
    _guardar_m1(tmp_path, m1_sintetica(_t(2024, 3, 4, 10), 10))
    assert len(cargar("XAUUSD", date(2024, 3, 4), date(2024, 3, 5), tf="m5", cfg=cfg)) == 2
