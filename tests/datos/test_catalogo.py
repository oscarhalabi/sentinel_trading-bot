import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from datos.almacen import escribir, listar_manifiestos
from datos.catalogo import Catalogo, dias_pendientes
from tests.datos.ayudas import ticks_sinteticos

DSN_TEST = os.environ.get("DATOS_PG_DSN_TEST")
base_real = pytest.mark.skipif(not DSN_TEST, reason="Falta DATOS_PG_DSN_TEST")

TABLAS = ["accesos_reserva", "problemas", "corridas", "particiones", "inicios",
          "simbolos", "fuentes", "schema_migrations"]


# --- función pura ----------------------------------------------------------

def test_dias_pendientes_de_hoy_hacia_atras():
    hechos = {date(2024, 3, 4): "ok", date(2024, 3, 5): "ok", date(2024, 3, 6): "ok"}
    r = dias_pendientes(hechos, date(2024, 3, 1), date(2024, 3, 8))
    assert r == [date(2024, 3, 8), date(2024, 3, 7), date(2024, 3, 3),
                 date(2024, 3, 2), date(2024, 3, 1)]


def test_dias_pendientes_ignora_vacios():
    hechos = {date(2024, 3, 2): "vacio", date(2024, 3, 3): "error"}
    r = dias_pendientes(hechos, date(2024, 3, 1), date(2024, 3, 3))
    assert r == [date(2024, 3, 3), date(2024, 3, 1)]


# --- base real -------------------------------------------------------------

@pytest.fixture
def cat():
    c = Catalogo(DSN_TEST)
    _limpiar(c)
    try:
        c.migrar()
        yield c
    finally:
        _limpiar(c)
        c.cerrar()


def _limpiar(c):
    with c._conn, c._conn.cursor() as cur:
        for t in TABLAS:
            cur.execute(f"drop table if exists {t} cascade")


def _filas(c, sql, params=()):
    with c._conn, c._conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _escribir_dia(raiz, dia):
    ini = datetime(dia.year, dia.month, dia.day, 10, tzinfo=timezone.utc)
    return escribir(raiz, ticks_sinteticos(ini, 20), "ticks", "dukascopy", "XAUUSD", dia)


@base_real
def test_migrar_es_idempotente(cat):
    assert cat.migrar() == []
    assert len(_filas(cat, "select 1 from schema_migrations")) == 1


@base_real
def test_registrar_upsert(cat, tmp_path):
    m = _escribir_dia(tmp_path, date(2024, 3, 5))
    cat.registrar(m)
    cat.registrar(m)
    filas = _filas(cat, "select estado, filas, ruta from particiones")
    assert filas == [("ok", 20, m.ruta)]
    assert cat.estados("ticks", "dukascopy", "XAUUSD") == {date(2024, 3, 5): "ok"}


@base_real
def test_registrar_error_cuenta_intentos(cat):
    d = date(2024, 3, 5)
    cat.registrar_error("ticks", "dukascopy", "XAUUSD", d, "fallo 1")
    cat.registrar_error("ticks", "dukascopy", "XAUUSD", d, "fallo 2")
    assert _filas(cat, "select estado, intentos, ultimo_error from particiones") == \
        [("error", 2, "fallo 2")]
    assert cat.estados("ticks", "dukascopy", "XAUUSD") == {d: "error"}


@base_real
def test_problema_no_se_duplica(cat):
    d = datetime(2024, 3, 5, tzinfo=timezone.utc)
    args = ("hueco", "XAUUSD", "dukascopy", d, d + timedelta(hours=1), "media", {"a": 1})
    assert cat.anotar_problema(*args) is True
    assert cat.anotar_problema(*args) is False
    assert len(_filas(cat, "select 1 from problemas")) == 1


@base_real
def test_corridas(cat):
    i = cat.abrir_corrida("bajar")
    cat.cerrar_corrida(i, "ok", {"dias": 3})
    assert _filas(cat, "select orden, resultado, resumen->>'dias', fin is not null from corridas") \
        == [("bajar", "ok", "3", True)]


@base_real
def test_inicio_y_simbolos(cat):
    cat.fijar_inicio("XAUUSD", "dukascopy", date(2020, 1, 2))
    cat.fijar_inicio("XAUUSD", "dukascopy", date(2019, 1, 2))
    assert _filas(cat, "select inicio from inicios") == [(date(2019, 1, 2),)]
    from datos.simbolos import cargar
    cat.sincronizar_simbolos(cargar())
    cat.sincronizar_simbolos(cargar())
    n = _filas(cat, "select count(*) from simbolos")[0][0]
    assert n > 0
    assert _filas(cat, "select 1 from simbolos where simbolo='XAUUSD'")


@base_real
def test_accesos_reserva_idempotente(cat, tmp_path):
    assert cat.importar_accesos_reserva(tmp_path) == 0  # sin archivo
    linea = {"momento": "2026-11-12T10:00:00+00:00", "quien": "oscar", "simbolo": "XAUUSD",
             "desde": "2025-11-12T00:00:00+00:00", "hasta": "2025-12-12T00:00:00+00:00"}
    (tmp_path / "_registro").mkdir()
    (tmp_path / "_registro" / "reserva.jsonl").write_text(json.dumps(linea) + "\n",
                                                          encoding="utf-8")
    assert cat.importar_accesos_reserva(tmp_path) == 1
    assert cat.importar_accesos_reserva(tmp_path) == 0
    assert len(_filas(cat, "select 1 from accesos_reserva")) == 1


@base_real
def test_catalogo_se_reconstruye_desde_manifiestos(cat, tmp_path):
    for d in (date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 7)):
        _escribir_dia(tmp_path, d)
    cat.registrar_error("ticks", "dukascopy", "XAUUSD", date(2020, 1, 1), "basura vieja")
    assert cat.reconstruir(tmp_path) == 3
    assert _filas(cat, "select estado, count(*) from particiones group by estado") == [("ok", 3)]
    assert _filas(cat, "select simbolo, fuente, inicio from inicios") == \
        [("XAUUSD", "dukascopy", date(2024, 3, 5))]
    assert len(list(listar_manifiestos(tmp_path))) == 3


def test_cli_catalogo_sin_dsn_da_error_claro(capsys):
    from datos.__main__ import _catalogo
    from datos.config import Config
    cfg = Config(data_dir=None, pg_dsn="", disco_min_gb=50, corte_reserva=None)
    assert _catalogo("estado", cfg) == 1
    assert "DATOS_PG_DSN" in capsys.readouterr().err


# --- correcciones de la revisión final (base real) ---

def _cfg_real(tmp_path):
    from datos.config import Config
    return Config(data_dir=tmp_path, pg_dsn=DSN_TEST, disco_min_gb=0, corte_reserva=None)


def _acceso_desde_consulta(tmp_path):
    """Escribe una línea de reserva como lo hace de verdad la consulta."""
    from datos import consulta
    d = datetime(2025, 12, 1, tzinfo=timezone.utc)
    consulta._registrar_acceso(_cfg_real(tmp_path), "XAUUSD", d, d + timedelta(days=30))


@base_real
def test_importa_la_reserva_que_escribe_la_consulta(cat, tmp_path):
    _acceso_desde_consulta(tmp_path)
    assert cat.importar_accesos_reserva(tmp_path) == 1
    assert _filas(cat, "select simbolo from accesos_reserva") == [("XAUUSD",)]


@base_real
@pytest.mark.parametrize("accion", ["reconstruir", "estado"])
def test_cli_catalogo_importa_accesos_reserva(cat, tmp_path, accion, capsys):
    from datos.__main__ import main
    _acceso_desde_consulta(tmp_path)
    assert main(["catalogo", accion], config=_cfg_real(tmp_path)) == 0
    assert len(_filas(cat, "select 1 from accesos_reserva")) == 1
    assert "postgresql://" not in capsys.readouterr().out


@base_real
def test_cli_verificar_importa_accesos_reserva(cat, tmp_path):
    from datos.__main__ import main
    from datos.simbolos import Simbolo, guardar
    ruta = tmp_path / "reg.json"
    guardar({"XAUUSD": Simbolo("XAUUSD", "metal", 1, {"dukascopy": "XAUUSD"}, 3, "metal",
                               "USD")}, ruta)
    _acceso_desde_consulta(tmp_path)
    assert main(["verificar"], ruta=ruta, config=_cfg_real(tmp_path)) == 0
    assert len(_filas(cat, "select 1 from accesos_reserva")) == 1


@base_real
def test_reconstruir_inicio_solo_cuenta_ticks(cat, tmp_path):
    from tests.datos.ayudas import m1_sintetica
    _escribir_dia(tmp_path, date(2024, 3, 5))
    escribir(tmp_path, m1_sintetica(datetime(2024, 2, 20, tzinfo=timezone.utc), 5), "m1",
             "dukascopy", "XAUUSD", date(2024, 2, 1))  # mes que empieza antes de los ticks
    assert cat.reconstruir(tmp_path) == 2
    assert _filas(cat, "select simbolo, fuente, inicio from inicios") == \
        [("XAUUSD", "dukascopy", date(2024, 3, 5))]
