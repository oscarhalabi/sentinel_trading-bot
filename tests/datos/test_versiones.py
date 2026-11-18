"""Pruebas del versionado de solo añadir (append-only)."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from datos.versiones import versionar

T1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T2 = datetime(2024, 2, 1, tzinfo=timezone.utc)
T3 = datetime(2024, 3, 1, tzinfo=timezone.utc)
CLAVE = ["evento", "ts_evento"]
CAMPOS = ["prevision", "real"]


def _ts(*a):
    return pd.Timestamp(datetime(*a, tzinfo=timezone.utc))


def _nuevas(filas):
    return pd.DataFrame(filas, columns=["evento", "ts_evento", "prevision", "real"])


def _vacias():
    return pd.DataFrame(columns=["evento", "ts_evento", "prevision", "real", "ts_version"])


def test_versionar_primera_vez_agrega_todo():
    nuevas = _nuevas([("NFP", _ts(2024, 1, 5, 13, 30), 170.0, None)])
    out = versionar(_vacias(), nuevas, CLAVE, CAMPOS, T1)
    assert len(out) == 1
    assert out["ts_version"].iloc[0] == pd.Timestamp(T1)
    assert str(out["ts_version"].dtype) == "datetime64[us, UTC]"


def test_versionar_agrega_solo_cambios():
    v1 = versionar(_vacias(), _nuevas([
        ("NFP", _ts(2024, 1, 5, 13, 30), 170.0, None),
        ("CPI", _ts(2024, 1, 11, 13, 30), 0.2, None),
    ]), CLAVE, CAMPOS, T1)
    nuevas = _nuevas([
        ("NFP", _ts(2024, 1, 5, 13, 30), 170.0, 216.0),   # cambia: sale el real
        ("CPI", _ts(2024, 1, 11, 13, 30), 0.2, None),     # igual: no se añade
        ("PPI", _ts(2024, 1, 12, 13, 30), 0.1, None),     # nuevo
    ])
    copia_previas, copia_nuevas = v1.copy(), nuevas.copy()
    v2 = versionar(v1, nuevas, CLAVE, CAMPOS, T2)
    assert len(v2) == 4
    agregadas = v2[v2["ts_version"] == pd.Timestamp(T2)]
    assert sorted(agregadas["evento"]) == ["NFP", "PPI"]
    assert (v2[v2["ts_version"] == pd.Timestamp(T1)]["evento"].tolist()) == ["NFP", "CPI"]
    # No modifica las entradas.
    pd.testing.assert_frame_equal(v1, copia_previas)
    pd.testing.assert_frame_equal(nuevas, copia_nuevas)


def test_versionar_compara_con_la_ultima_version():
    k = ("NFP", _ts(2024, 1, 5, 13, 30))
    v1 = versionar(_vacias(), _nuevas([(*k, 170.0, None)]), CLAVE, CAMPOS, T1)
    v2 = versionar(v1, _nuevas([(*k, 175.0, None)]), CLAVE, CAMPOS, T2)
    # Vuelve al valor de la versión 1: difiere de la última (175), así que se añade.
    v3 = versionar(v2, _nuevas([(*k, 170.0, None)]), CLAVE, CAMPOS, T3)
    assert v3["prevision"].tolist() == [170.0, 175.0, 170.0]
    # Igual a la última: no se añade.
    assert len(versionar(v3, _nuevas([(*k, 170.0, None)]), CLAVE, CAMPOS, T3)) == 3


def test_versionar_ignora_ruido_de_redondeo():
    k = ("CPI", _ts(2024, 1, 11, 13, 30))
    v1 = versionar(_vacias(), _nuevas([(*k, 0.3, None)]), CLAVE, CAMPOS, T1)
    # Ruido de redondeo de la API con la tolerancia por defecto: sin fila nueva.
    assert len(versionar(v1, _nuevas([(*k, 0.30000001, None)]), CLAVE, CAMPOS, T2)) == 1
    assert len(versionar(v1, _nuevas([(*k, 0.1 + 0.2, None)]), CLAVE, CAMPOS, T2)) == 1
    # Un cambio de verdad sí entra.
    assert len(versionar(v1, _nuevas([(*k, 0.31, None)]), CLAVE, CAMPOS, T2)) == 2


def test_versionar_nulos_iguales():
    k = ("NFP", _ts(2024, 1, 5, 13, 30))
    v1 = versionar(_vacias(), _nuevas([(*k, None, float("nan"))]), CLAVE, CAMPOS, T1)
    assert len(versionar(v1, _nuevas([(*k, float("nan"), None)]), CLAVE, CAMPOS, T2)) == 1
    # Nulo frente a valor: cambia.
    assert len(versionar(v1, _nuevas([(*k, 170.0, None)]), CLAVE, CAMPOS, T2)) == 2


def test_versionar_horas_exactas():
    df = lambda ts: pd.DataFrame({"evento": ["NFP"], "id": [1], "ts_anunciado": [ts]})
    v1 = versionar(pd.DataFrame(columns=["evento", "id", "ts_anunciado", "ts_version"]),
                   df(_ts(2024, 1, 5, 13, 30)), ["id"], ["evento", "ts_anunciado"], T1)
    v2 = versionar(v1, df(_ts(2024, 1, 5, 13, 30, 1)), ["id"], ["evento", "ts_anunciado"], T2)
    assert len(v2) == 2


def test_versionar_clave_repetida_en_nuevas_aborta():
    k = ("NFP", _ts(2024, 1, 5, 13, 30))
    with pytest.raises(ValueError):
        versionar(_vacias(), _nuevas([(*k, 1.0, None), (*k, 2.0, None)]), CLAVE, CAMPOS, T1)


def test_versionar_ts_version_sin_zona_aborta():
    with pytest.raises(ValueError):
        versionar(_vacias(), _nuevas([("NFP", _ts(2024, 1, 5), 1.0, None)]), CLAVE, CAMPOS,
                  datetime(2024, 1, 1))
