"""Ayudas compartidas por las pruebas de datos."""
from __future__ import annotations

from datetime import datetime

import pyarrow as pa


def ticks_sinteticos(inicio: datetime, n: int, precio: float = 2000.0,
                     paso_ms: int = 500) -> pa.Table:
    """Ticks de prueba con el esquema de ticks. Se completa en la Task 3."""
    raise NotImplementedError("Se completa en la Task 3 (esquema de ticks)")
