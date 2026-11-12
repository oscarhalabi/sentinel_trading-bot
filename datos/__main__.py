"""CLI de datos: python -m datos <orden>."""
from __future__ import annotations

import argparse
import sys

ORDENES = {
    "simbolos": "Lista los símbolos disponibles",
    "bajar": "Descarga datos de mercado",
    "velas": "Construye velas a partir de ticks",
    "verificar": "Verifica la integridad de lo guardado",
    "catalogo": "Muestra o reconstruye el catálogo",
}


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m datos",
                                description="Almacén de datos históricos de mercado.")
    sub = p.add_subparsers(dest="orden", metavar="orden")
    for nombre, ayuda in ORDENES.items():
        sub.add_parser(nombre, help=ayuda)
    return p


def main(argv: list[str] | None = None) -> int:
    p = construir_parser()
    args = p.parse_args(argv)
    if args.orden is None:
        p.print_help()
        return 2
    print("Orden aún no disponible", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
