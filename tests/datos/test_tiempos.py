"""Pruebas de las reglas de "cuándo se conoció" cada dato (sin mirar al futuro)."""
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from datos.contexto_conf import cargar_contexto
from datos.tiempos import (detectar_carga_en_bloque, estimar_publicacion, local_a_utc,
                           publicacion_alfred, publicacion_cot, publicacion_etf,
                           publicacion_serie_mercado)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def _serie(ctx, clave, id_):
    return next(s for s in ctx[clave] if s["id"] == id_)


def test_local_a_utc_invierno_y_verano():
    assert local_a_utc(date(2024, 1, 15), "08:30", "America/New_York") == utc(2024, 1, 15, 13, 30)
    assert local_a_utc(date(2024, 7, 15), "08:30", "America/New_York") == utc(2024, 7, 15, 12, 30)
    assert local_a_utc(date(2024, 7, 15), "08:30", "America/New_York").tzinfo == timezone.utc


def test_habil_siguiente_salta_festivo():
    # Miércoles 2024-07-03 → el jueves 4 es festivo → viernes 5 a las 16:30 NY (20:30 UTC).
    assert publicacion_serie_mercado(date(2024, 7, 3), "habil+1@16:30") == utc(2024, 7, 5, 20, 30)


def test_habil_siguiente_salta_fin_de_semana():
    assert publicacion_serie_mercado(date(2024, 5, 10), "habil+1@16:30") == utc(2024, 5, 13, 20, 30)


def test_habil_siguiente_salta_varios_festivos():
    # Viernes 2024-11-08 → lunes 11 es Veteranos → martes 12 (horario de invierno: 21:30 UTC).
    assert publicacion_serie_mercado(date(2024, 11, 8), "habil+1@16:30") == utc(2024, 11, 12, 21, 30)


def test_mismo_dia_en_cambio_de_horario():
    ctx = cargar_contexto()
    vix = _serie(ctx, "series_fred", "VIXCLS")
    # 2024-03-10 empieza el horario de verano: el lunes 11, 17:00 NY = 21:00 UTC.
    assert publicacion_serie_mercado(date(2024, 3, 11), vix["retraso"]) == utc(2024, 3, 11, 21, 0)
    assert publicacion_serie_mercado(date(2024, 3, 8), vix["retraso"]) == utc(2024, 3, 8, 22, 0)


def test_lunes_siguiente():
    # DTWEXBGS del viernes 2024-05-10 → lunes 13 a las 16:30 NY.
    assert publicacion_serie_mercado(date(2024, 5, 10), "lunes@16:30") == utc(2024, 5, 13, 20, 30)
    # Un lunes no se publica ese mismo lunes, sino el siguiente.
    assert publicacion_serie_mercado(date(2024, 5, 13), "lunes@16:30") == utc(2024, 5, 20, 20, 30)
    # Lunes festivo (Memorial Day 2024-05-27): pasa al martes, nunca antes.
    assert publicacion_serie_mercado(date(2024, 5, 24), "lunes@16:30") == utc(2024, 5, 28, 20, 30)


def test_serie_sin_retraso_usa_habil_siguiente():
    assert publicacion_serie_mercado(date(2024, 7, 3), None) == utc(2024, 7, 5, 20, 30)


def test_retraso_desconocido_aborta():
    with pytest.raises(ValueError):
        publicacion_serie_mercado(date(2024, 7, 3), "cuando_sea")


def test_publicacion_alfred():
    payems = _serie(cargar_contexto(), "series_alfred", "PAYEMS")
    assert publicacion_alfred(date(2024, 6, 7), payems) == utc(2024, 6, 7, 12, 30)


def test_estimar_publicacion_payems():
    payems = _serie(cargar_contexto(), "series_alfred", "PAYEMS")
    # Fin de marzo de 1985 + 7 días = 1985-04-07, 08:30 NY (EST: 13:30 UTC).
    assert estimar_publicacion(date(1985, 3, 1), payems) == utc(1985, 4, 7, 13, 30)


def test_estimar_publicacion_trimestral_y_semanal():
    ctx = cargar_contexto()
    gdp = _serie(ctx, "series_alfred", "GDP")
    icsa = _serie(ctx, "series_alfred", "ICSA")
    # GDP del 2.º trimestre (periodo 1990-04-01): fin 1990-06-30 + 30 días = 1990-07-30.
    assert estimar_publicacion(date(1990, 4, 1), gdp) == utc(1990, 7, 30, 12, 30)
    # ICSA de la semana que termina el sábado 1990-01-06 + 5 días = jueves 1990-01-11.
    assert estimar_publicacion(date(1990, 1, 6), icsa) == utc(1990, 1, 11, 13, 30)


def test_detectar_carga_en_bloque():
    periodos = pd.date_range("1960-01-01", periods=300, freq="MS")
    bloque = pd.DataFrame({"periodo": periodos.date,
                           "realtime_start": [date(1990, 5, 4)] * 300})
    propios = pd.DataFrame({"periodo": [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)],
                            "realtime_start": [date(2024, 2, 2), date(2024, 3, 8), date(2024, 4, 5)]})
    todo = pd.concat([bloque, propios], ignore_index=True)
    marca = detectar_carga_en_bloque(todo)
    assert marca.dtype == bool
    assert list(marca.index) == list(todo.index)
    assert marca.iloc[:300].all()
    assert not marca.iloc[300:].any()


def test_revision_general_no_es_carga_en_bloque():
    # 300 periodos, cada uno con su primera versión propia, y una revisión general posterior
    # (como GDP en 2013): esa revisión se publicó ese día, no se estima hacia atrás.
    periodos = pd.date_range("1960-01-01", periods=300, freq="MS").date
    primeras = [p + pd.Timedelta(days=40) for p in periodos]
    df = pd.DataFrame({"periodo": list(periodos) * 2,
                       "realtime_start": primeras + [date(2013, 7, 31)] * 300})
    assert not detectar_carga_en_bloque(df).any()


def test_carga_en_bloque_solo_primeras_versiones():
    # Carga en bloque en 1990 y luego una revisión general en 2013: solo las de 1990 se marcan.
    periodos = pd.date_range("1960-01-01", periods=300, freq="MS").date
    df = pd.DataFrame({"periodo": list(periodos) * 2,
                       "realtime_start": [date(1990, 5, 4)] * 300 + [date(2013, 7, 31)] * 300})
    marca = detectar_carga_en_bloque(df)
    assert marca.iloc[:300].all()
    assert not marca.iloc[300:].any()


def test_carga_en_bloque_necesita_mas_de_un_anio():
    # 24 periodos semanales (menos de un año) con la misma fecha: no es carga en bloque.
    periodos = pd.date_range("2020-01-04", periods=24, freq="7D")
    df = pd.DataFrame({"periodo": periodos.date, "realtime_start": [date(2020, 7, 1)] * 24})
    assert not detectar_carga_en_bloque(df).any()
    # 23 periodos mensuales: tampoco (hacen falta 24).
    periodos = pd.date_range("2000-01-01", periods=23, freq="MS")
    df = pd.DataFrame({"periodo": periodos.date, "realtime_start": [date(2010, 7, 1)] * 23})
    assert not detectar_carga_en_bloque(df).any()


def test_cot_viernes_y_excepcion():
    cot = {"excepciones_publicacion": {"2024-11-29": "2024-12-02T15:30"},
           "primera_publicacion": {"disaggregated": "2009-09-04", "tff": "2009-09-04"}}
    # Martes normal → viernes 15:30 NY.
    assert publicacion_cot(date(2024, 11, 19), "legacy", cot) == (utc(2024, 11, 22, 20, 30), False)
    # Semana de Acción de Gracias: la excepción lo pasa al lunes 2024-12-02 15:30 NY.
    assert publicacion_cot(date(2024, 11, 26), "legacy", cot) == (utc(2024, 12, 2, 20, 30), False)
    assert publicacion_cot(date(2024, 11, 26), "disaggregated", cot) == (utc(2024, 12, 2, 20, 30), False)


def test_cot_disaggregated_2007_publicacion_tardia():
    cot = cargar_contexto()["cot"]
    tardia = (utc(2009, 9, 4, 19, 30), True)
    assert publicacion_cot(date(2007, 3, 13), "disaggregated", cot) == tardia
    assert publicacion_cot(date(2007, 3, 13), "tff", cot) == tardia
    # Legacy no tiene primera publicación configurada: su viernes normal.
    assert publicacion_cot(date(2007, 3, 13), "legacy", cot) == (utc(2007, 3, 16, 19, 30), False)
    # Después de la primera publicación, el viernes normal.
    assert publicacion_cot(date(2009, 9, 8), "disaggregated", cot) == (utc(2009, 9, 11, 19, 30), False)


def test_cot_fin_de_semana_aborta():
    with pytest.raises(ValueError):
        publicacion_cot(date(2024, 11, 23), "legacy", cargar_contexto()["cot"])


def test_publicacion_etf():
    gld = cargar_contexto()["etf"]["GLD"]
    assert publicacion_etf(date(2024, 1, 15), gld) == utc(2024, 1, 15, 23, 0)
    assert publicacion_etf(date(2024, 7, 15), gld) == utc(2024, 7, 15, 22, 0)
