"""Pruebas de la verificación de calidad (sin red ni Postgres)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from datos.__main__ import main
from datos.almacen import ESQUEMA_TICKS, escribir
from datos.calidad import (Problema, chequear_coherencia, chequear_conteo, chequear_huecos,
                           chequear_saltos, chequear_split, escribir_informe, verificar)
from datos.config import Config
from datos.simbolos import Simbolo


def _sim(nombre="XAUUSD", clase="metal", horario="metal"):
    return Simbolo(nombre, clase, 1, {"dukascopy": nombre}, 3, horario, "USD")


def _cfg(tmp_path: Path, dsn="") -> Config:
    return Config(data_dir=tmp_path, pg_dsn=dsn, disco_min_gb=0, corte_reserva=None)


def _t(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _df(ts, bid=None, ask=None):
    n = len(ts)
    bid = np.full(n, 2000.0) if bid is None else np.asarray(bid, dtype=float)
    ask = bid + 0.3 if ask is None else np.asarray(ask, dtype=float)
    return pd.DataFrame({"ts": pd.to_datetime(list(ts), utc=True), "bid": bid, "ask": ask,
                         "bid_vol": 1.0, "ask_vol": 1.0})


def _cada_minuto(dia: date, h_ini=0, h_fin=24, paso_min=1):
    base = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)
    ts, t = [], base + timedelta(hours=h_ini)
    while t < base + timedelta(hours=h_fin):
        ts.append(t)
        t += timedelta(minutes=paso_min)
    return ts


# --- huecos ---

def test_domingo_parcial_no_es_hueco():
    dia = date(2024, 5, 12)
    t = _df(_cada_minuto(dia, 22, 24))
    assert chequear_huecos(t, _sim(), dia) == []


def test_detecta_hueco_en_horario_abierto():
    dia = date(2024, 5, 14)  # martes
    ts = [x for x in _cada_minuto(dia) if not (10 <= x.hour < 14)]
    r = chequear_huecos(_df(ts), _sim(), dia)
    assert len(r) == 1
    p = r[0]
    assert p.tipo == "hueco" and p.gravedad == "grave"
    assert p.desde == _t(2024, 5, 14, 10) and p.hasta == _t(2024, 5, 14, 14)


def test_hueco_corto_es_aviso():
    dia = date(2024, 5, 14)
    ts = [x for x in _cada_minuto(dia) if not (10 <= x.hour < 12)]
    r = chequear_huecos(_df(ts), _sim(), dia)
    assert [p.gravedad for p in r] == ["aviso"]


def test_fin_de_semana_no_es_hueco():
    dia = date(2024, 5, 18)  # sábado
    assert chequear_huecos(_df([]), _sim(), dia) == []


def test_feriado_fijo_no_es_hueco():
    assert chequear_huecos(_df([]), _sim(), date(2024, 12, 25)) == []


def test_accion_us_fuera_de_sesion_no_es_hueco():
    dia = date(2024, 5, 14)
    sim = _sim("AAPL", "accion", "accion_us")
    ts = [x for x in _cada_minuto(dia, 14, 20)]  # 10:00-16:00 NY
    assert chequear_huecos(_df(ts), sim, dia) == []


# --- coherencia ---

def test_bid_mayor_que_ask_es_grave():
    ts = _cada_minuto(date(2024, 5, 14), 0, 1, 10)
    t = _df(ts, bid=[2000, 2001, 2000, 2000, 2000, 2000], ask=[2000.3, 2000.0, 2000.3] + [2000.3] * 3)
    r = chequear_coherencia(t, "XAUUSD")
    assert len(r) == 1 and r[0].gravedad == "grave" and r[0].tipo == "coherencia"
    assert r[0].desde == ts[1]


def test_precio_cero_es_grave():
    t = _df(_cada_minuto(date(2024, 5, 14), 0, 1, 20), bid=[0.0, 1, 1], ask=[0.0, 1.1, 1.1])
    r = chequear_coherencia(t, "XAUUSD")
    assert r and r[0].gravedad == "grave"


def test_duplicados_y_desorden():
    ts = _cada_minuto(date(2024, 5, 14), 0, 1, 10)
    t = _df([ts[0], ts[2], ts[1], ts[1], ts[3]])
    r = chequear_coherencia(t, "XAUUSD")
    assert len(r) == 1 and r[0].gravedad == "grave"
    assert set(r[0].detalle["motivos"]) == {"ts_no_creciente", "duplicados"}


def test_tabla_sana_sin_problemas():
    t = _df(_cada_minuto(date(2024, 5, 14), 0, 1, 10))
    assert chequear_coherencia(t, "XAUUSD") == []


# --- saltos ---

def _serie_con_ruido(n=400):
    rng = np.random.default_rng(1)
    return 2000 + np.cumsum(rng.normal(0, 0.1, n))


def test_salto_que_revierte_se_marca():
    bid = _serie_con_ruido()
    bid[300] += 5.0  # un solo tick disparado que vuelve
    ts = [_t(2024, 5, 14, 10) + timedelta(seconds=i) for i in range(len(bid))]
    r = chequear_saltos(_df(ts, bid=bid), "XAUUSD")
    assert len(r) == 1 and r[0].tipo == "salto" and r[0].gravedad == "aviso"
    assert r[0].desde == ts[300]


def test_tendencia_fuerte_no_es_salto():
    bid = _serie_con_ruido()
    bid[300:] += 5.0  # cambio de nivel que se queda
    ts = [_t(2024, 5, 14, 10) + timedelta(seconds=i) for i in range(len(bid))]
    assert chequear_saltos(_df(ts, bid=bid), "XAUUSD") == []


# --- conteo ---

def test_dia_con_pocos_ticks():
    conteos = {date(2024, 5, d): 1000 for d in range(6, 12)}
    conteos[date(2024, 5, 12)] = 100
    r = chequear_conteo(conteos, "XAUUSD", "dukascopy")
    assert len(r) == 1 and r[0].tipo == "conteo" and r[0].gravedad == "aviso"
    assert r[0].desde == _t(2024, 5, 12)


def test_conteo_con_pocos_dias_no_juzga():
    assert chequear_conteo({date(2024, 5, 6): 1000, date(2024, 5, 7): 10}, "X", "dukascopy") == []


# --- split ---

def _cierres(apertura, cierre_ant):
    return pd.DataFrame({"apertura": [apertura], "cierre_anterior": [cierre_ant]},
                        index=[date(2024, 5, 14)])


def test_posible_split_2_a_1():
    r = chequear_split(_cierres(50.2, 100.0), "AAPL", "dukascopy")
    assert len(r) == 1 and r[0].tipo == "posible_split" and r[0].gravedad == "aviso"


def test_caida_normal_no_es_split():
    assert chequear_split(_cierres(93.0, 100.0), "AAPL", "dukascopy") == []


# --- verificar / informe ---

class CatalogoFalso:
    def __init__(self):
        self.vistos = set()

    def anotar_problema(self, tipo, simbolo, fuente, desde, hasta, gravedad, detalle):
        clave = (tipo, simbolo, fuente, desde)
        if clave in self.vistos:
            return False
        self.vistos.add(clave)
        return True


def _guardar_dia(tmp_path, dia, ts):
    n = len(ts)
    tabla = pa.table({
        "ts": pa.array(ts, type=pa.timestamp("us", tz="UTC")),
        "bid": pa.array([2000.0] * n), "ask": pa.array([2000.3] * n),
        "bid_vol": pa.array([1.0] * n, type=pa.float32()),
        "ask_vol": pa.array([1.0] * n, type=pa.float32()),
    }, schema=ESQUEMA_TICKS)
    escribir(tmp_path, tabla, "ticks", "dukascopy", "XAUUSD", dia)


def test_verificar_no_duplica_problemas(tmp_path):
    dia = date(2024, 5, 14)
    _guardar_dia(tmp_path, dia, [x for x in _cada_minuto(dia) if not (10 <= x.hour < 14)])
    cfg, cat = _cfg(tmp_path), CatalogoFalso()
    primera = verificar(cfg, [_sim()], cat)
    assert [p.tipo for p in primera] == ["hueco"]
    assert verificar(cfg, [_sim()], cat) == []
    # sin catálogo todo cuenta como nuevo
    assert len(verificar(cfg, [_sim()], None)) == 1


def test_verificar_respeta_desde(tmp_path):
    dia = date(2024, 5, 14)
    _guardar_dia(tmp_path, dia, [x for x in _cada_minuto(dia) if not (10 <= x.hour < 14)])
    assert verificar(_cfg(tmp_path), [_sim()], None, desde=date(2024, 5, 15)) == []


def test_informe_no_contiene_dsn(tmp_path):
    cfg = _cfg(tmp_path, "postgresql://u:secreto@h/db")
    p = Problema("hueco", "XAUUSD", "dukascopy", _t(2024, 5, 14, 10), _t(2024, 5, 14, 14),
                 "grave", {"horas": 4})
    ruta = escribir_informe(cfg, [p], None)
    html = ruta.read_text(encoding="utf-8")
    assert "secreto" not in html
    assert ruta.parent.name == "_informes" and ruta.suffix == ".html"
    assert "XAUUSD" in html and "grave" in html


def test_informe_secciones_y_tamano(tmp_path, monkeypatch):
    dia = date(2024, 5, 14)
    _guardar_dia(tmp_path, dia, _cada_minuto(dia, 0, 2))
    monkeypatch.setattr("datos.calidad._registro", lambda: {"XAUUSD": _sim()})
    html = escribir_informe(_cfg(tmp_path), [], {"faltantes": {"XAUUSD": 3}}).read_text(
        encoding="utf-8")
    for t in ("Bajado", "Faltante", "Tamaño por clase", "Problemas nuevos"):
        assert t in html
    assert "metal" in html


def test_cli_verificar_exit_1_con_grave(tmp_path, capsys):
    dia = date(2024, 5, 14)
    _guardar_dia(tmp_path, dia, [x for x in _cada_minuto(dia) if not (10 <= x.hour < 14)])
    ruta = tmp_path / "reg.json"
    import json
    ruta.write_text(json.dumps({"simbolos": [{"simbolo": "XAUUSD", "clase": "metal", "prioridad": 1,
                                 "fuentes": {"dukascopy": "XAUUSD"}, "decimales": 3,
                                 "horario": "metal", "divisa": "USD"}]}), encoding="utf-8")
    assert main(["verificar", "--simbolo", "XAUUSD"], ruta=ruta, config=_cfg(tmp_path)) == 1
    assert "_informes" in capsys.readouterr().out
    _guardar_dia(tmp_path, dia, _cada_minuto(dia))
    assert main(["verificar"], ruta=ruta, config=_cfg(tmp_path)) == 0
