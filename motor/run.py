"""Entrada unica del bot: resuelve el motor de BOT_ID en engines.json y lo arranca.

Uso:  python -m motor.run --mode {console,lite,tui} [--root <ruta del repo>]

--root es solo una marca en la linea de comandos para que los scripts puedan
reconocer los procesos de este repo; si se pasa, debe ser la raiz del repo.
"""

import argparse
import os
import sys

from . import config, registry
from . import main as botmain


def _raiz_repo():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(ruta):
    return os.path.normcase(os.path.realpath(ruta))


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

    if args.mode == "lite":
        from . import lite
        lite.run_lite(engine_cls=engine_cls)
    elif args.mode == "tui":
        from . import tui
        tui.run_tui(engine_cls=engine_cls)
    else:
        botmain.run(engine_cls=engine_cls)


if __name__ == "__main__":
    main(sys.argv[1:])
