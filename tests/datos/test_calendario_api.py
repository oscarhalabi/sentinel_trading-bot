"""Pruebas del calendario y los corporativos de FMP y EODHD.

Los fixtures de fixtures/contexto (fmp_*.json, eodhd_*.json) son PROVISIONALES: hechos a mano
con la forma que se cree que tienen las respuestas (campos date/country/event/impact/estimate/...
en FMP; type/country/date/importance/estimate/... en EODHD; split como "10.0/1.0" en EODHD).
Se sustituyen por respuestas reales recortadas en la Task 12.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from datos.almacen import leer_crudo, listar_crudos
from datos.config import ConfigInvalida, cargar_config
from datos.fuentes import calendario_api as cal
from datos.fuentes.calendario_api import EODHD, FMP, FUENTES_CALENDARIO, bajar_calendario, bajar_corporativos
from datos.simbolos import Simbolo

FIX = Path(__file__).parent / "fixtures" / "contexto"
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
CLAVE_FMP, CLAVE_EODHD = "CLAVE_FMP_SECRETA", "CLAVE_EODHD_SECRETA"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def leer(nombre):
    return (FIX / nombre).read_bytes()


class HttpFalso:
    """Responde según un trozo de la URL (el primero que aparezca en la URL)."""

    def __init__(self, respuestas):
        self.respuestas = respuestas   # [(trozo, bytes | lista de bytes por llamada)]
        self.urls = []

    def get(self, url, timeout, headers=None):
        self.urls.append(url)
        for trozo, cuerpo in self.respuestas:
            if trozo in url:
                if isinstance(cuerpo, list):
                    return 200, cuerpo.pop(0)
                return 200, cuerpo
        return 404, b""


@pytest.fixture
def cfg(tmp_path):
    return cargar_config({"DATA_DIR": str(tmp_path), "FMP_API_KEY": CLAVE_FMP,
                          "EODHD_API_KEY": CLAVE_EODHD})


def fmp(cfg, http=None):
    return FMP(cfg, http)


def filas_fmp(historico=False, ts=T0):
    return fmp(None).normalizar_eventos(leer("fmp_calendario.json"), ts, historico).to_pylist()


def test_fmp_normaliza_y_filtra_divisas(cfg):
    filas = filas_fmp()
    # Fuera: impacto bajo (US) y divisa no permitida (BRL). Quedan NFP, IPC alemán y CPI China.
    assert sorted(f["evento_fuente"] for f in filas) == [
        "CPI YoY", "Inflation Rate YoY", "Non Farm Payrolls"]
    nfp = next(f for f in filas if f["evento_fuente"] == "Non Farm Payrolls")
    assert nfp["divisa"] == "USD" and nfp["pais"] == "US" and nfp["impacto"] == "alto"
    assert nfp["ts_evento"] == utc(2024, 1, 5, 13, 30) == nfp["ts_conocido"]
    assert (nfp["prevision"], nfp["real"], nfp["anterior"]) == (170000.0, 216000.0, 216000.0)
    assert nfp["fuente"] == "fmp" and nfp["id_fuente"] is None and nfp["hora_exacta"] is True
    aleman = next(f for f in filas if f["evento_fuente"] == "Inflation Rate YoY")
    assert aleman["divisa"] == "EUR" and aleman["prevision"] == 3.0 and aleman["real"] is None
    china = next(f for f in filas if f["evento_fuente"] == "CPI YoY")
    assert china["divisa"] == "CNY"   # sin campo currency: sale de la tabla país → divisa


def test_fecha_sin_hora():
    china = next(f for f in filas_fmp() if f["evento_fuente"] == "CPI YoY")
    assert china["hora_exacta"] is False
    # Día sin hora: medianoche de Nueva York (05:00 UTC en enero).
    assert china["ts_evento"] == utc(2024, 1, 8, 5, 0)


def test_historico_ts_anunciado_horizonte():
    filas = filas_fmp(historico=True)
    for f in filas:
        assert f["ts_anunciado"] == f["ts_evento"] - timedelta(days=7)
        assert f["ts_version"] == f["ts_anunciado"]
        assert f["prevision_es_pit"] is False
        assert f["ts_conocido"] == f["ts_evento"]


def test_no_historico_prevision_es_pit():
    for f in filas_fmp(historico=False):
        assert f["prevision_es_pit"] is True
        assert f["ts_anunciado"] == f["ts_version"] == T0
        assert f["ts_conocido"] == f["ts_evento"]


def test_eodhd_pagina_con_offset(cfg, monkeypatch):
    monkeypatch.setattr(EODHD, "PAGINA", 2)
    todo = json.loads(leer("eodhd_eventos.json"))
    http = HttpFalso([("offset=0", json.dumps(todo[:2]).encode()),
                      ("offset=2", json.dumps(todo[2:]).encode())])
    fuente = EODHD(cfg, http)
    paginas = fuente.paginas_eventos(date(2024, 1, 1), date(2024, 1, 31))
    assert len(paginas) == 2
    assert len(http.urls) == 2 and "limit=2" in http.urls[0] and "offset=2" in http.urls[1]
    assert "from=2024-01-01" in http.urls[0] and "to=2024-01-31" in http.urls[0]
    filas = []
    for p in paginas:
        filas += fuente.normalizar_eventos(p, T0, False).to_pylist()
    # Fuera la de importancia 1 (bajo). Con id_fuente.
    assert sorted(f["id_fuente"] for f in filas) == ["e1", "e2"]
    gb = next(f for f in filas if f["id_fuente"] == "e2")
    assert gb["divisa"] == "GBP" and gb["impacto"] == "medio" and gb["prevision"] == 0.0


def test_eodhd_bajar_eventos_junta_paginas(cfg, monkeypatch):
    monkeypatch.setattr(EODHD, "PAGINA", 2)
    todo = json.loads(leer("eodhd_eventos.json"))
    http = HttpFalso([("offset=0", json.dumps(todo[:2]).encode()),
                      ("offset=2", json.dumps(todo[2:]).encode())])
    cuerpo = EODHD(cfg, http).bajar_eventos(date(2024, 1, 1), date(2024, 1, 31))
    assert len(json.loads(cuerpo)) == 3


def test_bajar_calendario_versiona_y_conserva_anunciado(cfg):
    http = HttpFalso([("economic-calendar", leer("fmp_calendario.json"))])
    n = bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False, http=http)
    assert n == 3
    ruta = Path(cfg.data_dir) / "calendario" / "fuente=fmp" / "simbolo=USD" / "anio=2024.parquet"
    assert pq.read_table(ruta).num_rows == 1
    # Misma respuesta: nada nuevo. Con el real cambiado: versión nueva con el mismo ts_anunciado.
    assert bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False, http=http) == 0
    filas = json.loads(leer("fmp_calendario.json"))
    filas[0]["actual"] = 999000
    http2 = HttpFalso([("economic-calendar", json.dumps(filas).encode())])
    assert bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False, http=http2) == 1
    t = pq.read_table(ruta).to_pylist()
    assert len(t) == 2 and t[0]["ts_anunciado"] == t[1]["ts_anunciado"]
    assert sorted(f["real"] for f in t) == [216000.0, 999000.0]


def test_bajar_calendario_eodhd_con_id_historico(cfg):
    http = HttpFalso([("offset=0", leer("eodhd_eventos.json"))])
    n = bajar_calendario(cfg, "eodhd", date(2024, 1, 1), date(2024, 1, 31), True, http=http)
    assert n == 2
    ruta = Path(cfg.data_dir) / "calendario" / "fuente=eodhd" / "simbolo=GBP" / "anio=2024.parquet"
    fila = pq.read_table(ruta).to_pylist()[0]
    assert fila["ts_anunciado"] == fila["ts_evento"] - timedelta(days=7) == fila["ts_version"]
    assert fila["prevision_es_pit"] is False


def test_bajar_calendario_recorre_meses(cfg):
    http = HttpFalso([("economic-calendar", b"[]")])
    bajar_calendario(cfg, "fmp", date(2024, 1, 15), date(2024, 3, 10), False, http=http)
    assert len(http.urls) == 3
    assert "from=2024-01-15&to=2024-01-31" in http.urls[0]
    assert "from=2024-02-01&to=2024-02-29" in http.urls[1]
    assert "from=2024-03-01&to=2024-03-10" in http.urls[2]


def test_clave_ausente_falla_antes_de_pedir(tmp_path):
    cfg = cargar_config({"DATA_DIR": str(tmp_path)})
    http = HttpFalso([])
    with pytest.raises(ConfigInvalida):
        bajar_calendario(cfg, "eodhd", date(2024, 1, 1), date(2024, 1, 31), False, http=http)
    with pytest.raises(ConfigInvalida):
        bajar_corporativos(cfg, "fmp", [], http=http)
    assert http.urls == []


def test_crudo_no_guarda_la_clave(cfg, monkeypatch):
    monkeypatch.setattr(EODHD, "PAGINA", 2)
    todo = json.loads(leer("eodhd_eventos.json"))
    bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False,
                     http=HttpFalso([("economic-calendar", leer("fmp_calendario.json"))]))
    bajar_calendario(cfg, "eodhd", date(2024, 1, 1), date(2024, 1, 31), False,
                     http=HttpFalso([("offset=0", json.dumps(todo[:2]).encode()),
                                     ("offset=2", json.dumps(todo[2:]).encode())]))
    manifiestos = list(listar_crudos(Path(cfg.data_dir)))
    assert any("fmp_cal_2024-01." in m.ruta for m in manifiestos)
    assert any("eodhd_cal_2024-01_p0." in m.ruta for m in manifiestos)
    assert any("eodhd_cal_2024-01_p1." in m.ruta for m in manifiestos)
    for m in manifiestos:
        assert CLAVE_FMP.encode() not in leer_crudo(Path(cfg.data_dir), m.ruta)
        assert CLAVE_EODHD.encode() not in leer_crudo(Path(cfg.data_dir), m.ruta)
    for p in Path(cfg.data_dir).rglob("*"):
        if p.is_file() and p.suffix == ".json":
            assert CLAVE_FMP not in p.read_text() and CLAVE_EODHD not in p.read_text()


def test_error_http_no_filtra_la_clave(cfg):
    with pytest.raises(RuntimeError) as e:
        bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False,
                         http=HttpFalso([]))
    assert CLAVE_FMP not in str(e.value)


def sim(nombre="NVDA.US"):
    return Simbolo(nombre, "accion", 1, {}, 2, "us", "USD")


def corporativo(cfg, fuente, cuerpos_http, simbolos=None):
    http = HttpFalso(cuerpos_http)
    n = bajar_corporativos(cfg, fuente, simbolos or [sim()], http=http)
    ruta = Path(cfg.data_dir) / "corporativo" / f"fuente={fuente}" / "simbolo=NVDA.US" / "completo.parquet"
    return n, pq.read_table(ruta).to_pylist(), http


def test_corporativo_split_y_dividendo(cfg):
    n, filas, http = corporativo(cfg, "fmp", [("splits", leer("fmp_splits.json")),
                                              ("dividends", leer("fmp_dividendos.json"))])
    assert n == 4 and len(filas) == 4
    assert any("symbol=NVDA" in u for u in http.urls)
    splits = {f["fecha_ex"]: f for f in filas if f["tipo"] == "split"}
    assert splits[date(2024, 6, 10)]["razon"] == 10.0 and splits[date(2021, 7, 20)]["razon"] == 4.0
    divs = {f["fecha_ex"]: f for f in filas if f["tipo"] == "dividendo"}
    d = divs[date(2024, 3, 6)]
    assert d["importe"] == 0.04 and d["divisa"] == "USD" and d["fecha_anuncio"] == date(2024, 2, 21)
    assert d["simbolo"] == "NVDA.US" and d["fuente"] == "fmp"
    # Anunciado el 2024-02-21: se conoce a las 09:30 NY (14:30 UTC).
    assert d["ts_conocido"] == utc(2024, 2, 21, 14, 30)
    # Dividendo con fecha de anuncio vacía en la API: se trata como "sin anuncio".
    assert divs[date(2024, 6, 11)]["fecha_anuncio"] is None


def test_corporativo_eodhd_ratio_y_ticker(cfg):
    n, filas, http = corporativo(cfg, "eodhd", [("splits/NVDA.US", leer("eodhd_splits.json")),
                                                ("div/NVDA.US", leer("eodhd_dividendos.json"))])
    assert any("/api/splits/NVDA.US" in u for u in http.urls)
    splits = {f["fecha_ex"]: f["razon"] for f in filas if f["tipo"] == "split"}
    assert splits == {date(2024, 6, 10): 10.0, date(2012, 1, 2): 0.5}   # 1/2 = contrasplit
    assert {f["divisa"] for f in filas if f["tipo"] == "dividendo"} == {"USD"}


def test_corporativo_sin_anuncio_usa_apertura_ex(cfg):
    _, filas, _ = corporativo(cfg, "fmp", [("splits", leer("fmp_splits.json")),
                                           ("dividends", leer("fmp_dividendos.json"))])
    s = next(f for f in filas if f["tipo"] == "split" and f["fecha_ex"] == date(2024, 6, 10))
    assert s["fecha_anuncio"] is None and s["ts_conocido"] == utc(2024, 6, 10, 13, 30)  # 09:30 NY (EDT)
    d = next(f for f in filas if f["tipo"] == "dividendo" and f["fecha_ex"] == date(2024, 6, 11))
    assert d["ts_conocido"] == utc(2024, 6, 11, 13, 30)


def test_corporativo_omite_no_ee_uu(cfg, caplog):
    http = HttpFalso([("splits", leer("fmp_splits.json")), ("dividends", leer("fmp_dividendos.json"))])
    with caplog.at_level("WARNING"):
        n = bajar_corporativos(cfg, "fmp", [sim("NVDA.US"), sim("SAP.DE")], http=http)
    assert n == 4 and all("SAP" not in u for u in http.urls)
    assert "1" in caplog.text and "no EE. UU." in caplog.text


def test_registro_de_fuentes():
    assert FUENTES_CALENDARIO == {"fmp": FMP, "eodhd": EODHD}


class HttpRoto:
    def __init__(self):
        self.urls = []

    def get(self, url, timeout, headers=None):
        raise ConnectionError(f"fallo de conexión a {url}")


def test_error_de_red_no_filtra_la_clave(cfg, caplog):
    with caplog.at_level("DEBUG"), pytest.raises(RuntimeError) as e:
        bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False, http=HttpRoto())
    assert CLAVE_FMP not in str(e.value) and "ConnectionError" in str(e.value)
    with caplog.at_level("DEBUG"), pytest.raises(RuntimeError) as e:
        bajar_corporativos(cfg, "eodhd", [sim()], http=HttpRoto())
    assert CLAVE_EODHD not in str(e.value)
    assert CLAVE_FMP not in caplog.text and CLAVE_EODHD not in caplog.text


def test_corporativo_redescarga_no_encoge_el_historial(cfg):
    _, antes, _ = corporativo(cfg, "fmp", [("splits", leer("fmp_splits.json")),
                                           ("dividends", leer("fmp_dividendos.json"))])
    div = leer("fmp_dividendos.json")
    # Respuesta vacía, y respuesta con una fila menos: lo guardado no cambia.
    for splits, divs in ((b"[]", b"[]"), (json.dumps(json.loads(leer("fmp_splits.json"))[:1]).encode(), div)):
        n, filas, _ = corporativo(cfg, "fmp", [("splits", splits), ("dividends", divs)])
        assert n == 0 and filas == antes
    # Una fila nueva se añade; una cambiada conserva lo guardado.
    nuevo = json.loads(leer("fmp_splits.json")) + [{"symbol": "NVDA", "date": "2025-01-02",
                                                     "numerator": 2, "denominator": 1}]
    nuevo[0]["numerator"] = 99
    n, filas, _ = corporativo(cfg, "fmp", [("splits", json.dumps(nuevo).encode()),
                                           ("dividends", div)])
    assert n == 1 and len(filas) == 5
    assert next(f for f in filas if f["fecha_ex"] == date(2024, 6, 10))["razon"] == 10.0
