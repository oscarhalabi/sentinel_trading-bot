"""Pruebas de `python -m datos contexto probar-calendario` con clientes HTTP falsos (sin red).

Las respuestas de FMP, EODHD y ALFRED de aquí son PROVISIONALES (hechas a mano con la forma que se
cree que tienen); se sustituyen por respuestas reales recortadas en la Task 12.
"""
import json
from datetime import date

from datos.__main__ import main
from datos.almacen import listar_crudos
from datos.config import cargar_config
from datos.fuentes.fred import bajar_series
from datos.prueba_calendario import probar_calendario

RANGOS = [(date(2008, 1, 1), date(2008, 1, 31)), (date(2024, 1, 1), date(2024, 1, 31))]
HOY = date(2026, 11, 19)

FMP_2024 = [
    # NFP a las 08:30 NY con el mismo dato que ALFRED (157232 - 157016 = 216 mil).
    {"date": "2024-01-05 13:30:00", "country": "US", "event": "Nonfarm Payrolls",
     "currency": "USD", "previous": 199000, "estimate": 170000, "actual": 216000,
     "impact": "High"},
    {"date": "2024-01-05 10:00:00", "country": "DE", "event": "Inflation Rate YoY",
     "currency": "EUR", "previous": 3.2, "estimate": 2.9, "actual": 2.9, "impact": "High"},
    {"date": "2024-01-10", "country": "JP", "event": "Household Spending",
     "currency": "JPY", "previous": 1, "estimate": None, "actual": 2, "impact": "Medium"},
]
EODHD_2024 = [
    # Misma publicación con hora y dato distintos de ALFRED.
    {"type": "Non Farm Payrolls", "country": "US", "date": "2024-01-05 15:30:00",
     "estimate": 170000, "actual": 200000, "previous": 199000, "importance": 3},
]
ALFRED_PAYEMS = {"observations": [
    {"realtime_start": "2023-12-08", "date": "2023-11-01", "value": "157000"},
    {"realtime_start": "2024-01-05", "date": "2023-11-01", "value": "157016"},
    {"realtime_start": "2024-01-05", "date": "2023-12-01", "value": "157232"},
]}
CLAVES = {"FRED_API_KEY": "CLAVE_FRED_X", "FMP_API_KEY": "CLAVE_FMP_X",
          "EODHD_API_KEY": "CLAVE_EODHD_X", "TE_API_KEY": "CLAVE_TE_X"}


class HttpFalso:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout, headers=None):
        self.urls.append(url)
        if "series_id=" in url:
            return 200, json.dumps(ALFRED_PAYEMS).encode()
        en_2024 = "from=2024-01" in url
        if "economic-calendar" in url:
            return 200, json.dumps(FMP_2024 if en_2024 else []).encode()
        if "economic-events" in url:
            return 200, json.dumps(EODHD_2024 if en_2024 else []).encode()
        return 404, b""


def config(tmp_path, **claves):
    return cargar_config({"DATA_DIR": str(tmp_path), **claves})


def test_prueba_informe_compara_con_alfred(tmp_path):
    cfg = config(tmp_path, **CLAVES)
    http = HttpFalso()
    bajar_series(cfg, http, ids=["PAYEMS"])
    ruta, res = probar_calendario(cfg, ["fmp", "eodhd"], RANGOS, http=http, hoy=HOY)
    assert ruta == tmp_path / "_informes" / "prueba-calendario-2026-11-19.html"
    fmp, eodhd = res["fmp"], res["eodhd"]
    assert fmp.estado == eodhd.estado == "probada"
    assert fmp.primer_anio == 2024 and fmp.filas == {"2008-01": 0, "2024-01": 3}
    assert fmp.divisas == ["EUR", "JPY", "USD"]
    assert round(fmp.pct_prevision) == 67 and round(fmp.pct_hora_exacta) == 67
    assert fmp.alfred == {"mapeados": 1, "comparables": 1, "hora_coincide": 1,
                          "real_comparables": 1, "real_coincide": 1}
    assert eodhd.alfred["hora_coincide"] == 0 and eodhd.alfred["real_coincide"] == 0
    texto = ruta.read_text(encoding="utf-8")
    assert "anotar a mano" in texto and "fmp" in texto and "eodhd" in texto
    for clave in CLAVES.values():
        assert clave not in texto
    # No toca el calendario normal; los crudos se guardan con el prefijo prueba_.
    assert not (tmp_path / "calendario").exists()
    nombres = [m.ruta for m in listar_crudos(tmp_path, "fmp")]
    assert nombres and all("_prueba_fmp_cal_" in r for r in nombres)


def test_prueba_sin_alfred_lo_dice(tmp_path):
    cfg = config(tmp_path, FMP_API_KEY="CLAVE_FMP_X")
    ruta, res = probar_calendario(cfg, ["fmp"], RANGOS, http=HttpFalso(), hoy=HOY)
    assert isinstance(res["fmp"].alfred, str) and "ALFRED" in res["fmp"].alfred
    assert "ALFRED no está bajado" in ruta.read_text(encoding="utf-8")


def test_prueba_sin_clave_no_probada(tmp_path, capsys):
    cfg = config(tmp_path, FMP_API_KEY="CLAVE_FMP_X")
    http = HttpFalso()
    assert main(["contexto", "probar-calendario", "--fuentes", "fmp,eodhd,te"],
                config=cfg, http=http) == 0
    assert not any("eodhd" in u for u in http.urls)
    salida = capsys.readouterr().out
    assert "eodhd: no probada (falta clave)" in salida
    assert "te: no probada (falta clave)" in salida
    informes = list((tmp_path / "_informes").glob("prueba-calendario-*.html"))
    assert len(informes) == 1
    assert "no probada (falta clave)" in informes[0].read_text(encoding="utf-8")


def test_te_con_clave_no_implementada(tmp_path):
    cfg = config(tmp_path, TE_API_KEY="CLAVE_TE_X")
    _, res = probar_calendario(cfg, ["te"], RANGOS, http=HttpFalso(), hoy=HOY)
    assert res["te"].estado == "no implementada"


def test_prueba_fuente_desconocida_sale_2(tmp_path, capsys):
    assert main(["contexto", "probar-calendario", "--fuentes", "fmp,xyz"],
                config=config(tmp_path), http=HttpFalso()) == 2
    assert "xyz" in capsys.readouterr().err


def test_prueba_error_de_api_va_al_informe(tmp_path):
    class HttpMalo(HttpFalso):
        def get(self, url, timeout, headers=None):
            self.urls.append(url)
            return 429, b"limite"

    cfg = config(tmp_path, FMP_API_KEY="CLAVE_FMP_X")
    ruta, res = probar_calendario(cfg, ["fmp"], RANGOS, http=HttpMalo(), hoy=HOY)
    assert res["fmp"].errores and "429" in res["fmp"].errores[0]
    texto = ruta.read_text(encoding="utf-8")
    assert "429" in texto and "CLAVE_FMP_X" not in texto


def test_rangos_solapados_prueban_cada_mes_una_vez(tmp_path):
    # Con los crudos que nunca se reemplazan, un mes repetido no debe romper la prueba.
    cfg = config(tmp_path, **CLAVES)
    rangos = [(date(2024, 1, 1), date(2024, 1, 31)), (date(2024, 1, 10), date(2024, 1, 20))]
    _, res = probar_calendario(cfg, ["fmp"], rangos, http=HttpFalso(), hoy=HOY)
    assert not res["fmp"].errores
    assert len([m for m in listar_crudos(tmp_path, "fmp")]) == 1
