"""Pruebas de la fuente Dukascopy con un cliente falso (sin red)."""
import lzma
import struct
from datetime import date, datetime, timezone

import pytest

from datos.almacen import ESQUEMA_TICKS
from datos.fuentes.base import ResultadoDia
from datos.fuentes.dukascopy import Dukascopy, escala_razonable, parsear_bi5, url_hora
from datos.simbolos import Simbolo

DIA = date(2024, 5, 13)


def _sim(decimales=3, clase="metal", fuentes=None):
    return Simbolo(simbolo="XAUUSD", clase=clase, prioridad=1,
                   fuentes={"dukascopy": "XAUUSD"} if fuentes is None else fuentes,
                   decimales=decimales, horario="metal", divisa="USD")


def _bi5(ticks):
    return lzma.compress(b"".join(struct.pack(">IIIff", *t) for t in ticks))


BI5_OK = _bi5([(0, 2345123, 2345000, 1.5, 2.5)])


class ClienteFalso:
    def __init__(self, respuestas=None, por_defecto=(200, BI5_OK)):
        self.respuestas = respuestas or {}  # url -> lista de (status, cuerpo) o una tupla
        self.por_defecto = por_defecto
        self.llamadas = {}

    def get(self, url, timeout):
        self.llamadas[url] = self.llamadas.get(url, 0) + 1
        r = self.respuestas.get(url, self.por_defecto)
        if isinstance(r, list):
            i = min(self.llamadas[url] - 1, len(r) - 1)
            return r[i]
        return r


def _fuente(cliente, **kw):
    return Dukascopy(http=cliente, dormir=lambda s: None, **kw)


def _url_hora_dia(h):
    return url_hora("XAUUSD", datetime(2024, 5, 13, h, tzinfo=timezone.utc))


def test_parsea_bi5():
    hora = datetime(2024, 5, 13, 10, tzinfo=timezone.utc)
    datos = _bi5([(0, 2345123, 2345000, 1.0, 2.0),
                  (1500, 2345200, 2345100, 1.0, 2.0),
                  (3599000, 2345300, 2345200, 3.0, 4.0)])
    t = parsear_bi5(datos, hora, 3)
    assert t.schema == ESQUEMA_TICKS
    f = t.to_pylist()
    assert [x["ts"] for x in f] == [
        datetime(2024, 5, 13, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 13, 10, 0, 1, 500000, tzinfo=timezone.utc),
        datetime(2024, 5, 13, 10, 59, 59, tzinfo=timezone.utc)]
    assert f[0]["ask"] == pytest.approx(2345.123)
    assert f[0]["bid"] == pytest.approx(2345.0)
    assert f[2]["bid_vol"] == pytest.approx(4.0)


def test_url_mes_de_00_a_11():
    assert url_hora("XAUUSD", datetime(2024, 1, 5, 7, tzinfo=timezone.utc)) == \
        "https://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/05/07h_ticks.bi5"
    assert "/2024/11/31/" in url_hora("XAUUSD", datetime(2024, 12, 31, 7, tzinfo=timezone.utc))


def test_hora_vacia_es_valida():
    t = parsear_bi5(b"", datetime(2024, 5, 13, 10, tzinfo=timezone.utc), 3)
    assert t.num_rows == 0
    assert t.schema == ESQUEMA_TICKS


def test_bi5_corrupto_es_fallo():
    cliente = ClienteFalso({_url_hora_dia(10): (200, b"esto no es lzma")})
    r = _fuente(cliente, reintentos=2).bajar_dia(_sim(), DIA)
    assert r.estado == "error"
    assert r.tabla is None
    assert "2024-05-13T10:00Z" in r.error
    assert cliente.llamadas[_url_hora_dia(10)] == 3


def test_tamano_no_multiplo_de_20_es_fallo():
    with pytest.raises(ValueError):
        parsear_bi5(lzma.compress(b"x" * 21), datetime(2024, 5, 13, tzinfo=timezone.utc), 3)


def test_dia_con_hora_fallida_queda_error():
    cliente = ClienteFalso({_url_hora_dia(5): (500, b"")})
    r = _fuente(cliente, reintentos=2).bajar_dia(_sim(), DIA)
    assert r.estado == "error"
    assert "05:00Z" in r.error


def test_reintenta_y_recupera():
    cliente = ClienteFalso({_url_hora_dia(3): [(503, b""), (503, b""), (200, BI5_OK)]})
    r = _fuente(cliente).bajar_dia(_sim(), DIA)
    assert r.estado == "ok"
    assert cliente.llamadas[_url_hora_dia(3)] == 3
    assert r.tabla.num_rows == 24
    ts = r.tabla.column("ts").to_pylist()
    assert ts == sorted(ts)


def test_espera_de_503_es_triple():
    esperas = []
    cliente = ClienteFalso({_url_hora_dia(3): [(503, b""), (200, BI5_OK)]})
    f = Dukascopy(http=cliente, dormir=esperas.append, max_por_segundo=1e9, espera_base=1.0)
    assert f.bajar_dia(_sim(), DIA).estado == "ok"
    assert 3.0 in esperas


def test_todo_vacio_es_vacio():
    r = _fuente(ClienteFalso(por_defecto=(404, b""))).bajar_dia(_sim(), DIA)
    assert r == ResultadoDia("vacio", None, None)


def test_sin_codigo_dukascopy_es_error():
    r = _fuente(ClienteFalso()).bajar_dia(_sim(fuentes={"otra": "X"}), DIA)
    assert r.estado == "error" and "dukascopy" in r.error


def test_escala_absurda_para_el_simbolo():
    hora = datetime(2024, 5, 13, 10, tzinfo=timezone.utc)
    datos = _bi5([(i, 2345123, 2345000, 1.0, 1.0) for i in range(10)])
    # Con 8 decimales el oro sale a ~0.023, fuera del rango de metal (0.5-20000).
    assert escala_razonable(_sim(8), parsear_bi5(datos, hora, 8)) is not None
    assert escala_razonable(_sim(3), parsear_bi5(datos, hora, 3)) is None


def test_bid_mayor_que_ask_es_sospechoso():
    hora = datetime(2024, 5, 13, 10, tzinfo=timezone.utc)
    datos = _bi5([(i, 2345000, 2345500, 1.0, 1.0) for i in range(10)])
    assert escala_razonable(_sim(3), parsear_bi5(datos, hora, 3)) is not None
