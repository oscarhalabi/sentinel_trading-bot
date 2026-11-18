"""Configuración del paquete datos: une el .env de la raíz con os.environ."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    # Ritmo de descarga de Dukascopy. Con 4 en paralelo y 20/s nos devolvió 429/503
    # (2026-11-12); 3 y 3 bajó XAUUSD entero sin bloqueos (2026-11-14).
    dukas_max_paralelo: int = 3
    dukas_max_por_segundo: float = 3.0
    # Contexto (Fase 2C). Las claves solo viven en el .env de la laptop y no salen en repr.
    fred_api_key: str = field(default="", repr=False)
    fmp_api_key: str = field(default="", repr=False)
    eodhd_api_key: str = field(default="", repr=False)
    te_api_key: str = field(default="", repr=False)
    ff_cada_horas: float = 4.0
    fuente_calendario: str = "fmp"
    fuente_corporativo: str = "fmp"


FUENTES_API = ("fmp", "eodhd", "te")


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

    texto_par = env.get("DUKAS_MAX_PARALELO", "").strip() or "3"
    try:
        paralelo = int(texto_par)
    except ValueError:
        paralelo = 0
    if paralelo < 1:
        raise ConfigInvalida(f"DUKAS_MAX_PARALELO debe ser un entero de 1 o más, no {texto_par!r}.")

    texto_ps = env.get("DUKAS_MAX_POR_SEGUNDO", "").strip() or "3"
    try:
        por_segundo = float(texto_ps)
    except ValueError:
        por_segundo = 0.0
    if not por_segundo > 0:
        raise ConfigInvalida(f"DUKAS_MAX_POR_SEGUNDO debe ser un número mayor que 0, no {texto_ps!r}.")

    texto_ff = env.get("DATOS_FF_CADA_HORAS", "").strip() or "4"
    try:
        ff_cada_horas = float(texto_ff)
    except ValueError:
        ff_cada_horas = 0.0
    if not ff_cada_horas > 0:
        raise ConfigInvalida(f"DATOS_FF_CADA_HORAS debe ser un número mayor que 0, no {texto_ff!r}.")

    fuentes = {}
    for var in ("DATOS_FUENTE_CALENDARIO", "DATOS_FUENTE_CORPORATIVO"):
        valor = env.get(var, "").strip().lower() or "fmp"
        if valor not in FUENTES_API:
            raise ConfigInvalida(f"{var} debe ser una de {', '.join(FUENTES_API)}, no {valor!r}.")
        fuentes[var] = valor

    return Config(data_dir=data_dir, pg_dsn=env.get("DATOS_PG_DSN", ""),
                  disco_min_gb=disco_min_gb, corte_reserva=corte,
                  dukas_max_paralelo=paralelo, dukas_max_por_segundo=por_segundo,
                  fred_api_key=env.get("FRED_API_KEY", "").strip(),
                  fmp_api_key=env.get("FMP_API_KEY", "").strip(),
                  eodhd_api_key=env.get("EODHD_API_KEY", "").strip(),
                  te_api_key=env.get("TE_API_KEY", "").strip(),
                  ff_cada_horas=ff_cada_horas,
                  fuente_calendario=fuentes["DATOS_FUENTE_CALENDARIO"],
                  fuente_corporativo=fuentes["DATOS_FUENTE_CORPORATIVO"])
