"""Pruebas de la configuración de contexto (contexto.json, eventos.json) y las claves."""
import json

import pytest

from datos.config import ConfigInvalida, cargar_config
from datos.contexto_conf import (cargar_contexto, cargar_eventos,
                                 normalizar_evento)


def _contexto_con(tmp_path, cambio):
    datos = cargar_contexto()
    cambio(datos)
    ruta = tmp_path / "contexto.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


def test_contexto_del_repo_es_valido():
    ctx = cargar_contexto()
    assert ctx["impacto_min"] == "medio"
    assert ctx["horizonte_anuncio_dias"] == 7
    assert ctx["latencia_por_defecto"] == "5s"
    assert "USD" in ctx["divisas_calendario"]
    assert {s["id"] for s in ctx["series_alfred"]} >= {"PAYEMS", "CPIAUCSL", "ICSA"}
    assert ctx["cot"]["mercados"]["ORO"] == "088691"
    assert ctx["etf"]["GLD"]["hora"] == "18:00"
    eventos = cargar_eventos()
    assert eventos["forexfactory:Non-Farm Employment Change"]["evento"] == "US_NFP"
    assert all(not k.startswith("_") for k in eventos)


def test_hora_mal_formada_aborta(tmp_path):
    def cambio(d):
        d["series_alfred"][0]["hora"] = "8:30pm"
    with pytest.raises(ConfigInvalida, match=r"series_alfred\[0\]\.hora"):
        cargar_contexto(_contexto_con(tmp_path, cambio))


def test_zona_desconocida_aborta(tmp_path):
    def cambio(d):
        d["series_alfred"][1]["zona"] = "Marte/Olimpo"
    with pytest.raises(ConfigInvalida, match=r"series_alfred\[1\]\.zona"):
        cargar_contexto(_contexto_con(tmp_path, cambio))


def test_retraso_desconocido_aborta(tmp_path):
    def cambio(d):
        d["series_fred"][0]["retraso"] = "cuando_sea"
    with pytest.raises(ConfigInvalida, match=r"series_fred\[0\]\.retraso"):
        cargar_contexto(_contexto_con(tmp_path, cambio))


def test_frecuencia_de_serie(tmp_path):
    ctx = cargar_contexto()
    frec = {s["id"]: s.get("frecuencia", "m") for s in ctx["series_alfred"]}
    assert frec["GDP"] == "q" and frec["ICSA"] == "w" and frec["PAYEMS"] == "m"

    def cambio(d):
        d["series_alfred"][2]["frecuencia"] = "anual"
    with pytest.raises(ConfigInvalida, match=r"series_alfred\[2\]\.frecuencia"):
        cargar_contexto(_contexto_con(tmp_path, cambio))


def test_divisa_mal_puesta_aborta(tmp_path):
    def cambio(d):
        d["divisas_calendario"].append("DOLAR")
    with pytest.raises(ConfigInvalida, match="divisas_calendario"):
        cargar_contexto(_contexto_con(tmp_path, cambio))


def test_eventos_forma_invalida_aborta(tmp_path):
    ruta = tmp_path / "eventos.json"
    ruta.write_text(json.dumps({"fmp:NFP": {"evento": "US_NFP"}}), encoding="utf-8")
    with pytest.raises(ConfigInvalida, match="fmp:NFP"):
        cargar_eventos(ruta)
    ruta.write_text(json.dumps({"sin_fuente": {"evento": "X", "categoria": "y"}}),
                    encoding="utf-8")
    with pytest.raises(ConfigInvalida, match="sin_fuente"):
        cargar_eventos(ruta)


def test_normalizar_mapeado_y_sin_mapear():
    mapa = {"fmp:Nonfarm Payrolls": {"evento": "US_NFP", "categoria": "empleo"}}
    assert normalizar_evento("fmp", "Nonfarm Payrolls", mapa) == ("US_NFP", "empleo")
    assert normalizar_evento("fmp", "Cosa rara", mapa) == ("Cosa rara", "otros")


def test_config_lee_claves_y_no_las_muestra(tmp_path):
    cfg = cargar_config({"DATA_DIR": str(tmp_path), "FRED_API_KEY": "secreto1",
                         "FMP_API_KEY": "secreto2", "EODHD_API_KEY": "secreto3",
                         "TE_API_KEY": "secreto4", "DATOS_FF_CADA_HORAS": "2",
                         "DATOS_FUENTE_CALENDARIO": "eodhd",
                         "DATOS_FUENTE_CORPORATIVO": "te"})
    assert cfg.fred_api_key == "secreto1" and cfg.te_api_key == "secreto4"
    assert cfg.fmp_api_key == "secreto2" and cfg.eodhd_api_key == "secreto3"
    assert cfg.ff_cada_horas == 2.0
    assert cfg.fuente_calendario == "eodhd" and cfg.fuente_corporativo == "te"
    assert "secreto" not in repr(cfg)


def test_config_contexto_por_defecto(tmp_path):
    cfg = cargar_config({"DATA_DIR": str(tmp_path)})
    assert cfg.fred_api_key == "" and cfg.ff_cada_horas == 4.0
    assert cfg.fuente_calendario == "fmp" and cfg.fuente_corporativo == "fmp"


@pytest.mark.parametrize("var,valor", [("DATOS_FF_CADA_HORAS", "abc"),
                                       ("DATOS_FF_CADA_HORAS", "0"),
                                       ("DATOS_FF_CADA_HORAS", "-1"),
                                       ("DATOS_FUENTE_CALENDARIO", "yahoo"),
                                       ("DATOS_FUENTE_CORPORATIVO", "xx")])
def test_config_contexto_invalida_aborta(tmp_path, var, valor):
    with pytest.raises(ConfigInvalida, match=var):
        cargar_config({"DATA_DIR": str(tmp_path), var: valor})
