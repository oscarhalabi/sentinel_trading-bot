"""Orquestador de descarga: decide qué días faltan, los pide a la fuente y los guarda.

Recorre cada símbolo de ayer hacia atrás. Es reanudable: lo que ya está `ok` o `vacio`
no se vuelve a pedir; un día con `error` se reintenta en la corrida siguiente.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from datos.almacen import (escribir, espacio_libre_gb, estado, listar_manifiestos,
                           marcar_vacio)
from datos.catalogo import Catalogo
from datos.config import Config
from datos.fuentes.base import Fuente
from datos.fuentes.dukascopy import escala_razonable
from datos.simbolos import Simbolo

SEMANAS_VACIAS = 8
DIAS_VACIOS_PARA_PARAR = SEMANAS_VACIAS * 7  # 56 días seguidos sin datos
ERRORES_SEGUIDOS_PARA_PARAR = 10  # un símbolo que solo da errores no recorre 20 años
PISO = date(2000, 1, 1)  # red de seguridad: nunca se pide nada más antiguo
TIPO = "ticks"


@dataclass
class Resumen:
    dias_ok: int = 0
    dias_vacios: int = 0
    dias_error: int = 0
    simbolos_parados: dict[str, str] = field(default_factory=dict)
    parado_por: str | None = None


# --- candado contra descargas simultáneas ---

def _pid_vivo(pid: int) -> bool:
    """¿Existe un proceso con ese PID? Funciona en Linux y en Windows."""
    if pid <= 0:
        return False
    if os.name == "nt":  # en Windows os.kill(pid, 0) mataría el proceso: no se usa
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            codigo = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(codigo)):
                return True
            return codigo.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, aunque sea de otro usuario
    except OverflowError:
        return False
    return True


def _pid_del_candado(ruta: Path) -> int | None:
    try:
        return int(ruta.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


@contextmanager
def candado(raiz: Path):
    """Crea DATA_DIR/_registro/descarga.lock con el PID; lo borra al salir."""
    carpeta = Path(raiz) / "_registro"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "descarga.lock"
    for _ in range(2):
        try:
            fd = os.open(str(ruta), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = _pid_del_candado(ruta)
            if pid is not None and _pid_vivo(pid):
                raise SystemExit(f"Ya hay una descarga en marcha (PID {pid})") from None
            try:  # candado huérfano: su dueño ya no existe
                ruta.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        break
    else:
        raise SystemExit("No se pudo tomar el candado de descarga (otra descarga arrancó a la vez).")
    try:
        yield
    finally:
        try:
            ruta.unlink()
        except FileNotFoundError:
            pass


# --- estado de los días ---

def _hechos_del_almacen(cfg: Config, fuente: str, simbolo: str) -> dict[date, str]:
    """Estado por día leyendo los manifiestos del símbolo una sola vez."""
    hechos: dict[date, str] = {}
    for m in listar_manifiestos(cfg.data_dir, TIPO):
        if m.fuente != fuente or m.simbolo != simbolo:
            continue
        dia = date.fromisoformat(m.periodo)
        e = estado(cfg.data_dir, TIPO, fuente, simbolo, dia)  # confirma que el Parquet está
        if e in ("ok", "vacio"):
            hechos[dia] = e
    return hechos


class _Parar(Exception):
    """Detiene toda la corrida (disco o tiempo)."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def _bajar_simbolo(cfg: Config, sim: Simbolo, fuente: Fuente, catalogo: Catalogo | None,
                   desde: date | None, hasta: date, antes_de_pedir: Callable[[], None],
                   res: Resumen) -> None:
    nombre = fuente.nombre
    hechos = (catalogo.estados(TIPO, nombre, sim.simbolo) if catalogo
              else _hechos_del_almacen(cfg, nombre, sim.simbolo))
    piso = desde or PISO
    vacios_seguidos = 0
    errores_seguidos = 0
    mas_antiguo_ok: date | None = None
    escala_revisada = False
    dia = hasta
    while dia >= piso:
        previo = hechos.get(dia)
        if previo in ("ok", "vacio"):
            if previo == "ok":
                vacios_seguidos = 0
                mas_antiguo_ok = dia
            else:
                vacios_seguidos += 1
        else:
            antes_de_pedir()
            try:
                r = fuente.bajar_dia(sim, dia)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # un fallo de la fuente es un día con error
                from datos.fuentes.base import ResultadoDia
                r = ResultadoDia("error", None, f"{type(e).__name__}: {e}")
            if r.estado == "ok" and (r.tabla is None or r.tabla.num_rows == 0):
                r = type(r)("vacio", None)
            if r.estado == "error":
                res.dias_error += 1
                errores_seguidos += 1
                vacios_seguidos = 0
                if catalogo:
                    catalogo.registrar_error(TIPO, nombre, sim.simbolo, dia, r.error or "error")
                if errores_seguidos >= ERRORES_SEGUIDOS_PARA_PARAR:
                    res.simbolos_parados[sim.simbolo] = (
                        f"{errores_seguidos} días seguidos con error (último: {r.error})")
                    return
            else:
                errores_seguidos = 0
                if r.estado == "ok":
                    if not escala_revisada:
                        escala_revisada = True
                        mensaje = escala_razonable(sim, r.tabla)
                        if mensaje:
                            res.simbolos_parados[sim.simbolo] = mensaje
                            return
                    try:
                        m = escribir(cfg.data_dir, r.tabla, TIPO, nombre, sim.simbolo, dia)
                        if catalogo:
                            catalogo.registrar(m)
                    except Exception as e:
                        res.dias_error += 1
                        if catalogo:
                            catalogo.registrar_error(TIPO, nombre, sim.simbolo, dia,
                                                     f"{type(e).__name__}: {e}")
                    else:
                        res.dias_ok += 1
                        vacios_seguidos = 0
                        mas_antiguo_ok = dia
                else:
                    try:
                        m = marcar_vacio(cfg.data_dir, TIPO, nombre, sim.simbolo, dia)
                        if catalogo:
                            catalogo.registrar(m)
                    except Exception as e:
                        res.dias_error += 1
                        if catalogo:
                            catalogo.registrar_error(TIPO, nombre, sim.simbolo, dia,
                                                     f"{type(e).__name__}: {e}")
                    else:
                        res.dias_vacios += 1
                        vacios_seguidos += 1
        if desde is None and vacios_seguidos >= DIAS_VACIOS_PARA_PARAR:
            if mas_antiguo_ok and catalogo:
                catalogo.fijar_inicio(sim.simbolo, nombre, mas_antiguo_ok)
            return
        dia -= timedelta(days=1)


def bajar(cfg: Config, simbolos: list[Simbolo], fuente: Fuente, catalogo: Catalogo | None,
          desde: date | None = None, hasta: date | None = None,
          max_horas: float | None = None, hoy: date | None = None,
          reloj: Callable[[], float] = time.monotonic) -> Resumen:
    """Descarga los días pendientes de cada símbolo, de `hasta` hacia atrás."""
    hoy = hoy or datetime.now(timezone.utc).date()
    hasta = hasta or hoy - timedelta(days=1)
    res = Resumen()
    with candado(cfg.data_dir):
        corrida = catalogo.abrir_corrida("bajar") if catalogo else None
        inicio = reloj()

        def antes_de_pedir() -> None:
            if espacio_libre_gb(cfg.data_dir) < cfg.disco_min_gb:
                raise _Parar("disco")
            if max_horas is not None and reloj() - inicio >= max_horas * 3600:
                raise _Parar("tiempo")

        resultado = "ok"
        try:
            for sim in simbolos:
                if fuente.nombre not in sim.fuentes:
                    continue
                _bajar_simbolo(cfg, sim, fuente, catalogo, desde, hasta, antes_de_pedir, res)
        except _Parar as p:
            res.parado_por = p.motivo
        except KeyboardInterrupt:
            resultado = "interrumpida"
            raise
        finally:
            if corrida is not None:
                if resultado == "ok" and (res.parado_por or res.simbolos_parados
                                          or res.dias_error):
                    resultado = "parcial"
                catalogo.cerrar_corrida(corrida, resultado, asdict(res))
    return res


def imprimir(res: Resumen) -> None:
    print(f"Días descargados: {res.dias_ok}")
    print(f"Días vacíos: {res.dias_vacios}")
    print(f"Días con error: {res.dias_error}")
    for s, motivo in res.simbolos_parados.items():
        print(f"Símbolo parado {s}: {motivo}")
    if res.parado_por:
        print(f"Corrida detenida por: {res.parado_por}")
