"""Entrada unica del bot: resuelve el motor de BOT_ID en engines.json y lo arranca.

Uso:  python -m motor.run --mode {console,lite,tui} [--root <ruta del repo>]

--root es solo una marca en la linea de comandos para que los scripts puedan
reconocer los procesos de este repo; si se pasa, debe ser la raiz del repo.

Una sola instancia por (USER_ID, BOT_ID): antes de arrancar se toma un candado
(mutex con nombre de Windows; si falla, un archivo bloqueado en logs/). Si ya
lo tiene otro proceso, se avisa y se sale con codigo 0, asi el vigilante no lo
cuenta como caida y no se duplican ordenes en la misma cuenta y magic.
"""

import argparse
import ctypes
import os
import re
import sys

from . import config, registry
from . import main as botmain


def _raiz_repo():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(ruta):
    return os.path.normcase(os.path.realpath(ruta))


# --- Candado de una sola instancia ------------------------------------------

_ERROR_ACCESS_DENIED = 5
_ERROR_ALREADY_EXISTS = 183


class Candado:
    """Candado tomado. Hay que guardarlo vivo mientras corre el motor."""

    def __init__(self, tipo, recurso):
        self.tipo = tipo          # "mutex", "archivo" o "ninguno"
        self.recurso = recurso    # handle del mutex o archivo abierto


def _nombre_candado(user_id, bot_id):
    return "sentinel_motor_" + re.sub(r"[^\w.-]", "_", f"{user_id}_{bot_id}".lower())


def _kernel32():
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k32


def _mutex(nombre):
    """Crea el mutex con nombre. Devuelve su handle, o None si ya existia.

    Se usa el espacio Global para que tambien choquen dos motores lanzados
    desde sesiones distintas del servidor (p.ej. dos escritorios remotos).
    Lanza OSError si no se puede crear por otro motivo.
    """
    k32 = _kernel32()
    h = k32.CreateMutexW(None, False, "Global\\" + nombre)
    err = ctypes.get_last_error()
    if not h:
        if err == _ERROR_ACCESS_DENIED:   # existe y es de otro usuario de Windows
            return None
        raise OSError(err, "CreateMutexW fallo")
    if err == _ERROR_ALREADY_EXISTS:
        k32.CloseHandle(h)
        return None
    return h


def _carpeta_candados():
    return os.path.join(_raiz_repo(), "logs")


def _archivo_bloqueado(nombre):
    """Plan B: archivo en logs/ bloqueado con msvcrt. Devuelve el archivo o None."""
    import msvcrt
    carpeta = _carpeta_candados()
    os.makedirs(carpeta, exist_ok=True)
    f = open(os.path.join(carpeta, nombre + ".lock"), "a+b")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def adquirir_candado(user_id, bot_id):
    """Toma el candado de (user_id, bot_id). Devuelve un Candado, o None si ya esta tomado."""
    nombre = _nombre_candado(user_id, bot_id)
    try:
        h = _mutex(nombre)
        return Candado("mutex", h) if h else None
    except (OSError, AttributeError) as e:  # AttributeError: no es Windows
        print(f"[AVISO] no se pudo crear el mutex del motor ({e}); se usa un archivo en logs/.")
    try:
        f = _archivo_bloqueado(nombre)
        return Candado("archivo", f) if f else None
    except (OSError, ImportError) as e:
        print(f"[AVISO] tampoco se pudo bloquear el archivo ({e}): el motor arranca sin candado.")
        return Candado("ninguno", None)


def liberar_candado(candado):
    """Suelta el candado (al cerrar el proceso se suelta solo de todas formas)."""
    if candado is None or candado.recurso is None:
        return
    if candado.tipo == "mutex":
        _kernel32().CloseHandle(candado.recurso)
    elif candado.tipo == "archivo":
        import msvcrt
        try:
            candado.recurso.seek(0)
            msvcrt.locking(candado.recurso.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        candado.recurso.close()
    candado.recurso = None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="motor.run")
    parser.add_argument("--mode", choices=("console", "lite", "tui"), default="console")
    parser.add_argument("--root", default=None,
                        help="ruta absoluta de la raiz del repo (marca para identificar el proceso)")
    args = parser.parse_args(argv)

    if args.root is not None and _norm(args.root) != _norm(_raiz_repo()):
        raise SystemExit(
            f"[ABORTADO] --root '{args.root}' no es la raiz de este repo ({_raiz_repo()})."
        )

    reg = registry.load_registry()
    if config.BOT_ID not in reg:
        raise SystemExit(
            f"[ABORTADO] BOT_ID '{config.BOT_ID}' no esta en engines.json. "
            f"Disponibles: {', '.join(sorted(reg))}."
        )
    engine_cls = registry.load_engine_class(reg[config.BOT_ID])

    # El candado vive hasta que el motor termina (y el proceso con el).
    candado = adquirir_candado(config.USER_ID, config.BOT_ID)
    if candado is None:
        print(f"[AVISO] ya hay un motor {config.BOT_ID} corriendo para este usuario "
              f"({config.USER_ID}); no se arranca otro.")
        return 0
    try:
        if args.mode == "lite":
            from . import lite
            lite.run_lite(engine_cls=engine_cls)
        elif args.mode == "tui":
            from . import tui
            tui.run_tui(engine_cls=engine_cls)
        else:
            botmain.run(engine_cls=engine_cls)
    finally:
        liberar_candado(candado)
    return 0


if __name__ == "__main__":
    main(sys.argv[1:])
