"""Piezas comunes de las fuentes de datos."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

import pyarrow as pa

from datos.simbolos import Simbolo


class ClienteHttp(Protocol):
    def get(self, url: str, timeout: float) -> tuple[int, bytes]: ...


class ClienteRequests:
    """Cliente real con `requests`. Las excepciones se dejan subir."""

    def __init__(self) -> None:
        import requests
        self._sesion = requests.Session()
        self._sesion.headers["User-Agent"] = "sentinel-datos/1.0"

    def get(self, url: str, timeout: float) -> tuple[int, bytes]:
        r = self._sesion.get(url, timeout=timeout)
        return r.status_code, r.content


@dataclass
class ResultadoDia:
    estado: Literal["ok", "vacio", "error"]
    tabla: pa.Table | None
    error: str | None = None


@dataclass
class InstrumentoFuente:
    codigo: str
    nombre: str
    clase: str
    decimales: int
    divisa: str
    inicio: date | None


class Fuente(Protocol):
    nombre: str

    def bajar_dia(self, sim: Simbolo, dia: date) -> ResultadoDia: ...

    def listar_instrumentos(self) -> list[InstrumentoFuente]: ...
