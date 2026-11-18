from datetime import date, datetime, timezone

import pyarrow as pa
import pytest

from datos.almacen import (
    ESQUEMA_CALENDARIO, ESQUEMA_COT, ESQUEMA_CORPORATIVO, ESQUEMA_ETF, ESQUEMA_SERIE,
    TIPOS, EsquemaInvalido, escribir, escribir_crudo, estado, leer_crudo,
    leer_manifiesto, listar_crudos, ruta_relativa,
)

T = datetime(2024, 3, 5, 13, 30, tzinfo=timezone.utc)
D = date(2024, 3, 5)


def _tabla_minima(esquema: pa.Schema) -> pa.Table:
    """Una fila con valores simples, del tipo de cada columna del esquema."""
    fila = {}
    for c in esquema:
        if pa.types.is_timestamp(c.type):
            fila[c.name] = [T]
        elif pa.types.is_date(c.type):
            fila[c.name] = [D]
        elif pa.types.is_boolean(c.type):
            fila[c.name] = [True]
        elif pa.types.is_floating(c.type):
            fila[c.name] = [1.5]
        else:
            fila[c.name] = ["x"]
    return pa.table(fila, schema=esquema)


def test_rutas_anio_y_completo():
    assert str(ruta_relativa("calendario", "fmp", "USD", date(2024, 6, 1))) == \
        "calendario/fuente=fmp/simbolo=USD/anio=2024.parquet"
    assert str(ruta_relativa("serie", "alfred", "PAYEMS", None)) == \
        "serie/fuente=alfred/simbolo=PAYEMS/completo.parquet"


def test_ticks_y_m1_no_cambian():
    assert str(ruta_relativa("ticks", "dukascopy", "XAUUSD", date(2024, 3, 5))) == \
        "ticks/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-03-05.parquet"
    assert str(ruta_relativa("m1", "dukascopy", "XAUUSD", date(2024, 3, 20))) == \
        "m1/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-03.parquet"


@pytest.mark.parametrize("tipo,esquema,periodo,texto", [
    ("calendario", ESQUEMA_CALENDARIO, D, "2024"),
    ("serie", ESQUEMA_SERIE, None, "completo"),
    ("cot", ESQUEMA_COT, D, "2024"),
    ("etf", ESQUEMA_ETF, None, "completo"),
    ("corporativo", ESQUEMA_CORPORATIVO, None, "completo"),
])
def test_escribe_y_estado_de_cada_tipo(tmp_path, tipo, esquema, periodo, texto):
    m = escribir(tmp_path, _tabla_minima(esquema), tipo, "f", "S", periodo)
    assert estado(tmp_path, tipo, "f", "S", periodo) == "ok"
    assert m.periodo == texto and m.filas == 1
    assert m.ts_min == "2024-03-05T13:30:00+00:00"
    assert leer_manifiesto(tmp_path, m.ruta).esquema_version == TIPOS[tipo].version


def test_tipos_y_particiones():
    assert {k: (v.columna_tiempo, v.particion) for k, v in TIPOS.items()} == {
        "ticks": ("ts", "dia"), "m1": ("ts", "mes"),
        "calendario": ("ts_evento", "anio"), "serie": ("ts_conocido", "completo"),
        "cot": ("ts_conocido", "anio"), "etf": ("ts_conocido", "completo"),
        "corporativo": ("ts_conocido", "completo"),
    }


def test_columna_tiempo_sin_zona_se_rechaza(tmp_path):
    t = _tabla_minima(ESQUEMA_CALENDARIO)
    i = t.schema.get_field_index("ts_evento")
    t = t.set_column(i, "ts_evento", t["ts_evento"].cast(pa.timestamp("us")))
    with pytest.raises(EsquemaInvalido):
        escribir(tmp_path, t, "calendario", "fmp", "USD", D)


def test_crudo_ida_y_vuelta(tmp_path):
    cuerpo = b'{"mensaje": "texto_plano_reconocible", "n": [1, 2, 3]}' * 50
    momento = datetime(2024, 3, 5, 13, 30, 15, tzinfo=timezone.utc)
    m = escribir_crudo(tmp_path, cuerpo, "fmp", "calendario_usd", momento, "json")
    assert m.ruta == "crudo/fuente=fmp/2024/03/05/133015_calendario_usd.json.zst"
    assert m.tipo == "crudo" and m.periodo == "2024-03-05" and m.filas == 0
    assert m.ts_min == m.ts_max == "2024-03-05T13:30:15+00:00"
    assert leer_crudo(tmp_path, m.ruta) == cuerpo
    en_disco = (tmp_path / m.ruta).read_bytes()
    assert en_disco[:4] == b"\x28\xb5\x2f\xfd"  # firma de zstd
    assert len(en_disco) < len(cuerpo) // 5  # el texto repetido se comprimió
    assert m.bytes == len(en_disco)
    escribir_crudo(tmp_path, b"{}", "otra", "x", momento, "json")
    assert [x.ruta for x in listar_crudos(tmp_path, "fmp")] == [m.ruta]
    assert len(list(listar_crudos(tmp_path))) == 2


def test_crudo_nombre_invalido(tmp_path):
    with pytest.raises(ValueError):
        escribir_crudo(tmp_path, b"x", "fmp", "../malo", T, "json")
