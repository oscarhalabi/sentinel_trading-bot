"""Configuración del paquete datos: une el .env de la raíz con os.environ."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from motor.registry import read_env_file

RAIZ_REPO = Path(__file__).resolve().parent.parent


class ConfigInvalida(SystemExit):
    """Configuración mal puesta. Al ser SystemExit, la CLI muestra el mensaje sin traza."""


@dataclass(frozen=True)
class Config:
    data_dir: Path
    pg_dsn: str
    disco_min_gb: float
    corte_reserva: date | None


def _leer_entorno() -> dict[str, str]:
    env: dict[str, str] = {}
    ruta = RAIZ_REPO / ".env"
    if ruta.is_file():
        env.update(read_env_file(str(ruta)))
    env.update(os.environ)  # gana os.environ
    return env


def cargar_config(entorno: Mapping[str, str] | None = None) -> Config:
    """Carga la configuración. Si se pasa `entorno`, reemplaza .env y os.environ (tests)."""
    env = _leer_entorno() if entorno is None else dict(entorno)

    crudo = env.get("DATA_DIR", "").strip()
    if not crudo:
        raise ConfigInvalida("Falta la variable DATA_DIR (carpeta donde se guardan los datos).")
    data_dir = Path(crudo)
    if not data_dir.is_dir():
        raise ConfigInvalida(f"DATA_DIR no existe o no es una carpeta: {data_dir}")

    texto_gb = env.get("DATOS_DISCO_MIN_GB", "").strip() or "50"
    try:
        disco_min_gb = float(texto_gb)
    except ValueError:
        raise ConfigInvalida(f"DATOS_DISCO_MIN_GB debe ser un número, no {texto_gb!r}.") from None

    texto_corte = env.get("DATOS_CORTE_RESERVA", "").strip()
    corte = None
    if texto_corte:
        try:
            corte = date.fromisoformat(texto_corte)
        except ValueError:
            raise ConfigInvalida(
                f"DATOS_CORTE_RESERVA debe ser AAAA-MM-DD, no {texto_corte!r}.") from None

    return Config(data_dir=data_dir, pg_dsn=env.get("DATOS_PG_DSN", ""),
                  disco_min_gb=disco_min_gb, corte_reserva=corte)
