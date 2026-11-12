"""Almacén Parquet con manifiestos. Es el ÚNICO módulo que escribe archivos de datos.

Cada Parquet tiene un manifiesto JSON en `_manifiestos/<ruta>.json` con filas, rango de
tiempo, sha256 y tamaño. Todas las rutas guardadas son relativas a la raíz (DATA_DIR),
así que la carpeta se puede mover sin romper nada.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import warnings
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterator, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ESQUEMA_TICKS = pa.schema([
    ("ts", pa.timestamp("us", tz="UTC")),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("bid_vol", pa.float32()),
    ("ask_vol", pa.float32()),
])

ESQUEMA_M1 = pa.schema([
    ("ts", pa.timestamp("us", tz="UTC")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("ask_close", pa.float64()),
    ("spread_min", pa.float64()),
    ("spread_medio", pa.float64()),
    ("spread_max", pa.float64()),
    ("ticks", pa.int32()),
    ("bid_vol", pa.float64()),
    ("ask_vol", pa.float64()),
])

_ESQUEMAS = {"ticks": ESQUEMA_TICKS, "m1": ESQUEMA_M1}
VERSION_ESQUEMA = {"ticks": 1, "m1": 1}

Tipo = Literal["ticks", "m1"]
Estado = Literal["ok", "vacio", "incompleto", "falta"]

CARPETA_MANIFIESTOS = "_manifiestos"


class EsquemaInvalido(ValueError):
    """La tabla no cumple el esquema esperado."""


class ManifiestoIlegible(UserWarning):
    """Aviso: un manifiesto no se pudo leer y se trata como si no existiera."""


@dataclass
class Manifiesto:
    ruta: str
    tipo: str
    fuente: str
    simbolo: str
    periodo: str
    estado: Literal["ok", "vacio"]
    filas: int
    ts_min: str | None
    ts_max: str | None
    sha256: str | None
    esquema_version: int
    descargado_en: str
    commit: str
    hashes_anteriores: list[str] = field(default_factory=list)
    origen: dict[str, str] = field(default_factory=dict)
    bytes: int = 0  # tamaño del Parquet, para la comprobación rápida

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, texto: str) -> "Manifiesto":
        """Lee un manifiesto. Las claves que no conoce se ignoran (versiones futuras)."""
        datos = json.loads(texto)
        conocidas = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in datos.items() if k in conocidas})


# --- rutas -----------------------------------------------------------------

def ruta_relativa(tipo: Tipo, fuente: str, simbolo: str, periodo: date) -> PurePosixPath:
    base = PurePosixPath(tipo) / f"fuente={fuente}" / f"simbolo={simbolo}" / f"anio={periodo.year:04d}"
    if tipo == "ticks":
        return base / f"{periodo:%Y-%m-%d}.parquet"
    if tipo == "m1":
        return base / f"{periodo:%Y-%m}.parquet"
    raise ValueError(f"Tipo desconocido: {tipo!r}")


def _periodo_texto(tipo: str, periodo: date) -> str:
    return f"{periodo:%Y-%m-%d}" if tipo == "ticks" else f"{periodo:%Y-%m}"


def _ruta_manifiesto(raiz: Path, ruta_rel: str | PurePosixPath) -> Path:
    return Path(raiz) / CARPETA_MANIFIESTOS / (str(ruta_rel) + ".json")


# --- utilidades ------------------------------------------------------------

@lru_cache(maxsize=1)
def _commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=5,
        )
        salida = r.stdout.strip()
        return salida if r.returncode == 0 and salida else "desconocido"
    except Exception:
        return "desconocido"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _iso(valor: datetime) -> str:
    return valor.astimezone(timezone.utc).isoformat()


def _validar(tabla: pa.Table, tipo: str) -> None:
    esperado = _ESQUEMAS[tipo]
    for campo in esperado:
        if campo.name not in tabla.schema.names:
            raise EsquemaInvalido(f"Falta la columna '{campo.name}' en {tipo}")
    ts = tabla.schema.field("ts").type
    if not pa.types.is_timestamp(ts) or ts.tz is None or ts.tz.upper() not in ("UTC", "+00:00"):
        raise EsquemaInvalido(f"La columna 'ts' debe ser timestamp con zona UTC, no {ts}")
    if ts.unit != "us":
        raise EsquemaInvalido(f"La columna 'ts' debe estar en microsegundos, no '{ts.unit}'")


def _escribir_atomico(destino: Path, contenido: bytes | None = None, tabla: pa.Table | None = None) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    with open(tmp, "wb") as f:
        if tabla is not None:
            pq.write_table(tabla, f, compression="zstd")
        else:
            f.write(contenido or b"")
        f.flush()
        os.fsync(f.fileno())  # que esté en disco antes de renombrar (corte de luz)
    os.replace(tmp, destino)


def _guardar_manifiesto(raiz: Path, m: Manifiesto) -> None:
    _escribir_atomico(_ruta_manifiesto(raiz, m.ruta), contenido=m.to_json().encode("utf-8"))


# --- API -------------------------------------------------------------------

def _leer_archivo_manifiesto(p: Path) -> Manifiesto | None:
    """Lee un manifiesto; si está roto (JSON cortado, faltan claves) avisa y da None."""
    try:
        return Manifiesto.from_json(p.read_text(encoding="utf-8"))
    except (ValueError, TypeError, AttributeError) as e:
        warnings.warn(f"Manifiesto ilegible, se trata como ausente: {p} ({type(e).__name__})",
                      ManifiestoIlegible, stacklevel=3)
        return None


def leer_manifiesto(raiz: Path, ruta_rel: str) -> Manifiesto | None:
    """El manifiesto de esa ruta, o None si no existe o no se puede leer."""
    p = _ruta_manifiesto(raiz, ruta_rel)
    if not p.is_file():
        return None
    return _leer_archivo_manifiesto(p)


def escribir(raiz: Path, tabla: pa.Table, tipo: Tipo, fuente: str, simbolo: str,
             periodo: date, origen: dict[str, str] | None = None) -> Manifiesto:
    """Valida, ordena por ts y escribe el Parquet y su manifiesto de forma segura."""
    raiz = Path(raiz)
    _validar(tabla, tipo)
    tabla = tabla.sort_by("ts")
    rel = ruta_relativa(tipo, fuente, simbolo, periodo)
    destino = raiz / rel

    previo = leer_manifiesto(raiz, str(rel))
    anteriores = list(previo.hashes_anteriores) if previo else []
    if previo and previo.estado == "ok" and previo.sha256:
        anteriores.append(previo.sha256)

    _escribir_atomico(destino, tabla=tabla)

    filas = tabla.num_rows
    ts_min = _iso(pc.min(tabla["ts"]).as_py()) if filas else None
    ts_max = _iso(pc.max(tabla["ts"]).as_py()) if filas else None
    m = Manifiesto(
        ruta=str(rel), tipo=tipo, fuente=fuente, simbolo=simbolo,
        periodo=_periodo_texto(tipo, periodo), estado="ok", filas=filas,
        ts_min=ts_min, ts_max=ts_max, sha256=_sha256(destino),
        esquema_version=VERSION_ESQUEMA[tipo], descargado_en=_ahora(), commit=_commit(),
        hashes_anteriores=anteriores, origen=dict(origen or {}),
        bytes=destino.stat().st_size,
    )
    _guardar_manifiesto(raiz, m)
    return m


def marcar_vacio(raiz: Path, tipo: Tipo, fuente: str, simbolo: str, periodo: date) -> Manifiesto:
    """Anota que el periodo no tiene datos. No se hace si ya existe un Parquet."""
    raiz = Path(raiz)
    rel = ruta_relativa(tipo, fuente, simbolo, periodo)
    if (raiz / rel).exists():
        raise ValueError(f"No se puede marcar vacío: ya existe {rel}")
    m = Manifiesto(
        ruta=str(rel), tipo=tipo, fuente=fuente, simbolo=simbolo,
        periodo=_periodo_texto(tipo, periodo), estado="vacio", filas=0,
        ts_min=None, ts_max=None, sha256=None, esquema_version=VERSION_ESQUEMA[tipo],
        descargado_en=_ahora(), commit=_commit(),
    )
    _guardar_manifiesto(raiz, m)
    return m


def estado(raiz: Path, tipo: Tipo, fuente: str, simbolo: str, periodo: date,
           verificar_hash: bool = False) -> Estado:
    """ok / vacio / incompleto / falta. El hash solo se comprueba con verificar_hash=True."""
    raiz = Path(raiz)
    rel = ruta_relativa(tipo, fuente, simbolo, periodo)
    parquet = raiz / rel
    m = leer_manifiesto(raiz, str(rel))
    if m is None:
        return "incompleto" if parquet.exists() else "falta"
    if m.estado == "vacio":
        return "vacio"
    if not parquet.is_file() or parquet.stat().st_size != m.bytes:
        return "incompleto"
    if verificar_hash and _sha256(parquet) != m.sha256:
        return "incompleto"
    return "ok"


def listar_manifiestos(raiz: Path, tipo: str | None = None, fuente: str | None = None,
                       simbolo: str | None = None) -> Iterator[Manifiesto]:
    """Manifiestos guardados. `fuente` y `simbolo` (con `tipo`) recortan la carpeta que se lee.
    Los manifiestos ilegibles se saltan con un aviso."""
    base = Path(raiz) / CARPETA_MANIFIESTOS / (tipo or "")
    if tipo and fuente:
        base = base / f"fuente={fuente}"
        if simbolo:
            base = base / f"simbolo={simbolo}"
    if not base.is_dir():
        return
    for p in sorted(base.rglob("*.json")):
        m = _leer_archivo_manifiesto(p)
        if m is not None:
            yield m


def espacio_libre_gb(raiz: Path) -> float:
    """Espacio libre en GB del disco donde vive `raiz` (o su primer padre existente)."""
    p = Path(raiz)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9
