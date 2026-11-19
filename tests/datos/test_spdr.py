"""Pruebas de las tenencias de GLD (SPDR).

El fixture contexto/gld.csv es PROVISIONAL: columnas Date, Tonnes y Total Value US$, con fechas
"DD-Mon-YYYY" o "YYYY-MM-DD", miles con coma y una fila HOLIDAY. Se sustituye por el CSV real
recortado en la Task 12.
"""
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from datos.almacen import listar_crudos
from datos.config import cargar_config
from datos.contexto_conf import cargar_contexto
from datos.fuentes import spdr
from datos.fuentes.spdr import URL_GLD, bajar_gld, normalizar_gld

FIX = Path(__file__).parent / "fixtures" / "contexto"
CSV = (FIX / "gld.csv").read_bytes()
ETF = cargar_contexto()["etf"]["GLD"]


class HttpFalso:
    def __init__(self, cuerpo=CSV, estado=200):
        self.cuerpo, self.estado, self.urls = cuerpo, estado, []

    def get(self, url, timeout, headers=None):
        self.urls.append(url)
        return self.estado, self.cuerpo


@pytest.fixture
def cfg(tmp_path):
    return cargar_config({"DATA_DIR": str(tmp_path)})


def test_gld_normaliza_y_ts_conocido_18h():
    t = normalizar_gld(CSV, ETF).to_pylist()
    assert [f["fecha"] for f in t] == [date(2026, 11, 15), date(2026, 11, 17), date(2026, 11, 18)]
    assert t[0]["etf"] == "GLD" and t[0]["toneladas"] == 860.52
    assert t[0]["valor_usd"] == 71234567890.50
    # 18:00 de Nueva York (EST en noviembre) = 23:00 UTC
    assert t[0]["ts_conocido"] == datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc)


def test_gld_formato_cambiado_avisa():
    with pytest.raises(ValueError, match="tonnes"):
        normalizar_gld(b"Date,Otra\n15-Nov-2026,1\n", ETF)


def test_gld_salta_filas_y_las_cuenta(caplog):
    with caplog.at_level(logging.WARNING):
        normalizar_gld(CSV, ETF)
    assert "2 filas" in caplog.text


def test_bajar_gld_guarda_crudo_y_parquet(cfg, tmp_path):
    http = HttpFalso()
    assert bajar_gld(cfg, http) == 3
    assert http.urls == [URL_GLD]
    ruta = tmp_path / "etf" / "fuente=spdr" / "simbolo=GLD" / "completo.parquet"
    assert pq.read_table(ruta).num_rows == 3
    assert len(list(listar_crudos(tmp_path, "spdr"))) == 1


def test_bajar_gld_no_encoge_el_historico(cfg, tmp_path, caplog):
    bajar_gld(cfg, HttpFalso())
    corto = b"Date,Tonnes,Total Value US$\n15-Nov-2026,999,1\n19-Nov-2026,863,72\n"
    with caplog.at_level(logging.WARNING):
        assert bajar_gld(cfg, HttpFalso(corto)) == 1
    t = pq.read_table(tmp_path / "etf" / "fuente=spdr" / "simbolo=GLD" / "completo.parquet")
    por_fecha = {f["fecha"]: f for f in t.to_pylist()}
    assert len(por_fecha) == 4 and por_fecha[date(2026, 11, 15)]["toneladas"] == 860.52
    assert "cambió" in caplog.text
    assert bajar_gld(cfg, HttpFalso(corto)) == 0   # nada nuevo: no reescribe


def test_fecha_repetida_en_el_csv_se_guarda_una_vez(cfg, tmp_path):
    repetido = (b"Date,Tonnes,Total Value US$\n15-Nov-2026,850,1\n15-Nov-2026,851,2\n"
                b"16-Nov-2026,852,3\n")
    assert bajar_gld(cfg, HttpFalso(repetido)) == 2
    t = pq.read_table(tmp_path / "etf" / "fuente=spdr" / "simbolo=GLD" / "completo.parquet")
    filas = t.to_pylist()
    assert sorted(f["fecha"] for f in filas) == [date(2026, 11, 15), date(2026, 11, 16)]
    assert next(f for f in filas if f["fecha"] == date(2026, 11, 15))["toneladas"] == 851.0
    # Con lo ya guardado también: la repetida nueva no entra dos veces.
    otro = b"Date,Tonnes,Total Value US$\n17-Nov-2026,853,4\n17-Nov-2026,854,5\n"
    assert bajar_gld(cfg, HttpFalso(otro)) == 1


def test_error_http_y_de_red_sin_detalles(cfg):
    with pytest.raises(RuntimeError, match="HTTP 500"):
        bajar_gld(cfg, HttpFalso(estado=500))

    class Roto:
        def get(self, *a, **k):
            raise ConnectionError("https://secreto/url")
    with pytest.raises(RuntimeError) as e:
        bajar_gld(cfg, Roto())
    assert "secreto" not in str(e.value) and "ConnectionError" in str(e.value)
