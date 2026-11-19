"""Pruebas de Forex Factory. El fixture ff_thisweek_provisional.json es PROVISIONAL (hecho a mano
con la forma del JSON público: title, country, date ISO con desplazamiento, impact, forecast,
previous) hasta sustituirlo por una respuesta real recortada en la laptop."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from datos.almacen import leer_crudo, listar_crudos
from datos.config import cargar_config
from datos.contexto_conf import cargar_contexto, cargar_eventos
from datos.fuentes.forexfactory import URLS_FF, guardar_foto, normalizar_ff

FIX = Path(__file__).parent / "fixtures" / "contexto" / "ff_thisweek_provisional.json"
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def norm(cuerpo=None):
    ctx = cargar_contexto()
    return normalizar_ff(cuerpo or FIX.read_bytes(), T0, cargar_eventos(),
                         ctx["divisas_calendario"], ctx["impacto_min"]).to_pylist()


def por_titulo(filas, titulo):
    return next(f for f in filas if f["evento_fuente"] == titulo)


class HttpFalso:
    def __init__(self, cuerpos):
        self.cuerpos = cuerpos   # url -> bytes | int (estado de error)

    def get(self, url, timeout, headers=None):
        c = self.cuerpos[url]
        return (c, b"") if isinstance(c, int) else (200, c)


@pytest.fixture
def cfg(tmp_path):
    return cargar_config({"DATA_DIR": str(tmp_path)})


def http(this=None, nxt=b"[]"):
    return HttpFalso({URLS_FF[0]: this if this is not None else FIX.read_bytes(),
                      URLS_FF[1]: nxt})


def con_prevision(valor):
    datos = json.loads(FIX.read_bytes())
    datos[0]["forecast"] = valor
    return json.dumps(datos).encode()


def test_ff_hora_con_desplazamiento():
    f = por_titulo(norm(), "Non-Farm Employment Change")
    assert f["ts_evento"] == utc(2026, 11, 20, 13, 30) and f["hora_exacta"]
    assert f["ts_foto"] == f["ts_anunciado"] == f["ts_version"] == T0
    assert f["ts_conocido"] == f["ts_evento"]
    assert f["evento"] == "US_NFP" and f["fuente"] == "forexfactory"
    assert f["prevision_es_pit"] and f["programado"] and f["impacto"] == "alto"


def test_foto_posterior_al_evento_no_es_pit():
    ctx = cargar_contexto()
    despues = utc(2026, 11, 20, 14, 0)   # NFP a las 13:30 UTC; Tentative del 21 aún no
    filas = normalizar_ff(FIX.read_bytes(), despues, cargar_eventos(),
                          ctx["divisas_calendario"], ctx["impacto_min"]).to_pylist()
    for f in filas:
        assert f["prevision_es_pit"] is (f["ts_evento"] > despues), f["evento_fuente"]
    assert por_titulo(filas, "Non-Farm Employment Change")["prevision_es_pit"] is False


def test_evento_sin_hora_no_cuenta_como_exacto():
    f = por_titulo(norm(), "Tentative Event")
    assert not f["hora_exacta"]
    assert f["ts_evento"] == utc(2026, 11, 21, 5, 0)   # medianoche NY (invierno, UTC-5)


def test_ff_parsea_unidades():
    filas = norm()
    nfp = por_titulo(filas, "Non-Farm Employment Change")
    assert nfp["prevision"] == 250000 and nfp["anterior"] == 227000 and nfp["unidad"] == "K"
    ipc = por_titulo(filas, "CPI m/m")
    assert ipc["prevision"] == 0.3 and ipc["unidad"] == "%" and ipc["anterior"] == -300000
    assert por_titulo(filas, "Tentative Event")["prevision"] is None


def test_filtra_divisa_e_impacto():
    titulos = {f["evento_fuente"] for f in norm()}
    assert "Other Country" not in titulos and "Minor Data" not in titulos


def test_dos_fotos_sin_cambios_no_duplican(cfg):
    assert guardar_foto(cfg, http(), T0) > 0
    assert guardar_foto(cfg, http(), T0 + timedelta(hours=4)) == 0
    t = pq.read_table(cfg.data_dir / "calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet")
    assert t.num_rows == 2


def test_prevision_cambiada_crea_version(cfg):
    guardar_foto(cfg, http(), T0)
    assert guardar_foto(cfg, http(con_prevision("260K")), T0 + timedelta(hours=4)) == 1
    t = pq.read_table(cfg.data_dir / "calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet")
    nfp = [f for f in t.to_pylist() if f["evento"] == "US_NFP"]
    assert sorted(f["prevision"] for f in nfp) == [250000, 260000]


def test_ts_anunciado_es_la_primera_foto(cfg):
    guardar_foto(cfg, http(), T0)
    t1 = T0 + timedelta(hours=4)
    guardar_foto(cfg, http(con_prevision("260K")), t1)
    t = pq.read_table(cfg.data_dir / "calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet")
    nfp = [f for f in t.to_pylist() if f["evento"] == "US_NFP"]
    assert {f["ts_anunciado"] for f in nfp} == {T0}
    assert {f["ts_version"] for f in nfp} == {T0, t1}


def test_cambio_de_hora_el_mismo_dia_es_version(cfg):
    guardar_foto(cfg, http(), T0)
    datos = json.loads(FIX.read_bytes())
    datos[0]["date"] = "2026-11-20T10:00:00-05:00"
    assert guardar_foto(cfg, http(json.dumps(datos).encode()), T0 + timedelta(hours=4)) == 1


def test_guarda_los_dos_crudos_y_un_fallo_no_pierde_el_otro(cfg):
    malo = HttpFalso({URLS_FF[0]: FIX.read_bytes(), URLS_FF[1]: 503})
    with pytest.raises(RuntimeError, match="nextweek"):
        guardar_foto(cfg, malo, T0)
    assert (cfg.data_dir / "calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet").exists()
    crudos = list(listar_crudos(cfg.data_dir, "forexfactory"))
    assert len(crudos) == 1 and leer_crudo(cfg.data_dir, crudos[0].ruta) == FIX.read_bytes()


def test_festivo_se_conserva():
    f = por_titulo(norm(), "Bank Holiday")
    assert f["impacto"] == "bajo" and f["categoria"] == "festivo" and not f["hora_exacta"]
