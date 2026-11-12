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
        sp = sub.add_parser(nombre, help=ayuda)
        if nombre == "simbolos":
            ssub = sp.add_subparsers(dest="accion", metavar="accion")
            d = ssub.add_parser("descubrir", help="Busca instrumentos nuevos en Dukascopy")
            d.add_argument("--aplicar", action="store_true",
                           help="Guarda los cambios (sin esto solo muestra el resumen)")
    return p


def _descubrir(aplicar: bool, fuente, ruta) -> int:
    from datos import simbolos as sim
    from datos.fuentes.dukascopy import Dukascopy

    fuente = fuente if fuente is not None else Dukascopy()
    ruta = ruta if ruta is not None else sim.RUTA_REGISTRO
    actuales = sim.cargar(ruta)
    try:
        descubiertos = fuente.listar_instrumentos()
    except Exception as e:
        print(f"No se pudo listar los instrumentos: {e}", file=sys.stderr)
        return 1
    nuevo, nuevos, desaparecidos = sim.fusionar_descubiertos(actuales, descubiertos, "dukascopy")
    cambiados = sorted(n for n in actuales if n in nuevo and nuevo[n] != actuales[n]
                       and n not in desaparecidos)
    print(f"Instrumentos en la fuente: {len(descubiertos)}")
    print(f"Sin clase (descartados): {getattr(fuente, 'sin_clase', 0)}")
    print(f"Nuevos: {len(nuevos)}")
    for n in nuevos:
        print(f"  + {n}")
    print(f"Desaparecidos (quedarían inactivos): {len(desaparecidos)}")
    for n in desaparecidos:
        print(f"  - {n}")
    print(f"Con cambios: {len(cambiados)}")
    for n in cambiados:
        print(f"  ~ {n}")
    if aplicar:
        sim.guardar(nuevo, ruta)
        print("Registro guardado.")
    else:
        print("Solo resumen: no se escribió nada. Usa --aplicar para guardar.")
    return 0


def main(argv: list[str] | None = None, fuente=None, ruta=None) -> int:
    p = construir_parser()
    args = p.parse_args(argv)
    if args.orden is None:
        p.print_help()
        return 2
    if args.orden == "simbolos" and getattr(args, "accion", None) == "descubrir":
        return _descubrir(args.aplicar, fuente, ruta)
    print("Orden aún no disponible", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
