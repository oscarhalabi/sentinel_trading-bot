"""Pruebas de FRED/ALFRED. Los fixtures de tests/datos/fixtures/contexto son PROVISIONALES
(hechos a mano con la forma de la API) hasta sustituirlos por una respuesta real recortada."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from datos.almacen import leer_manifiesto, listar_crudos, leer_crudo
from datos.config import ConfigInvalida, cargar_config
from datos.contexto_conf import cargar_contexto
from datos.fuentes.fred import bajar_series, normalizar_alfred, normalizar_fred

FIX = Path(__file__).parent / "fixtures" / "contexto"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def conf(clave, id_):
    return next(s for s in cargar_contexto()[clave] if s["id"] == id_)


def filas(tabla):
    return tabla.to_pylist()


def test_alfred_versiones_y_ts_hasta():
    t = filas(normalizar_alfred((FIX / "alfred_payems.json").read_bytes(), conf("series_alfred", "PAYEMS")))
    marzo = [f for f in t if f["periodo"] == date(2024, 3, 1)]
    assert len(marzo) == 2
    assert marzo[0]["ts_conocido"] == utc(2024, 4, 5, 12, 30)   # 08:30 NY en horario de verano
    assert marzo[0]["ts_hasta"] == marzo[1]["ts_conocido"] == utc(2024, 5, 3, 12, 30)
    assert marzo[1]["ts_hasta"] is None
    assert marzo[0]["fuente"] == "alfred" and not marzo[0]["publicacion_estimada"]
    abril = next(f for f in t if f["periodo"] == date(2024, 4, 1))
    assert abril["valor"] is None   # "." → nulo, pero la fila se conserva


def test_alfred_carga_en_bloque_estimada():
    c = conf("series_alfred", "PAYEMS")
    t = filas(normalizar_alfred((FIX / "alfred_payems.json").read_bytes(), c))
    viejas = [f for f in t if f["periodo"].year < 1960]
    assert len(viejas) == 36 and all(f["publicacion_estimada"] for f in viejas)
    ene = next(f for f in viejas if f["periodo"] == date(1950, 1, 1))
    assert ene["ts_conocido"] == utc(1950, 2, 7, 13, 30)   # fin de enero + 7 días, 08:30 NY
    assert not next(f for f in t if f["periodo"] == date(2024, 2, 1))["publicacion_estimada"]


def test_fred_punto_es_nulo():
    t = filas(normalizar_fred((FIX / "fred_dgs10.json").read_bytes(), conf("series_fred", "DGS10")))
    dia4 = next(f for f in t if f["periodo"] == date(2024, 7, 4))
    assert dia4["valor"] is None and dia4["ts_hasta"] is None and dia4["fuente"] == "fred"


def test_fred_dgs10_dia_habil_siguiente():
    t = filas(normalizar_fred((FIX / "fred_dgs10.json").read_bytes(), conf("series_fred", "DGS10")))
    # Miércoles 3 de julio → el 4 es festivo → viernes 5 a las 16:30 NY (20:30 UTC).
    assert next(f for f in t if f["periodo"] == date(2024, 7, 3))["ts_conocido"] == utc(2024, 7, 5, 20, 30)


class HttpFalso:
    def __init__(self, estados=None):
        self.urls, self.estados = [], estados or {}

    def get(self, url, timeout, headers=None):
        self.urls.append(url)
        sid = url.split("series_id=")[1].split("&")[0]
        if self.estados.get(sid, 200) != 200:
            return self.estados[sid], b"error"
        nombre = "alfred_payems.json" if "realtime_start" in url else "fred_dgs10.json"
        return 200, (FIX / nombre).read_bytes()


def cfg(tmp_path, clave="CLAVE_SECRETA"):
    return cargar_config({"DATA_DIR": str(tmp_path), "FRED_API_KEY": clave})


def test_crudo_no_guarda_la_clave(tmp_path):
    http = HttpFalso()
    r = bajar_series(cfg(tmp_path), http, ids=["PAYEMS", "DGS10"])
    assert r["PAYEMS"] > 0 and r["DGS10"] == 3
    assert any("realtime_start=1776-07-04" in u and "realtime_end=9999-12-31" in u for u in http.urls)
    assert any("realtime" not in u for u in http.urls)
    assert all("api_key=CLAVE_SECRETA" in u for u in http.urls)
    assert len(list(listar_crudos(tmp_path, "alfred"))) == 1
    for p in tmp_path.rglob("*"):
        if p.is_file() and p.suffix == ".json":
            assert "CLAVE_SECRETA" not in p.read_text(encoding="utf-8")
    for m in listar_crudos(tmp_path):
        assert "api_key" not in leer_crudo(tmp_path, m.ruta).decode()
    m = leer_manifiesto(tmp_path, "serie/fuente=alfred/simbolo=PAYEMS/completo.parquet")
    assert m is not None and "api_key" not in json.dumps(m.origen)


def test_sin_clave_aborta(tmp_path):
    http = HttpFalso()
    with pytest.raises(ConfigInvalida, match="FRED_API_KEY"):
        bajar_series(cargar_config({"DATA_DIR": str(tmp_path)}), http)
    assert http.urls == []


def test_una_serie_falla_las_demas_se_guardan(tmp_path):
    http = HttpFalso({"PAYEMS": 500})
    with pytest.raises(RuntimeError, match="PAYEMS"):
        bajar_series(cfg(tmp_path), http, ids=["PAYEMS", "DGS10"])
    assert leer_manifiesto(tmp_path, "serie/fuente=fred/simbolo=DGS10/completo.parquet") is not None
