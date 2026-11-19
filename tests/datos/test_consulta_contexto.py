"""Pruebas de la consulta de contexto: lo que se sabía en cada instante, nunca más.

Los datos se escriben a mano (o con los normalizadores reales) en un DATA_DIR temporal.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from datos.almacen import (
    ESQUEMA_CALENDARIO,
    ESQUEMA_COT,
    ESQUEMA_ETF,
    ESQUEMA_SERIE,
    escribir,
)
from datos.config import cargar_config
from datos.consulta import ReservaBloqueada, contexto, unir_contexto
from datos.fuentes.fred import normalizar_fred
from datos.tiempos import publicacion_cot

COT_CONF = {"primera_publicacion": {"disaggregated": "2009-09-04", "tff": "2009-09-04"},
            "excepciones_publicacion": {}}


def _t(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, corte: str | None = "2030-01-01"):
    entorno = {"DATA_DIR": str(tmp_path)}
    if corte:
        entorno["DATOS_CORTE_RESERVA"] = corte
    return cargar_config(entorno)


def _evento(ts_evento, ts_version, fuente="fmp", id_fuente="1", evento="US_CPI",
            divisa="USD", real=None, prevision=None, anterior=None, anterior_revisado=None,
            impacto="alto", hora_exacta=True, pit=False, ts_anunciado=None, programado=True):
    return {
        "ts_evento": ts_evento,
        "ts_anunciado": ts_anunciado if ts_anunciado is not None else ts_version,
        "ts_conocido": ts_evento, "ts_version": ts_version,
        "ts_foto": ts_version if fuente == "forexfactory" else None,
        "fuente": fuente, "id_fuente": id_fuente, "pais": None, "divisa": divisa,
        "evento": evento, "evento_fuente": evento, "categoria": "inflacion",
        "impacto": impacto, "unidad": "%", "programado": programado,
        "hora_exacta": hora_exacta, "prevision_es_pit": pit, "real": real,
        "prevision": prevision, "anterior": anterior, "anterior_revisado": anterior_revisado,
    }


def _guardar_calendario(raiz: Path, filas: list[dict]) -> None:
    grupos: dict[tuple, list[dict]] = {}
    for f in filas:
        grupos.setdefault((f["fuente"], f["divisa"], f["ts_evento"].year), []).append(f)
    for (fuente, divisa, anio), g in grupos.items():
        tabla = pa.Table.from_pylist(g, schema=ESQUEMA_CALENDARIO)
        escribir(raiz, tabla, "calendario", fuente, divisa, date(anio, 1, 1))


def _guardar_serie(raiz: Path, filas: list[dict], fuente="alfred") -> None:
    base = {"ts_hasta": None, "fuente": fuente, "publicacion_estimada": False}
    tabla = pa.Table.from_pylist([{**base, **f} for f in filas], schema=ESQUEMA_SERIE)
    escribir(raiz, tabla, "serie", fuente, filas[0]["serie"], None)


# --- calendario -------------------------------------------------------------

CPI = _t(2024, 3, 12, 12, 30)


def _cpi(tmp_path):
    _guardar_calendario(tmp_path, [_evento(CPI, CPI - timedelta(days=7), real=3.2,
                                           prevision=3.1, anterior=3.0,
                                           anterior_revisado=3.05)])
    return _cfg(tmp_path)


def test_ipc_antes_de_su_hora_anunciado_sin_dato(tmp_path):
    cfg = _cpi(tmp_path)
    rango = (_t(2024, 3, 1), _t(2024, 4, 1))
    # Antes del anuncio: el evento no existe.
    antes = contexto("calendario", *rango, conocido_en=CPI - timedelta(days=8), cfg=cfg)
    assert antes.empty
    # Anunciado pero antes de su hora: se ve el evento, sin dato real ni sorpresa.
    r = contexto("calendario", *rango, conocido_en=CPI - timedelta(minutes=1), cfg=cfg)
    assert len(r) == 1
    fila = r.iloc[0]
    assert fila["prevision"] == pytest.approx(3.1)
    assert fila["fuente_prevision"] == "api"
    assert pd.isna(fila["real"]) and pd.isna(fila["sorpresa"])
    assert pd.isna(fila["anterior_revisado"]) and pd.isna(fila["revision_anterior"])
    assert fila["anterior"] == pytest.approx(3.0)
    # Después de su hora + latencia: dato real y sorpresa.
    d = contexto("calendario", *rango, conocido_en=CPI + timedelta(seconds=10), cfg=cfg).iloc[0]
    assert d["real"] == pytest.approx(3.2)
    assert d["sorpresa"] == pytest.approx(0.1)
    assert d["revision_anterior"] == pytest.approx(0.05)


def test_latencia_retrasa_el_dato(tmp_path):
    cfg = _cpi(tmp_path)
    rango = (_t(2024, 3, 1), _t(2024, 4, 1))
    t = CPI + timedelta(seconds=3)
    assert pd.isna(contexto("calendario", *rango, conocido_en=t, cfg=cfg).iloc[0]["real"])
    assert contexto("calendario", *rango, conocido_en=t, latencia="2s",
                    cfg=cfg).iloc[0]["real"] == pytest.approx(3.2)
    t2 = CPI + timedelta(seconds=9)
    assert pd.isna(contexto("calendario", *rango, conocido_en=t2,
                            latencia=timedelta(seconds=10), cfg=cfg).iloc[0]["real"])
    # Exactamente en ts_conocido + latencia ya se conoce.
    assert contexto("calendario", *rango, conocido_en=CPI + timedelta(seconds=5),
                    cfg=cfg).iloc[0]["real"] == pytest.approx(3.2)


def test_evento_reprogramado_hora_vieja_y_nueva(tmp_path):
    vieja, nueva = _t(2024, 3, 12, 12, 30), _t(2024, 3, 14, 12, 30)
    anuncio, cambio = _t(2024, 3, 1), _t(2024, 3, 10)
    _guardar_calendario(tmp_path, [
        _evento(vieja, anuncio, id_fuente="77", prevision=1.0),
        _evento(nueva, cambio, id_fuente="77", prevision=1.0, ts_anunciado=anuncio),
    ])
    cfg = _cfg(tmp_path)
    rango = (_t(2024, 3, 1), _t(2024, 4, 1))
    r = contexto("calendario", *rango, conocido_en=_t(2024, 3, 5), cfg=cfg)
    assert list(r["ts_evento"]) == [pd.Timestamp(vieja)]
    r = contexto("calendario", *rango, conocido_en=_t(2024, 3, 11), cfg=cfg)
    assert list(r["ts_evento"]) == [pd.Timestamp(nueva)]
    # La hora nueva fuera del rango pedido: el evento ya no sale en el rango viejo.
    r = contexto("calendario", _t(2024, 3, 12), _t(2024, 3, 13), conocido_en=_t(2024, 3, 11),
                 cfg=cfg)
    assert r.empty


def test_horizonte_recalcula_anuncio_solo_historico(tmp_path):
    _guardar_calendario(tmp_path, [
        _evento(CPI, CPI - timedelta(days=7), id_fuente="1", pit=False),
        _evento(CPI, CPI - timedelta(days=7), id_fuente="2", pit=True),
    ])
    cfg = _cfg(tmp_path)
    t = CPI - timedelta(days=3)
    r = contexto("calendario", _t(2024, 3, 1), _t(2024, 4, 1), conocido_en=t,
                 horizonte_dias=1, cfg=cfg)
    assert list(r["id_fuente"]) == ["2"]
    r = contexto("calendario", _t(2024, 3, 1), _t(2024, 4, 1), conocido_en=t, cfg=cfg)
    assert sorted(r["id_fuente"]) == ["1", "2"]


def test_evento_sin_hora_exacta_dato_al_final_del_dia(tmp_path):
    medianoche = _t(2024, 3, 12, 4)   # 00:00 NY (horario de verano)
    _guardar_calendario(tmp_path, [_evento(medianoche, _t(2024, 3, 1), hora_exacta=False,
                                           real=5.0, prevision=4.0)])
    cfg = _cfg(tmp_path)
    rango = (_t(2024, 3, 1), _t(2024, 4, 1))
    r = contexto("calendario", *rango, conocido_en=_t(2024, 3, 12, 20), cfg=cfg)
    assert pd.isna(r.iloc[0]["real"])
    r = contexto("calendario", *rango, conocido_en=_t(2024, 3, 13, 4, 0, 5), cfg=cfg)
    assert r.iloc[0]["real"] == pytest.approx(5.0)


NFP = _t(2024, 3, 8, 13, 30)


def _ff_y_api(tmp_path):
    _guardar_calendario(tmp_path, [
        _evento(NFP, NFP - timedelta(days=7), fuente="fmp", id_fuente="9", evento="US_NFP",
                prevision=200.0, real=250.0),
        # Fotos de FF: antes del evento (1 h de diferencia en la hora) y después.
        _evento(NFP + timedelta(minutes=60), NFP - timedelta(days=1), fuente="forexfactory",
                id_fuente=None, evento="US_NFP", prevision=180.0, pit=True),
        _evento(NFP + timedelta(minutes=60), NFP + timedelta(hours=1), fuente="forexfactory",
                id_fuente=None, evento="US_NFP", prevision=190.0, real=250.0, pit=True,
                ts_anunciado=NFP - timedelta(days=1)),
    ])
    return _cfg(tmp_path)


def test_prevision_ff_usa_foto_anterior_al_evento(tmp_path):
    cfg = _ff_y_api(tmp_path)
    rango = (_t(2024, 3, 1), _t(2024, 4, 1))
    t = NFP + timedelta(hours=2)
    ff = contexto("calendario", *rango, conocido_en=t, fuente="fmp", prevision="ff", cfg=cfg)
    assert ff.iloc[0]["prevision"] == pytest.approx(180.0)   # no la foto posterior (190)
    assert ff.iloc[0]["fuente_prevision"] == "ff"
    assert ff.iloc[0]["sorpresa"] == pytest.approx(70.0)
    api = contexto("calendario", *rango, conocido_en=t, fuente="fmp", prevision="api", cfg=cfg)
    assert api.iloc[0]["prevision"] == pytest.approx(200.0)
    assert api.iloc[0]["fuente_prevision"] == "api"
    mejor = contexto("calendario", *rango, conocido_en=t, fuente="fmp", cfg=cfg)
    assert mejor.iloc[0]["prevision"] == pytest.approx(180.0)
    # Desde la propia fuente FF: también la foto anterior al evento.
    propia = contexto("calendario", *rango, conocido_en=t, fuente="forexfactory",
                      prevision="ff", cfg=cfg)
    assert len(propia) == 1 and propia.iloc[0]["prevision"] == pytest.approx(180.0)
    # Antes de la primera foto de FF: no hay previsión de FF; "mejor" usa la de la API.
    antes = contexto("calendario", *rango, conocido_en=NFP - timedelta(days=2), fuente="fmp",
                     cfg=cfg)
    assert antes.iloc[0]["prevision"] == pytest.approx(200.0)
    assert antes.iloc[0]["fuente_prevision"] == "api"
    solo_ff = contexto("calendario", *rango, conocido_en=NFP - timedelta(days=2),
                       fuente="fmp", prevision="ff", cfg=cfg)
    assert pd.isna(solo_ff.iloc[0]["prevision"]) and pd.isna(solo_ff.iloc[0]["fuente_prevision"])


def test_prevision_ff_no_empareja_a_mas_de_90_min(tmp_path):
    _guardar_calendario(tmp_path, [
        _evento(NFP, NFP - timedelta(days=7), id_fuente="9", evento="US_NFP", prevision=200.0),
        _evento(NFP + timedelta(minutes=91), NFP - timedelta(days=1), fuente="forexfactory",
                id_fuente=None, evento="US_NFP", prevision=180.0, pit=True),
    ])
    r = contexto("calendario", _t(2024, 3, 1), _t(2024, 4, 1), conocido_en=NFP, fuente="fmp",
                 cfg=_cfg(tmp_path))
    assert r.iloc[0]["prevision"] == pytest.approx(200.0)


# --- series ------------------------------------------------------------------

K1, K2 = _t(2024, 2, 2, 13, 30), _t(2024, 3, 8, 13, 30)


def test_revision_alfred_no_se_ve_antes(tmp_path):
    _guardar_serie(tmp_path, [
        {"serie": "PAYEMS", "periodo": date(2024, 1, 1), "valor": 100.0, "ts_conocido": K1,
         "ts_hasta": K2},
        {"serie": "PAYEMS", "periodo": date(2024, 1, 1), "valor": 110.0, "ts_conocido": K2},
    ])
    cfg = _cfg(tmp_path)
    q = lambda t: contexto("serie", date(2023, 1, 1), date(2025, 1, 1), conocido_en=t,
                           claves=["PAYEMS"], cfg=cfg)
    assert q(K1 - timedelta(hours=1)).empty
    assert list(q(K1 + timedelta(hours=1))["valor"]) == [100.0]
    assert list(q(K2 + timedelta(seconds=2))["valor"]) == [100.0]   # latencia
    assert list(q(K2 + timedelta(hours=1))["valor"]) == [110.0]


def test_excluir_estimadas(tmp_path):
    _guardar_serie(tmp_path, [
        {"serie": "PAYEMS", "periodo": date(1990, 1, 1), "valor": 1.0,
         "ts_conocido": _t(1990, 2, 2, 13, 30), "publicacion_estimada": True},
        {"serie": "PAYEMS", "periodo": date(2024, 1, 1), "valor": 2.0, "ts_conocido": K1},
    ])
    cfg = _cfg(tmp_path)
    todas = contexto("serie", date(1980, 1, 1), date(2025, 1, 1), cfg=cfg)
    assert list(todas["valor"]) == [1.0, 2.0]
    sin = contexto("serie", date(1980, 1, 1), date(2025, 1, 1), excluir_estimadas=True, cfg=cfg)
    assert list(sin["valor"]) == [2.0]


def test_h15_dia_habil_siguiente(tmp_path):
    cuerpo = json.dumps({"observations": [
        {"date": "2024-03-08", "value": "4.08", "realtime_start": "2024-03-11",
         "realtime_end": "2024-03-11"}]}).encode()
    tabla = normalizar_fred(cuerpo, {"id": "DGS10", "retraso": "habil+1@16:30"})
    escribir(tmp_path, tabla, "serie", "fred", "DGS10", None)
    cfg = _cfg(tmp_path)
    # Viernes 8 → se conoce el lunes 11 a las 16:30 NY (20:30 UTC).
    q = lambda t: contexto("serie", date(2024, 3, 1), date(2024, 4, 1), conocido_en=t,
                           claves=["DGS10"], cfg=cfg)
    assert q(_t(2024, 3, 9)).empty
    assert q(_t(2024, 3, 11, 20, 29)).empty
    assert list(q(_t(2024, 3, 11, 20, 31))["valor"]) == [4.08]


# --- cot y etf ---------------------------------------------------------------

def _cot(raiz, filas):
    df = pd.DataFrame(filas)
    df["_anio"] = [f.year for f in df["fecha_informe"]]
    for (mercado, anio), g in df.groupby(["mercado", "_anio"]):
        tabla = pa.Table.from_pylist(g.drop(columns="_anio").to_dict("records"),
                                     schema=ESQUEMA_COT)
        escribir(raiz, tabla, "cot", "cftc", mercado, date(int(anio), 1, 1))


def _fila_cot(fecha, valor, informe="legacy", campo="interes_abierto", mercado="ORO",
              ts_version=None):
    conocido, tardia = publicacion_cot(fecha, informe, COT_CONF)
    return {"mercado": mercado, "informe": informe, "campo": campo, "fecha_informe": fecha,
            "valor": valor, "ts_conocido": conocido,
            "ts_version": ts_version or conocido, "publicacion_tardia": tardia}


def test_cot_disaggregated_2007_no_existe_antes_de_2009(tmp_path):
    _cot(tmp_path, [_fila_cot(date(2007, 3, 6), 123.0, informe="disaggregated",
                              campo="gestores_largos")])
    cfg = _cfg(tmp_path)
    q = lambda t: contexto("cot", date(2007, 1, 1), date(2008, 1, 1), conocido_en=t,
                           claves=["ORO"], cfg=cfg)
    assert q(_t(2007, 3, 10)).empty
    assert q(_t(2009, 9, 4, 19, 29)).empty          # 15:30 NY = 19:30 UTC
    r = q(_t(2009, 9, 4, 19, 31))
    assert list(r["valor"]) == [123.0] and bool(r.iloc[0]["publicacion_tardia"])


def test_cot_correccion_solo_desde_su_version(tmp_path):
    fecha = date(2024, 3, 5)
    _cot(tmp_path, [_fila_cot(fecha, 10.0),
                    _fila_cot(fecha, 11.0, ts_version=_t(2024, 3, 15))])
    cfg = _cfg(tmp_path)
    q = lambda t: contexto("cot", date(2024, 3, 1), date(2024, 4, 1), conocido_en=t,
                           cfg=cfg)
    assert list(q(_t(2024, 3, 10))["valor"]) == [10.0]
    assert list(q(_t(2024, 3, 16))["valor"]) == [11.0]


def _etf(raiz, filas):
    tabla = pa.Table.from_pylist(filas, schema=ESQUEMA_ETF)
    escribir(raiz, tabla, "etf", "spdr", "GLD", None)


def test_etf_desde_su_hora(tmp_path):
    _etf(tmp_path, [{"etf": "GLD", "fecha": date(2024, 3, 8), "toneladas": 800.0,
                     "valor_usd": 1e9, "ts_conocido": _t(2024, 3, 8, 23)}])
    cfg = _cfg(tmp_path)
    q = lambda t: contexto("etf", date(2024, 3, 1), date(2024, 4, 1), conocido_en=t,
                           cfg=cfg)
    assert q(_t(2024, 3, 8, 22, 59)).empty
    assert list(q(_t(2024, 3, 8, 23, 1))["toneladas"]) == [800.0]


# --- reserva -----------------------------------------------------------------

def test_reserva_bloquea_contexto(tmp_path):
    _guardar_calendario(tmp_path, [
        _evento(_t(2024, 5, 10, 12, 30), _t(2024, 5, 3), id_fuente="a", real=1.0),
        _evento(_t(2024, 6, 20, 12, 30), _t(2024, 6, 13), id_fuente="b", real=2.0),
    ])
    cfg = _cfg(tmp_path, corte="2024-06-01")
    with pytest.raises(ReservaBloqueada):
        contexto("calendario", _t(2024, 6, 1), _t(2024, 7, 1), cfg=cfg)
    r = contexto("calendario", _t(2024, 5, 1), _t(2024, 7, 1), cfg=cfg)
    assert list(r["id_fuente"]) == ["a"]
    registro = tmp_path / "_registro" / "reserva.jsonl"
    assert not registro.exists()
    r = contexto("calendario", _t(2024, 5, 1), _t(2024, 7, 1), claves=["USD"],
                 incluir_reservado=True, cfg=cfg)
    assert list(r["id_fuente"]) == ["a", "b"]
    lineas = [json.loads(x) for x in registro.read_text(encoding="utf-8").splitlines()]
    assert [x["simbolo"] for x in lineas] == ["calendario:USD"]


def test_reserva_oculta_dato_real_posterior_al_corte(tmp_path):
    _guardar_calendario(tmp_path, [
        _evento(_t(2024, 6, 3, 12, 30), _t(2024, 5, 27), id_fuente="a", real=1.0)])
    cfg = _cfg(tmp_path, corte="2024-06-01")
    r = contexto("calendario", _t(2024, 5, 1), _t(2024, 7, 1), cfg=cfg)
    assert len(r) == 1 and pd.isna(r.iloc[0]["real"])


def test_tipo_y_prevision_invalidos(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        contexto("velas", date(2024, 1, 1), date(2024, 2, 1), cfg=cfg)
    with pytest.raises(ValueError):
        contexto("calendario", date(2024, 1, 1), date(2024, 2, 1), prevision="otra", cfg=cfg)


def test_contexto_vacio_devuelve_columnas(tmp_path):
    r = contexto("calendario", date(2024, 1, 1), date(2024, 2, 1), cfg=_cfg(tmp_path))
    assert r.empty and {"sorpresa", "fuente_prevision", "revision_anterior"} <= set(r.columns)


# --- unir_contexto -----------------------------------------------------------

def _velas(inicio, n, paso_min=60):
    cierres = pd.date_range(inicio, periods=n, freq=f"{paso_min}min", tz="UTC").as_unit("us")
    return pd.DataFrame({"ts": cierres - pd.Timedelta(minutes=paso_min), "ts_cierre": cierres,
                         "close": np.arange(n, dtype=float)})


def test_unir_contexto_nunca_usa_dato_posterior(tmp_path):
    # El valor de cada fila es su instante de publicación (segundos): así se comprueba
    # directamente que nada conocido después del cierre llega a la vela.
    rng = np.random.default_rng(0)
    filas, inicio = [], _t(2024, 1, 1)
    for i in range(40):
        conocido = inicio + timedelta(hours=float(rng.uniform(0, 24 * 20)))
        periodo = date(2023, 1, 1) + timedelta(days=i)
        filas.append({"serie": "X", "periodo": periodo, "valor": conocido.timestamp(),
                      "ts_conocido": conocido})
    # Una revisión de un periodo viejo publicada tarde no sustituye al último periodo.
    filas.append({"serie": "X", "periodo": date(2022, 1, 1), "valor": -1.0,
                  "ts_conocido": inicio + timedelta(days=10)})
    _guardar_serie(tmp_path, filas)
    _etf(tmp_path, [{"etf": "GLD", "fecha": date(2024, 1, 1) + timedelta(days=i),
                     "toneladas": float(i), "valor_usd": float(i),
                     "ts_conocido": _t(2024, 1, 1, 23) + timedelta(days=i)} for i in range(20)])
    _cot(tmp_path, [_fila_cot(date(2024, 1, 2) + timedelta(days=7 * i), float(i))
                    for i in range(3)])
    cfg = _cfg(tmp_path)
    precios = _velas(inicio, 24 * 21)
    r = unir_contexto(precios, {"serie": ["X"], "etf": ["GLD"], "cot": ["ORO"]}, cfg=cfg)
    assert len(r) == len(precios) and list(r["close"]) == list(precios["close"])
    lat = 5.0
    buenos = [f for f in filas if f["valor"] > 0]
    for cierre, valor in zip(r["ts_cierre"], r["X"]):
        c = cierre.timestamp()
        conocidas = [f for f in buenos if f["ts_conocido"].timestamp() + lat <= c]
        if not conocidas:
            assert pd.isna(valor)
            continue
        esperado = max(conocidas, key=lambda f: f["periodo"])["valor"]
        assert valor == esperado and valor + lat <= c
    # ETF: la fila del día i se conoce ese día a las 23:00 UTC.
    for cierre, ton in zip(r["ts_cierre"], r["GLD_toneladas"]):
        if not pd.isna(ton):
            assert _t(2024, 1, 1, 23) + timedelta(days=ton) + timedelta(seconds=5) <= cierre
    # COT: el informe del martes se conoce el viernes 15:30 NY.
    for cierre, v in zip(r["ts_cierre"], r["cot_ORO_interes_abierto"]):
        if not pd.isna(v):
            conocido, _ = publicacion_cot(date(2024, 1, 2) + timedelta(days=7 * int(v)),
                                          "legacy", COT_CONF)
            assert conocido + timedelta(seconds=5) <= cierre
    assert r["cot_ORO_interes_abierto"].notna().any()


def test_unir_contexto_revision_alfred_por_vela(tmp_path):
    _guardar_serie(tmp_path, [
        {"serie": "PAYEMS", "periodo": date(2024, 1, 1), "valor": 100.0, "ts_conocido": K1,
         "ts_hasta": K2},
        {"serie": "PAYEMS", "periodo": date(2024, 1, 1), "valor": 110.0, "ts_conocido": K2},
    ])
    precios = pd.DataFrame({"ts_cierre": pd.to_datetime(
        [K1 - timedelta(hours=1), K1 + timedelta(hours=1), K2 + timedelta(hours=1)], utc=True)})
    r = unir_contexto(precios, {"serie": ["PAYEMS"]}, cfg=_cfg(tmp_path))
    assert pd.isna(r["PAYEMS"].iloc[0])
    assert list(r["PAYEMS"].iloc[1:]) == [100.0, 110.0]


def test_unir_contexto_proximo_evento_y_sorpresa(tmp_path):
    e1, e2 = _t(2024, 3, 8, 13, 30), _t(2024, 3, 12, 12, 30)
    _guardar_calendario(tmp_path, [
        _evento(e1, e1 - timedelta(days=7), id_fuente="1", evento="US_NFP", real=250.0,
                prevision=200.0),
        _evento(e2, e2 - timedelta(days=7), id_fuente="2", evento="US_CPI", real=3.0,
                prevision=3.2),
        # Impacto medio: no cuenta con impacto_min="alto".
        _evento(e1 + timedelta(hours=1), e1 - timedelta(days=7), id_fuente="3",
                evento="US_OTRO", impacto="medio", real=1.0, prevision=0.0),
        # Sin hora exacta: no cuenta como próximo evento.
        _evento(_t(2024, 3, 6, 5), _t(2024, 2, 20), id_fuente="4", evento="US_DIA",
                hora_exacta=False),
        # Otra divisa.
        _evento(_t(2024, 3, 7, 12), _t(2024, 2, 20), id_fuente="5", evento="EU_X",
                divisa="EUR", real=1.0, prevision=0.0),
    ])
    cfg = _cfg(tmp_path)
    cierres = [_t(2024, 2, 20), _t(2024, 3, 2), e1 - timedelta(minutes=30),
               e1 + timedelta(seconds=3), e1 + timedelta(minutes=10), e2 + timedelta(hours=1)]
    precios = pd.DataFrame({"ts_cierre": pd.to_datetime(cierres, utc=True).as_unit("us")})
    r = unir_contexto(precios, {"calendario": []}, divisas=["USD"], cfg=cfg)
    # Antes de que se anuncie nada (e1 se anuncia el 1 de marzo).
    assert pd.isna(r["min_hasta_proximo"].iloc[0]) and pd.isna(r["proximo_evento"].iloc[0])
    assert r["proximo_evento"].iloc[1] == "US_NFP"
    assert r["min_hasta_proximo"].iloc[2] == pytest.approx(30.0)
    assert pd.isna(r["min_desde_ultimo"].iloc[2])
    # 3 s después del NFP: ya no es el próximo, pero con latencia 5 s aún no hay dato.
    assert r["proximo_evento"].iloc[3] == "US_CPI"
    assert pd.isna(r["sorpresa_ultimo"].iloc[3])
    assert r["min_desde_ultimo"].iloc[4] == pytest.approx(10.0)
    assert r["sorpresa_ultimo"].iloc[4] == pytest.approx(50.0)
    assert r["sorpresa_ultimo"].iloc[5] == pytest.approx(-0.2)
    assert pd.isna(r["min_hasta_proximo"].iloc[5])
    # Con todas las divisas, el evento de EUR del 7 de marzo es el último antes del NFP.
    todas = unir_contexto(precios, {"calendario": []}, cfg=cfg)
    assert todas["sorpresa_ultimo"].iloc[2] == pytest.approx(1.0)


def test_unir_contexto_exige_ts_cierre_utc(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        unir_contexto(pd.DataFrame({"ts": [1]}), {"serie": []}, cfg=cfg)
    sin_zona = pd.DataFrame({"ts_cierre": pd.to_datetime(["2024-01-01"])})
    with pytest.raises(ValueError):
        unir_contexto(sin_zona, {"serie": []}, cfg=cfg)


def test_unir_contexto_version_que_baja_de_impacto_deja_de_contar(tmp_path):
    e = _t(2024, 3, 12, 12, 30)
    _guardar_calendario(tmp_path, [
        _evento(e, _t(2024, 3, 1), id_fuente="1", evento="US_CPI"),
        _evento(e, _t(2024, 3, 5), id_fuente="1", evento="US_CPI", impacto="medio",
                ts_anunciado=_t(2024, 3, 1)),
    ])
    precios = pd.DataFrame({"ts_cierre": pd.to_datetime(
        [_t(2024, 3, 3), _t(2024, 3, 6)], utc=True).as_unit("us")})
    r = unir_contexto(precios, {"calendario": []}, cfg=_cfg(tmp_path))
    assert r["proximo_evento"].iloc[0] == "US_CPI"
    assert pd.isna(r["proximo_evento"].iloc[1])


def test_corporativo_desde_su_anuncio(tmp_path):
    from datos.almacen import ESQUEMA_CORPORATIVO
    tabla = pa.Table.from_pylist([{
        "simbolo": "XAUUSD", "tipo": "dividendo", "fuente": "fmp", "fecha_ex": date(2024, 3, 15),
        "fecha_anuncio": date(2024, 3, 1), "razon": None, "importe": 0.5, "divisa": "USD",
        "ts_conocido": _t(2024, 3, 1)}], schema=ESQUEMA_CORPORATIVO)
    escribir(tmp_path, tabla, "corporativo", "fmp", "XAUUSD", None)
    cfg = _cfg(tmp_path)
    q = lambda t: contexto("corporativo", date(2024, 3, 1), date(2024, 4, 1),
                           conocido_en=t, cfg=cfg)
    assert q(_t(2024, 2, 29)).empty
    assert list(q(_t(2024, 3, 2))["importe"]) == [0.5]


_CAL = ["min_hasta_proximo", "proximo_evento", "min_desde_ultimo", "sorpresa_ultimo"]


def _precios_simples():
    return pd.DataFrame({"ts_cierre": pd.to_datetime(
        [_t(2024, 3, 3), _t(2024, 3, 6)], utc=True).as_unit("us")})


def test_unir_contexto_calendario_vacio_da_columnas_nulas(tmp_path):
    r = unir_contexto(_precios_simples(), {"calendario": []}, cfg=_cfg(tmp_path))
    assert set(_CAL) <= set(r.columns) and r[_CAL].isna().all().all()


def test_unir_contexto_filtro_sin_eventos_da_columnas_nulas(tmp_path):
    _guardar_calendario(tmp_path, [_evento(_t(2024, 3, 12, 12, 30), _t(2024, 3, 1),
                                           real=1.0, prevision=0.5)])
    cfg = _cfg(tmp_path)
    r = unir_contexto(_precios_simples(), {"calendario": []}, divisas=["NOK"], cfg=cfg)
    assert r[_CAL].isna().all().all()
    antes = pd.DataFrame({"ts_cierre": pd.to_datetime([_t(2020, 1, 1)], utc=True).as_unit("us")})
    r = unir_contexto(antes, {"calendario": []}, cfg=cfg)
    assert r[_CAL].isna().all().all()


def test_unir_contexto_claves_sin_datos_dan_columnas_nan(tmp_path):
    r = unir_contexto(_precios_simples(), {"serie": ["DGS10"], "etf": ["GLD"], "cot": ["ORO"]},
                      cfg=_cfg(tmp_path))
    assert r["DGS10"].isna().all()
    assert r["GLD_toneladas"].isna().all() and r["GLD_valor_usd"].isna().all()
    from datos.contexto_conf import cargar_contexto
    for campo in cargar_contexto()["cot"]["campos"]:
        assert r[f"cot_ORO_{campo}"].isna().all()


# --- C1: la previsión de la sorpresa nunca es posterior al evento -----------


def _nfp_api(estimado, real):
    return json.dumps([{"id": "1", "date": "2025-03-07 13:30:00", "country": "US",
                        "currency": "USD", "event": "Nonfarm Payrolls", "estimate": estimado,
                        "actual": real, "previous": 100, "impact": "High"}]).encode()


def _guardar_api(cfg, cuerpo, ts):
    from datos.almacen import escribir_crudo
    from datos.fuentes import calendario_api
    m = escribir_crudo(cfg.data_dir, cuerpo, "fmp", "fmp_cal_2025-03", ts, "json")
    calendario_api.procesar_crudo(cfg, cuerpo, m)


def _nfp_cambia_tras_el_evento(tmp_path):
    """Previsión 200 bajada antes del NFP; descarga posterior con previsión 260 y real 255."""
    cfg = _cfg(tmp_path)
    _guardar_api(cfg, _nfp_api(200, None), _t(2025, 3, 6, 12))
    _guardar_api(cfg, _nfp_api(260, 255), _t(2025, 3, 8, 12))
    return cfg


def test_sorpresa_usa_la_prevision_previa_al_evento(tmp_path):
    cfg = _nfp_cambia_tras_el_evento(tmp_path)
    for modo in ("mejor", "api"):
        r = contexto("calendario", _t(2025, 3, 1), _t(2025, 4, 1), conocido_en=_t(2025, 3, 9),
                     cfg=cfg, prevision=modo)
        fila = r.iloc[0]
        assert fila["real"] == pytest.approx(255)
        assert fila["prevision"] == pytest.approx(200)
        assert fila["sorpresa"] == pytest.approx(55)
        assert fila["fuente_prevision"] == "api"


def test_unir_contexto_sorpresa_con_prevision_previa(tmp_path):
    cfg = _nfp_cambia_tras_el_evento(tmp_path)
    precios = pd.DataFrame({"ts_cierre": pd.to_datetime(
        ["2025-03-07 13:00", "2025-03-07 14:00", "2025-03-08 13:00"], utc=True).as_unit("us")})
    r = unir_contexto(precios, {"calendario": []}, cfg=cfg)
    assert pd.isna(r["sorpresa_ultimo"].iloc[0])
    # Antes de la descarga posterior (sin real todavía en la versión vigente) no hay sorpresa;
    # después, la sorpresa es contra la previsión previa (200), no la posterior (260).
    assert pd.isna(r["sorpresa_ultimo"].iloc[1])
    assert r["sorpresa_ultimo"].iloc[2] == pytest.approx(55)


def test_evento_visto_por_primera_vez_tras_ocurrir_sin_prevision(tmp_path):
    cfg = _cfg(tmp_path)
    _guardar_api(cfg, _nfp_api(260, 255), _t(2025, 3, 8, 12))
    fila = contexto("calendario", _t(2025, 3, 1), _t(2025, 4, 1), conocido_en=_t(2025, 3, 9),
                    cfg=cfg).iloc[0]
    assert fila["real"] == pytest.approx(255)
    assert pd.isna(fila["prevision"]) and pd.isna(fila["sorpresa"])
    assert fila["fuente_prevision"] is None
    assert not fila["prevision_es_pit"]
    precios = pd.DataFrame({"ts_cierre": pd.to_datetime(["2025-03-08 13:00"],
                                                         utc=True).as_unit("us")})
    assert pd.isna(unir_contexto(precios, {"calendario": []}, cfg=cfg)["sorpresa_ultimo"].iloc[0])


def test_foto_ff_posterior_al_evento_no_es_prevision(tmp_path):
    from datos.almacen import escribir_crudo
    from datos.fuentes import forexfactory
    cfg = _cfg(tmp_path)
    ff = json.dumps([{"title": "Non-Farm Employment Change", "country": "USD",
                      "date": "2025-03-07T08:30:00-05:00", "impact": "High", "forecast": "250K",
                      "previous": "227K", "actual": "300K"}]).encode()
    m = escribir_crudo(cfg.data_dir, ff, "forexfactory", "ff_thisweek", _t(2025, 3, 7, 20), "json")
    forexfactory.procesar_crudo(cfg, ff, m)
    for modo in ("mejor", "ff", "api"):
        fila = contexto("calendario", _t(2025, 3, 1), _t(2025, 4, 1), conocido_en=_t(2025, 3, 9),
                        cfg=cfg, prevision=modo).iloc[0]
        assert pd.isna(fila["prevision"]) and pd.isna(fila["sorpresa"]), modo
        assert not fila["prevision_es_pit"]
    # Con una previsión de la API previa al evento, 'mejor' usa esa (no la de FF posterior).
    _guardar_api(cfg, _nfp_api(200, None), _t(2025, 3, 6, 12))
    r = contexto("calendario", _t(2025, 3, 1), _t(2025, 4, 1), conocido_en=_t(2025, 3, 9),
                 cfg=cfg, fuente="fmp")
    assert r.iloc[0]["prevision"] == pytest.approx(200)
    assert r.iloc[0]["fuente_prevision"] == "api"
