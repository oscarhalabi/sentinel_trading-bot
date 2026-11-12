"""Pruebas del descubrimiento de instrumentos (sin red)."""
import json
import shutil
from datetime import date
from pathlib import Path

from datos.__main__ import main
from datos.fuentes.base import InstrumentoFuente
from datos.fuentes.dukascopy import Dukascopy, parsear_instrumentos
from datos.simbolos import RUTA_REGISTRO, Simbolo, cargar, fusionar_descubiertos, nombre_canonico

MUESTRA = Path(__file__).parent / "fixtures" / "dukascopy_instrumentos.json"


def _inst(codigo, nombre=None, clase="metal", dec=3, divisa="USD"):
    return InstrumentoFuente(codigo, nombre or codigo, clase, dec, divisa, None)


def _sim(nombre="XAUUSD", **kw):
    base = dict(simbolo=nombre, clase="metal", prioridad=1, fuentes={"dukascopy": nombre},
                decimales=3, horario="metal", divisa="USD")
    base.update(kw)
    return Simbolo(**base)


def test_parsea_instrumentos():
    # formato provisional: verificar con la respuesta real en la laptop (Task 11 Step 3)
    r = {i.codigo: i for i in parsear_instrumentos(MUESTRA.read_bytes())}
    assert (r["EURUSD"].clase, r["EURUSD"].decimales, r["EURUSD"].divisa) == ("fx_mayor", 5, "USD")
    assert (r["XAUUSD"].clase, r["XAUUSD"].decimales) == ("metal", 3)
    assert (r["USA500IDXUSD"].clase, r["USA500IDXUSD"].decimales) == ("indice", 3)
    assert (r["NVDAUSUSD"].clase, r["NVDAUSUSD"].decimales) == ("accion", 3)
    assert r["SPYUSUSD"].clase == "etf"
    assert (r["BTCUSD"].clase, r["BTCUSD"].decimales) == ("cripto", 1)
    assert r["LIGHTCMDUSD"].clase == "energia" and r["COPPERCMDUSD"].clase == "materia_prima"
    assert r["NVDAUSUSD"].inicio == date(2017, 7, 14)
    assert "RAROUSD" not in r  # sin grupo: se descarta


def test_acepta_jsonp():
    cuerpo = b"_callbacks____1(" + MUESTRA.read_bytes() + b");"
    assert len(parsear_instrumentos(cuerpo)) == len(parsear_instrumentos(MUESTRA.read_bytes()))


def test_nombre_canonico():
    assert nombre_canonico(_inst("NVDAUSUSD", "NVDA.US/USD", "accion")) == "NVDA.US"
    assert nombre_canonico(_inst("USA500IDXUSD", clase="indice")) == "US500"
    assert nombre_canonico(_inst("EURUSD", "EUR/USD", "fx_mayor")) == "EURUSD"
    assert nombre_canonico(_inst("LIGHTCMDUSD", clase="energia")) == "WTI"


def test_descubrir_no_pisa_ajustes_manuales():
    actuales = {"XAUUSD": _sim(ajustado_a_mano=("prioridad", "decimales"), decimales=2)}
    nuevo, nuevos, _ = fusionar_descubiertos(actuales, [_inst("XAUUSD", dec=3)], "dukascopy")
    assert nuevo["XAUUSD"].prioridad == 1 and nuevo["XAUUSD"].decimales == 2
    assert nuevos == []


def test_fusion_actualiza_campos_y_mezcla_fuentes():
    actuales = {"XAUUSD": _sim(fuentes={"dukascopy": "VIEJO", "otra": "X"}, decimales=2)}
    nuevo, _, _ = fusionar_descubiertos(actuales, [_inst("XAUUSD", dec=3)], "dukascopy")
    assert nuevo["XAUUSD"].decimales == 3
    assert nuevo["XAUUSD"].fuentes == {"dukascopy": "XAUUSD", "otra": "X"}


def test_nuevo_entra_con_prioridad_2():
    nuevo, nuevos, _ = fusionar_descubiertos({}, [_inst("NVDAUSUSD", "NVDA.US/USD", "accion")],
                                             "dukascopy")
    s = nuevo["NVDA.US"]
    assert nuevos == ["NVDA.US"] and s.prioridad == 2 and s.horario == "accion_us" and s.activo


def test_desaparecido_queda_inactivo():
    actuales = {"XAUUSD": _sim(), "XAGUSD": _sim("XAGUSD")}
    nuevo, _, desaparecidos = fusionar_descubiertos(actuales, [_inst("XAUUSD")], "dukascopy")
    assert desaparecidos == ["XAGUSD"]
    assert nuevo["XAGUSD"].activo is False and "XAGUSD" in nuevo


class _HttpFalso:
    def __init__(self, cuerpo):
        self.cuerpo, self.cabeceras = cuerpo, None

    def get(self, url, timeout, headers=None):
        self.cabeceras = headers
        return 200, self.cuerpo


def test_listar_instrumentos_usa_referer():
    http = _HttpFalso(MUESTRA.read_bytes())
    d = Dukascopy(http=http)
    assert d.listar_instrumentos()
    assert http.cabeceras and "Referer" in http.cabeceras
    assert d.sin_clase == 1


def test_descubrir_sin_aplicar_no_escribe(tmp_path, capsys):
    ruta = tmp_path / "simbolos.json"
    shutil.copy(RUTA_REGISTRO, ruta)
    antes = ruta.read_bytes()
    d = Dukascopy(http=_HttpFalso(MUESTRA.read_bytes()))
    assert main(["simbolos", "descubrir"], fuente=d, ruta=ruta) == 0
    assert ruta.read_bytes() == antes
    assert "Nuevos:" in capsys.readouterr().out


def test_descubrir_aplicar_guarda(tmp_path):
    ruta = tmp_path / "simbolos.json"
    shutil.copy(RUTA_REGISTRO, ruta)
    d = Dukascopy(http=_HttpFalso(MUESTRA.read_bytes()))
    assert main(["simbolos", "descubrir", "--aplicar"], fuente=d, ruta=ruta) == 0
    reg = cargar(ruta)
    assert reg["SAP.DE"].prioridad == 2 and reg["SAP.DE"].horario == "accion_eu"
    assert reg["XAUUSD"].prioridad == 1
    assert reg["GBPAUD"].activo is False  # no está en la muestra
