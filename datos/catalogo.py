"""Catálogo Postgres: qué datos existen, qué corridas hubo y qué problemas se anotaron.

Las tablas `particiones` e `inicios` se pueden rehacer siempre desde los manifiestos
JSON del almacén (`reconstruir`). El resto (corridas, problemas, accesos) es historial.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping

import psycopg2
from psycopg2.extras import Json

from datos.almacen import Manifiesto, listar_manifiestos
from scripts.localdb_migrate import apply_pending

MIGRACIONES = Path(__file__).resolve().parent / "migrations"
RESERVA_JSONL = "reserva.jsonl"


def dias_pendientes(hechos: Mapping[date, str], desde: date, hasta: date) -> list[date]:
    """Días de `hasta` a `desde` (de hoy hacia atrás) que no estén `ok` ni `vacio`."""
    pendientes = []
    dia = hasta
    while dia >= desde:
        if hechos.get(dia) not in ("ok", "vacio"):
            pendientes.append(dia)
        dia -= timedelta(days=1)
    return pendientes


def _fecha_periodo(periodo: str) -> date:
    """'AAAA-MM-DD' -> ese día; 'AAAA-MM' -> el día 1 del mes."""
    partes = [int(p) for p in periodo.split("-")]
    return date(partes[0], partes[1], partes[2] if len(partes) > 2 else 1)


class Catalogo:
    """Una conexión por instancia. Usar `cerrar()` o `with Catalogo(dsn) as c:`."""

    def __init__(self, dsn: str):
        self._conn = psycopg2.connect(dsn, connect_timeout=5)

    def cerrar(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Catalogo":
        return self

    def __exit__(self, *_exc) -> None:
        self.cerrar()

    def _ejecutar(self, sql: str, params=()) -> int:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def _consultar(self, sql: str, params=()) -> list[tuple]:
        with self._conn, self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # --- migraciones ---
    def migrar(self) -> list[str]:
        return apply_pending(self._conn, str(MIGRACIONES))

    # --- particiones ---
    def estados(self, tipo: str, fuente: str, simbolo: str) -> dict[date, str]:
        filas = self._consultar(
            "select periodo, estado from particiones "
            "where tipo=%s and fuente=%s and simbolo=%s", (tipo, fuente, simbolo))
        return {_fecha_periodo(p): e for p, e in filas}

    _UPSERT = (
        "insert into particiones (tipo, fuente, simbolo, periodo, ruta, estado, filas, "
        "ts_min, ts_max, sha256, intentos, ultimo_error, actualizado) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,null,now()) "
        "on conflict (tipo, fuente, simbolo, periodo) do update set "
        "ruta=excluded.ruta, estado=excluded.estado, filas=excluded.filas, "
        "ts_min=excluded.ts_min, ts_max=excluded.ts_max, sha256=excluded.sha256, "
        "ultimo_error=null, actualizado=now()"
    )

    @staticmethod
    def _valores(m: Manifiesto) -> tuple:
        return (m.tipo, m.fuente, m.simbolo, m.periodo, m.ruta, m.estado, m.filas,
                m.ts_min, m.ts_max, m.sha256)

    def registrar(self, m: Manifiesto) -> None:
        self._ejecutar(self._UPSERT, self._valores(m))

    def registrar_error(self, tipo: str, fuente: str, simbolo: str,
                        periodo: date, error: str) -> None:
        texto = f"{periodo:%Y-%m-%d}" if tipo == "ticks" else f"{periodo:%Y-%m}"
        self._ejecutar(
            "insert into particiones (tipo, fuente, simbolo, periodo, estado, intentos, "
            "ultimo_error, actualizado) values (%s,%s,%s,%s,'error',1,%s,now()) "
            "on conflict (tipo, fuente, simbolo, periodo) do update set "
            "estado='error', intentos=particiones.intentos+1, "
            "ultimo_error=excluded.ultimo_error, actualizado=now()",
            (tipo, fuente, simbolo, texto, error))

    def fijar_inicio(self, simbolo: str, fuente: str, inicio: date) -> None:
        """Guarda el inicio; si ya había uno, se queda el más antiguo."""
        self._ejecutar(
            "insert into inicios (simbolo, fuente, inicio) values (%s,%s,%s) "
            "on conflict (simbolo, fuente) do update set "
            "inicio=least(inicios.inicio, excluded.inicio)", (simbolo, fuente, inicio))

    # --- corridas y problemas ---
    def abrir_corrida(self, orden: str) -> int:
        return self._consultar(
            "insert into corridas (orden) values (%s) returning id", (orden,))[0][0]

    def cerrar_corrida(self, id: int, resultado: str, resumen: dict) -> None:
        self._ejecutar("update corridas set fin=now(), resultado=%s, resumen=%s where id=%s",
                       (resultado, Json(resumen), id))

    def anotar_problema(self, tipo: str, simbolo: str, fuente: str, desde: datetime,
                        hasta: datetime, gravedad: str, detalle: dict) -> bool:
        """True si se anotó; False si ya existía el mismo problema."""
        n = self._ejecutar(
            "insert into problemas (tipo, simbolo, fuente, desde, hasta, gravedad, detalle) "
            "values (%s,%s,%s,%s,%s,%s,%s) "
            "on conflict (tipo, simbolo, fuente, desde) do nothing",
            (tipo, simbolo, fuente, desde, hasta, gravedad, Json(detalle)))
        return n == 1

    # --- símbolos ---
    def sincronizar_simbolos(self, simbolos: dict) -> None:
        with self._conn, self._conn.cursor() as cur:
            for nombre, s in simbolos.items():
                datos = asdict(s)
                cur.execute(
                    "insert into simbolos (simbolo, clase, prioridad, activo, datos, actualizado) "
                    "values (%s,%s,%s,%s,%s,now()) "
                    "on conflict (simbolo) do update set clase=excluded.clase, "
                    "prioridad=excluded.prioridad, activo=excluded.activo, "
                    "datos=excluded.datos, actualizado=now()",
                    (nombre, s.clase, s.prioridad, s.activo, Json(datos)))

    # --- reserva ---
    def importar_accesos_reserva(self, raiz: Path) -> int:
        """Importa `reserva.jsonl` (idempotente). Devuelve cuántas filas nuevas entraron."""
        ruta = Path(raiz) / RESERVA_JSONL
        if not ruta.is_file():
            return 0
        nuevas = 0
        with self._conn, self._conn.cursor() as cur:
            for linea in ruta.read_text(encoding="utf-8").splitlines():
                if not linea.strip():
                    continue
                a = json.loads(linea)
                cur.execute(
                    "insert into accesos_reserva (momento, quien, simbolo, desde, hasta) "
                    "values (%s,%s,%s,%s,%s) on conflict (momento, quien, simbolo) do nothing",
                    (a["momento"], a["quien"], a["simbolo"], a.get("desde"), a.get("hasta")))
                nuevas += cur.rowcount
        return nuevas

    # --- reconstrucción ---
    def reconstruir(self, raiz: Path) -> int:
        """Vacía `particiones` e `inicios` y los rehace desde los manifiestos."""
        n = 0
        with self._conn, self._conn.cursor() as cur:
            cur.execute("delete from particiones")
            cur.execute("delete from inicios")
            for m in listar_manifiestos(raiz):
                cur.execute(self._UPSERT, self._valores(m))
                n += 1
            cur.execute(
                "insert into inicios (simbolo, fuente, inicio) "
                "select simbolo, fuente, min(to_date(periodo || case when length(periodo)=7 "
                "then '-01' else '' end, 'YYYY-MM-DD')) from particiones "
                "where estado='ok' group by simbolo, fuente")
        return n

    # --- resumen ---
    def resumen_estado(self) -> list[tuple]:
        """Por (simbolo, fuente, tipo): ok, vacio, error, inicio, primer y último periodo."""
        return self._consultar(
            "select p.simbolo, p.fuente, p.tipo, "
            "count(*) filter (where p.estado='ok'), count(*) filter (where p.estado='vacio'), "
            "count(*) filter (where p.estado='error'), min(i.inicio), min(p.periodo), max(p.periodo) "
            "from particiones p left join inicios i on i.simbolo=p.simbolo and i.fuente=p.fuente "
            "group by p.simbolo, p.fuente, p.tipo order by 1,2,3")
