"""Horarios de mercado conocidos. (La función abierto() llega en otra tarea.)"""
from __future__ import annotations

HORARIOS = frozenset({
    "fx", "metal", "indice_us", "indice_eu", "indice_asia", "accion_us",
    "accion_eu", "energia", "materia_prima", "cripto_24x7",
})
