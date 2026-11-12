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
