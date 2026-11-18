"""Pruebas de abierto() y los feriados."""
from datetime import datetime, timezone

import pytest

from datos.horarios import FERIADOS_FIJOS, abierto


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_fx_domingo_y_viernes():
    assert not abierto("fx", utc(2024, 5, 12, 20, 59))   # dom 16:59 NY
    assert abierto("fx", utc(2024, 5, 12, 21, 0))        # dom 17:00 NY
    assert abierto("fx", utc(2024, 5, 17, 20, 59))       # vie 16:59 NY
    assert not abierto("fx", utc(2024, 5, 17, 21, 0))    # vie 17:00 NY
    assert not abierto("fx", utc(2024, 5, 18, 12, 0))    # sábado


def test_metal_pausa_diaria():
    assert not abierto("metal", utc(2024, 5, 14, 21, 30))  # mar 17:30 NY
    assert abierto("metal", utc(2024, 5, 14, 22, 0))
    assert abierto("fx", utc(2024, 5, 14, 21, 30))


def test_sesiones_y_cripto():
    assert abierto("indice_us", utc(2024, 5, 14, 14, 0))
    assert not abierto("indice_us", utc(2024, 5, 14, 22, 0))
    assert abierto("indice_eu", utc(2024, 5, 14, 10, 0))
    assert abierto("indice_asia", utc(2024, 5, 14, 3, 0))
    assert not abierto("indice_asia", utc(2024, 5, 18, 3, 0))
    assert abierto("cripto_24x7", utc(2024, 5, 18, 3, 0))


def test_feriados_fijos():
    assert FERIADOS_FIJOS == {(1, 1), (12, 25)}
    assert not abierto("fx", utc(2024, 12, 25, 12, 0))
    assert not abierto("indice_eu", utc(2025, 1, 1, 10, 0))
    assert abierto("cripto_24x7", utc(2024, 12, 25, 12, 0))


def test_horario_desconocido_y_sin_zona():
    with pytest.raises(ValueError):
        abierto("marte", utc(2024, 5, 14))
    with pytest.raises(ValueError):
        abierto("fx", datetime(2024, 5, 14))


# --- festivos de EE. UU. (CME para metal/energía/materias primas, NYSE para índices y acciones) ---

def test_metal_viernes_santo_cerrado():
    assert not abierto("metal", utc(2024, 3, 29, 15, 0))   # Viernes Santo
    assert not abierto("metal", utc(2019, 4, 19, 12, 0))
    assert abierto("metal", utc(2024, 3, 28, 15, 0))        # jueves anterior
    assert abierto("fx", utc(2024, 3, 29, 15, 0))           # el FX no sigue a la CME


def test_metal_cierre_temprano_en_festivo():
    # Martin Luther King 2019: abierto hasta las 13:00 de Nueva York (18:00 UTC).
    assert abierto("metal", utc(2019, 1, 21, 17, 30))
    assert not abierto("metal", utc(2019, 1, 21, 18, 30))
    assert not abierto("metal", utc(2019, 1, 21, 21, 30))
    assert abierto("metal", utc(2019, 1, 21, 23, 30))       # reabre a las 18:00 NY
    # Juneteenth solo desde 2022; el 4 de julio en sábado se observa el viernes.
    assert not abierto("metal", utc(2023, 6, 19, 19, 0))
    assert abierto("metal", utc(2021, 6, 18, 19, 0))
    assert not abierto("metal", utc(2020, 7, 3, 18, 0))
    # Acción de Gracias, el viernes siguiente y Nochebuena.
    assert not abierto("metal", utc(2020, 11, 26, 19, 0))
    assert not abierto("metal", utc(2020, 11, 27, 19, 0))
    assert not abierto("metal", utc(2025, 12, 24, 19, 0))
    assert abierto("metal", utc(2025, 12, 24, 15, 0))


def test_metal_navidad_y_anio_nuevo_observados():
    assert not abierto("metal", utc(2021, 12, 24, 12, 0))   # Navidad en sábado: cierra el viernes
    assert not abierto("metal", utc(2022, 12, 26, 12, 0))   # Navidad en domingo: cierra el lunes
    assert not abierto("metal", utc(2023, 1, 2, 12, 0))     # Año Nuevo en domingo
    assert abierto("metal", utc(2021, 12, 31, 15, 0))       # Año Nuevo en sábado: el viernes abre
    # El domingo antes de un lunes cerrado no abre la sesión de la tarde.
    assert not abierto("metal", utc(2023, 12, 24, 23, 30))
    assert abierto("metal", utc(2023, 12, 17, 23, 30))


def test_indice_us_festivos_nyse():
    assert not abierto("indice_us", utc(2024, 1, 15, 15, 0))  # Martin Luther King
    assert not abierto("accion_us", utc(2024, 3, 29, 15, 0))  # Viernes Santo
    assert abierto("indice_us", utc(2024, 1, 16, 15, 0))
    # Viernes después de Acción de Gracias: cierra a las 13:00 NY (18:00 UTC).
    assert abierto("indice_us", utc(2024, 11, 29, 15, 0))
    assert not abierto("indice_us", utc(2024, 11, 29, 18, 30))
