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
        if nombre == "catalogo":
            csub = sp.add_subparsers(dest="accion", metavar="accion")
            csub.add_parser("migrar", help="Crea o actualiza las tablas del catálogo")
            csub.add_parser("reconstruir", help="Rehace el catálogo desde los manifiestos")
            csub.add_parser("estado", help="Resume lo que hay por símbolo")
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
    if not descubiertos:
        print("La fuente devolvió una lista vacía o sin instrumentos reconocibles. "
              "No se toca el registro.", file=sys.stderr)
        return 1
    crudos = getattr(fuente, "codigos_crudos", None) or None
    nuevo, nuevos, desaparecidos = sim.fusionar_descubiertos(
        actuales, descubiertos, "dukascopy", crudos)
    reaparecidos = sorted(n for n in actuales if not actuales[n].activo and nuevo[n].activo)
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
    print(f"Reaparecidos (vuelven a activo): {len(reaparecidos)}")
    for n in reaparecidos:
        print(f"  * {n}")
    con_codigo = [s for s in actuales.values() if s.activo and "dukascopy" in s.fuentes]
    if con_codigo and len(desaparecidos) > 0.2 * len(con_codigo):
        print(f"ATENCIÓN: desaparecerían {len(desaparecidos)} de {len(con_codigo)} símbolos "
              "activos (más del 20 %). Parece un cambio de formato de la fuente, no se "
              "escribe nada.", file=sys.stderr)
        return 1
    if aplicar:
        sim.guardar(nuevo, ruta)
        print("Registro guardado.")
    else:
        print("Solo resumen: no se escribió nada. Usa --aplicar para guardar.")
    return 0


def _catalogo(accion: str, config=None) -> int:
    from datetime import date

    from datos.catalogo import Catalogo, dias_pendientes
    from datos.config import cargar_config

    cfg = config if config is not None else cargar_config()
    if not cfg.pg_dsn:
        print("Falta DATOS_PG_DSN: no hay base de datos del catálogo configurada.",
              file=sys.stderr)
        return 1
    try:
        cat = Catalogo(cfg.pg_dsn)
    except Exception as e:  # el mensaje de psycopg2 puede traer el DSN: no se imprime
        print(f"No se pudo conectar al Postgres del catálogo ({type(e).__name__}).",
              file=sys.stderr)
        return 1
    with cat:
        if accion == "migrar":
            hechas = cat.migrar()
            print("Migraciones aplicadas:", ", ".join(hechas) if hechas else "ninguna (al día)")
        elif accion == "reconstruir":
            print(f"Catálogo reconstruido: {cat.reconstruir(cfg.data_dir)} particiones.")
        else:
            filas = cat.resumen_estado()
            if not filas:
                print("El catálogo está vacío.")
            for sim, fuente, tipo, ok, vacio, error, inicio, p_min, p_max in filas:
                faltan = "n/d"
                if tipo == "ticks" and inicio:
                    hechos = cat.estados(tipo, fuente, sim)
                    faltan = sum(1 for d in dias_pendientes(hechos, inicio, date.today())
                                 if d not in hechos)
                print(f"{sim} [{fuente}/{tipo}]: ok={ok} vacio={vacio} error={error} "
                      f"faltantes={faltan} inicio={inicio or '-'}")
    return 0


def main(argv: list[str] | None = None, fuente=None, ruta=None) -> int:
    p = construir_parser()
    args = p.parse_args(argv)
    if args.orden is None:
        p.print_help()
        return 2
    if args.orden == "simbolos" and getattr(args, "accion", None) == "descubrir":
        return _descubrir(args.aplicar, fuente, ruta)
    if args.orden == "catalogo":
        if getattr(args, "accion", None) is None:
            print("Falta la acción: migrar, reconstruir o estado.", file=sys.stderr)
            return 2
        return _catalogo(args.accion)
    print("Orden aún no disponible", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
