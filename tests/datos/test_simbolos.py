import json
from dataclasses import replace

import pytest

from datos import simbolos
from datos.simbolos import RegistroInvalido, Simbolo, cargar, guardar, ordenar_para_descarga

XAU = {"simbolo": "XAUUSD", "clase": "metal", "prioridad": 1,
       "fuentes": {"dukascopy": "XAUUSD"}, "decimales": 3, "horario": "metal",
       "divisa": "USD", "activo": True, "ajustado_a_mano": ["prioridad"]}
NVDA = {"simbolo": "NVDA.US", "clase": "accion", "prioridad": 2,
        "fuentes": {"dukascopy": "NVDAUSUSD"}, "decimales": 3, "horario": "accion_us",
        "divisa": "USD", "activo": True, "ajustado_a_mano": []}


def _escribir(tmp_path, lista):
    ruta = tmp_path / "simbolos.json"
    ruta.write_text(json.dumps({"version": 1, "simbolos": lista}), encoding="utf-8")
    return ruta


def test_registro_valido(tmp_path):
    reg = cargar(_escribir(tmp_path, [XAU, NVDA]))
    assert set(reg) == {"XAUUSD", "NVDA.US"}
    assert reg["XAUUSD"].clase == "metal"
    assert reg["XAUUSD"].fuentes == {"dukascopy": "XAUUSD"}
    assert reg["XAUUSD"].ajustado_a_mano == ("prioridad",)
    assert reg["NVDA.US"].prioridad == 2 and reg["NVDA.US"].horario == "accion_us"


@pytest.mark.parametrize("lista,campo", [
    ([{**XAU, "clase": "metalico"}], "clase"),
    ([{**XAU, "horario": "marte"}], "horario"),
    ([{**XAU, "decimales": "3"}], "decimales"),
    ([{**XAU, "decimales": 9}], "decimales"),
    ([{**XAU, "prioridad": 3}], "prioridad"),
    ([XAU, dict(XAU)], "duplicado"),
    ([{**XAU, "fuentes": {}}], "fuentes"),
])
def test_registro_invalido_aborta(tmp_path, lista, campo):
    with pytest.raises(RegistroInvalido) as e:
        cargar(_escribir(tmp_path, lista))
    assert "XAUUSD" in str(e.value)
    assert campo in str(e.value)


def test_guardar_y_cargar_es_identico(tmp_path):
    ruta = _escribir(tmp_path, [NVDA, XAU])
    reg = cargar(ruta)
    salida = tmp_path / "otro.json"
    guardar(reg, salida)
    assert cargar(salida) == reg
    assert not (tmp_path / "otro.json.tmp").exists()
    nombres = [s["simbolo"] for s in json.loads(salida.read_text(encoding="utf-8"))["simbolos"]]
    assert nombres == ["XAUUSD", "NVDA.US"]


def test_orden_de_descarga(tmp_path):
    reg = cargar(_escribir(tmp_path, [NVDA, XAU]))
    reg["EURUSD"] = Simbolo("EURUSD", "fx_mayor", 1, {"dukascopy": "EURUSD"}, 5, "fx", "USD")
    reg["AAPL.US"] = replace(reg["NVDA.US"], simbolo="AAPL.US", activo=False)
    reg["MSFT.US"] = replace(reg["NVDA.US"], simbolo="MSFT.US")
    assert [s.simbolo for s in ordenar_para_descarga(reg)] == \
        ["EURUSD", "XAUUSD", "MSFT.US", "NVDA.US"]


def test_registro_del_repo_es_valido():
    reg = cargar()
    assert "XAUUSD" in reg and len(reg) > 30
    assert simbolos.RUTA_REGISTRO.is_file()
