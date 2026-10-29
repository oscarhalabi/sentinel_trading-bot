"""Tests del registro de motores (motor/registry.py)."""
import pytest

from motor import registry


@pytest.fixture
def reg():
    return registry.load_registry()


def test_carga_registro(reg):
    assert "n1" in reg and "n2" in reg
    assert registry.magic_for(reg["n1"], {}) == 300100


def test_magic_desde_env(reg):
    assert registry.magic_for(reg["n1"], {"MAGIC_N1": "555"}) == 555


def test_bot_desconocido_aborta(reg):
    with pytest.raises(SystemExit) as e:
        registry.validate_instance(reg, ["n1", "zz"], {})
    assert "zz" in str(e.value)


def test_magic_duplicado_aborta(reg):
    with pytest.raises(SystemExit):
        registry.validate_instance(reg, ["n1", "n2"], {"MAGIC_N2": "300100"})


def test_choque_con_produccion_aborta(reg):
    with pytest.raises(SystemExit):
        registry.validate_instance(reg, ["n1"], {"MAGIC_E2": "300100"})


def test_instancia_valida_no_aborta(reg):
    registry.validate_instance(reg, ["n1", "n2"], {})


def test_parse_bots():
    assert registry.parse_bots(" N1, n2 ,") == ["n1", "n2"]


def test_parse_bots_vacio_aborta():
    with pytest.raises(SystemExit):
        registry.parse_bots(" , ")
    with pytest.raises(SystemExit):
        registry.parse_bots(None)


def test_symbols_default(reg):
    assert registry.symbols_for(reg["n1"], "XAUUSD+") == ("XAUUSD+",)


def test_symbols_propios():
    spec = registry.EngineSpec("x", "a:B", "MAGIC_X", 1, ("EURUSD",), "")
    assert registry.symbols_for(spec, "XAUUSD+") == ("EURUSD",)


def test_load_engine_class():
    spec = registry.EngineSpec("x", "collections:OrderedDict", "MAGIC_X", 1, None, "")
    from collections import OrderedDict
    assert registry.load_engine_class(spec) is OrderedDict
