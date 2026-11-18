"""Versionado de solo añadir: nunca se borra ni se cambia una fila ya guardada."""
from __future__ import annotations

import math
from datetime import datetime
from numbers import Number

import pandas as pd


def _nulo(v) -> bool:
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):   # listas u otros valores no escalares
        return False


def _iguales(a, b, tolerancia: float) -> bool:
    if _nulo(a) or _nulo(b):
        return _nulo(a) and _nulo(b)
    if (isinstance(a, Number) and isinstance(b, Number)
            and not isinstance(a, bool) and not isinstance(b, bool)
            and (isinstance(a, float) or isinstance(b, float))):
        return math.isclose(a, b, rel_tol=tolerancia, abs_tol=tolerancia)
    return bool(a == b)   # textos, enteros y horas: exactos


def versionar(previas: pd.DataFrame, nuevas: pd.DataFrame, clave: list[str],
              campos: list[str], ts_version: datetime,
              tolerancia: float = 1e-6) -> pd.DataFrame:
    """Devuelve `previas` + las filas de `nuevas` cuyos `campos` difieren de la última versión
    (mayor `ts_version`) de su `clave`, con `ts_version` puesto. No modifica las entradas.

    Floats con tolerancia relativa y absoluta `tolerancia` (1e-6: tapa el ruido de redondeo de
    las APIs; las cifras económicas tienen pocos decimales, así que no esconde un cambio real);
    nulos (None, NaN, NaT) iguales entre sí.
    """
    if ts_version.tzinfo is None:
        raise ValueError("ts_version debe llevar zona horaria (UTC)")
    if nuevas.duplicated(subset=clave).any():
        raise ValueError(f"Hay filas nuevas con la clave {clave} repetida")
    ts = pd.Timestamp(ts_version).tz_convert("UTC").as_unit("us")

    ultimas: dict[tuple, tuple] = {}
    if not previas.empty:
        orden = previas.sort_values("ts_version", kind="stable")
        for fila in orden[clave + campos].itertuples(index=False, name=None):
            ultimas[fila[:len(clave)]] = fila[len(clave):]

    cambian = []
    for fila in nuevas[clave + campos].itertuples(index=False, name=None):
        anterior = ultimas.get(fila[:len(clave)])
        cambian.append(anterior is None or not all(
            _iguales(a, b, tolerancia) for a, b in zip(anterior, fila[len(clave):])))

    agregadas = nuevas[cambian].copy()
    agregadas["ts_version"] = pd.Series(ts, index=agregadas.index, dtype="datetime64[us, UTC]")
    if previas.empty:
        columnas = list(previas.columns) or list(agregadas.columns)
        return agregadas.reindex(columns=columnas).reset_index(drop=True)
    if agregadas.empty:
        return previas.copy()
    return pd.concat([previas, agregadas[previas.columns]], ignore_index=True)
