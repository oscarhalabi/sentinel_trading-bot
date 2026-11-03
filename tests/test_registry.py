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


# --- magics de produccion por defecto ---

def test_magic_de_produccion_por_defecto_aborta_sin_env(reg):
    # Aunque el .env no traiga MAGIC_M15/M5/E2, sus valores de produccion valen.
    for magic in ("100100", "100200", "100300"):
        with pytest.raises(SystemExit):
            registry.validate_instance(reg, ["n1"], {"MAGIC_N1": magic})


def test_magic_de_produccion_movido_libera_el_por_defecto(reg):
    # Si produccion usa otro magic para m15, el 100100 ya no choca... pero el nuevo si.
    registry.validate_instance(reg, ["n1"], {"MAGIC_N1": "100100", "MAGIC_M15": "111"})
    with pytest.raises(SystemExit):
        registry.validate_instance(reg, ["n1"], {"MAGIC_N1": "111", "MAGIC_M15": "111"})


# --- usuario de produccion ---

class _Filas:
    def __init__(self, filas):
        self.filas = filas
        self.filtros = {}
        self.tabla = None

    def table(self, nombre):
        self.tabla = nombre
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filtros[col] = val
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = [f for f in self.filas if f.get("user_id") == self.filtros.get("user_id")]
        return r


def test_usuario_de_produccion_aborta(reg):
    cli = _Filas([{"user_id": "u", "bot_id": "m15"}, {"user_id": "u", "bot_id": "n1"}])
    with pytest.raises(SystemExit) as e:
        registry.assert_usuario_propio(cli, "u", reg)
    assert "produccion" in str(e.value) and "m15" in str(e.value)
    assert cli.tabla == "bot_instances" and cli.filtros == {"user_id": "u"}


def test_usuario_propio_o_nuevo_no_aborta(reg):
    registry.assert_usuario_propio(_Filas([]), "u", reg)
    registry.assert_usuario_propio(_Filas([{"user_id": "u", "bot_id": "n1"},
                                           {"user_id": "otro", "bot_id": "e2"}]), "u", reg)


# --- que .env son instancias ---

def test_instance_env_files_salta_plantillas(tmp_path):
    for nombre in ("a.env", "B.env", "example.env", "Example-m5.env", "template_x.env",
                   "_borrador.env", "a.env.new", "notas.txt"):
        (tmp_path / nombre).write_text("BOTS=n1\n", encoding="utf-8")
    nombres = [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
               for p in registry.instance_env_files(str(tmp_path))]
    assert nombres == ["a.env", "B.env"]


def test_registry_cli_list(tmp_path, capsys):
    (tmp_path / "a.env").write_text("BOTS=n1\n", encoding="utf-8")
    (tmp_path / "example.env").write_text("BOTS=n1\n", encoding="utf-8")
    assert registry.main(["list", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [str(tmp_path / "a.env")]


# --- terminal y cuenta MT5: nunca los de produccion ---

MT5_OK = {"MT5_PATH": r"C:\MT5_instances_n1\mt5_123\terminal64.exe", "MT5_LOGIN": "123"}


def test_mt5_propio_pasa():
    registry.validate_mt5_target(MT5_OK)


@pytest.mark.parametrize("cambio", [
    {"MT5_PATH": ""},                                                     # sin ruta: terminal por defecto
    {"MT5_PATH": r"C:\MT5_instances\mt5_26329953\terminal64.exe"},        # terminal de produccion
    {"MT5_PATH": r"c:/mt5_instances/MT5_26329955/terminal64.exe"},        # idem, otra grafia
    {"MT5_LOGIN": "26329957"},                                            # cuenta de produccion
    {"MT5_LOGIN": ""},
    {"MT5_LOGIN": "12a"},
])
def test_mt5_de_produccion_o_incompleto_aborta(cambio):
    with pytest.raises(SystemExit):
        registry.validate_mt5_target({**MT5_OK, **cambio})


def test_carpeta_con_prefijo_parecido_no_es_produccion():
    registry.validate_mt5_target({**MT5_OK, "MT5_PATH": r"C:\MT5_instances_n1\x\terminal64.exe"})


def test_validate_env_completo(reg):
    assert registry.validate_env(reg, {"BOTS": "n1,n2", **MT5_OK}) == ["n1", "n2"]
    with pytest.raises(SystemExit):
        registry.validate_env(reg, {"BOTS": "n1", "MAGIC_N1": "100300", **MT5_OK})   # magic del e2
    with pytest.raises(SystemExit):
        registry.validate_env(reg, {"BOTS": "n1", "MT5_LOGIN": "26329953",
                                    "MT5_PATH": MT5_OK["MT5_PATH"]})


def test_read_env_file_con_bom_y_no_ascii(tmp_path):
    ruta = tmp_path / "a.env"
    ruta.write_bytes("\ufeffBOTS=n1\nMT5_PASSWORD=contraseña€\n".encode("utf-8"))
    env = registry.read_env_file(str(ruta))
    assert env == {"BOTS": "n1", "MT5_PASSWORD": "contraseña€"}
