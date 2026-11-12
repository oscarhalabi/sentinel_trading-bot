import json
import shutil
from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from datos.almacen import (
    EsquemaInvalido, escribir, espacio_libre_gb, estado, leer_manifiesto,
    listar_manifiestos, marcar_vacio, ruta_relativa,
)
from tests.datos.ayudas import m1_sintetica, ticks_sinteticos

INICIO = datetime(2024, 3, 5, 10, 0, tzinfo=timezone.utc)
DIA = date(2024, 3, 5)
ARGS = ("ticks", "dukascopy", "XAUUSD", DIA)


def test_escribe_y_lee(tmp_path):
    t = ticks_sinteticos(INICIO, 100)
    m = escribir(tmp_path, t.slice(0).take(list(reversed(range(100)))), *ARGS)
    rel = ruta_relativa(*ARGS)
    assert str(rel) == "ticks/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-03-05.parquet"
    leida = pq.read_table(tmp_path / rel)
    assert leida.num_rows == 100
    ts = leida["ts"].to_pylist()
    assert ts == sorted(ts)
    assert m.estado == "ok" and m.filas == 100
    assert m.ts_min == "2024-03-05T10:00:00+00:00"
    assert m.ts_max == ts[-1].isoformat()
    assert m.periodo == "2024-03-05"
    assert estado(tmp_path, *ARGS) == "ok"


def test_m1_periodo_mensual(tmp_path):
    m = escribir(tmp_path, m1_sintetica(INICIO, 5), "m1", "dukascopy", "XAUUSD", DIA)
    assert m.periodo == "2024-03"
    assert str(ruta_relativa("m1", "dukascopy", "XAUUSD", date(2024, 3, 20))) == \
        "m1/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-03.parquet"
    assert estado(tmp_path, "m1", "dukascopy", "XAUUSD", date(2024, 3, 31)) == "ok"


def test_ts_sin_zona_se_rechaza(tmp_path):
    t = ticks_sinteticos(INICIO, 3)
    t = t.set_column(0, "ts", t["ts"].cast(pa.timestamp("us")))
    with pytest.raises(EsquemaInvalido):
        escribir(tmp_path, t, *ARGS)


def test_falta_columna_se_rechaza(tmp_path):
    t = ticks_sinteticos(INICIO, 3).drop_columns(["ask"])
    with pytest.raises(EsquemaInvalido):
        escribir(tmp_path, t, *ARGS)


def test_escritura_interrumpida_se_rehace(tmp_path):
    rel = ruta_relativa(*ARGS)
    destino = tmp_path / rel
    destino.parent.mkdir(parents=True)
    pq.write_table(ticks_sinteticos(INICIO, 5), destino)
    assert estado(tmp_path, *ARGS) == "incompleto"
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    assert estado(tmp_path, *ARGS) == "ok"


def test_falta(tmp_path):
    assert estado(tmp_path, *ARGS) == "falta"


def test_hash_distinto_es_incompleto(tmp_path):
    escribir(tmp_path, ticks_sinteticos(INICIO, 50), *ARGS)
    p = tmp_path / ruta_relativa(*ARGS)
    datos = bytearray(p.read_bytes())
    datos[len(datos) // 2] ^= 0xFF
    p.write_bytes(bytes(datos))
    assert estado(tmp_path, *ARGS) == "ok"  # rápido: mismo tamaño
    assert estado(tmp_path, *ARGS, verificar_hash=True) == "incompleto"


def test_parquet_borrado_es_incompleto(tmp_path):
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    (tmp_path / ruta_relativa(*ARGS)).unlink()
    assert estado(tmp_path, *ARGS) == "incompleto"


def test_dia_vacio(tmp_path):
    m = marcar_vacio(tmp_path, *ARGS)
    assert m.estado == "vacio" and m.filas == 0 and m.sha256 is None
    assert estado(tmp_path, *ARGS) == "vacio"
    assert not (tmp_path / ruta_relativa(*ARGS)).exists()


def test_marcar_vacio_con_parquet_falla(tmp_path):
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    with pytest.raises(ValueError):
        marcar_vacio(tmp_path, *ARGS)


def test_reescribir_guarda_hash_anterior(tmp_path):
    m1 = escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    m2 = escribir(tmp_path, ticks_sinteticos(INICIO, 8), *ARGS)
    assert m2.sha256 != m1.sha256
    assert m2.hashes_anteriores == [m1.sha256]
    assert leer_manifiesto(tmp_path, str(ruta_relativa(*ARGS))).hashes_anteriores == [m1.sha256]


def test_mover_data_dir_sigue_funcionando(tmp_path):
    a = tmp_path / "a"
    escribir(a, ticks_sinteticos(INICIO, 5), *ARGS)
    b = tmp_path / "b"
    shutil.move(str(a), str(b))
    assert estado(b, *ARGS) == "ok"
    assert leer_manifiesto(b, str(ruta_relativa(*ARGS))).filas == 5


def test_rutas_relativas(tmp_path):
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    marcar_vacio(tmp_path, "ticks", "dukascopy", "XAUUSD", date(2024, 3, 6))
    for f in (tmp_path / "_manifiestos").rglob("*.json"):
        texto = f.read_text()
        assert str(tmp_path) not in texto
        assert "\\" not in json.loads(texto)["ruta"]


def test_listar_manifiestos(tmp_path):
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    escribir(tmp_path, m1_sintetica(INICIO, 5), "m1", "dukascopy", "XAUUSD", DIA)
    assert len(list(listar_manifiestos(tmp_path))) == 2
    assert [m.tipo for m in listar_manifiestos(tmp_path, "m1")] == ["m1"]
    assert list(listar_manifiestos(tmp_path / "nada")) == []


def test_espacio_libre(tmp_path):
    assert espacio_libre_gb(tmp_path) > 0


# --- correcciones de la revisión final ---

def test_manifiesto_truncado_es_incompleto_y_listar_no_revienta(tmp_path):
    from datos.almacen import ManifiestoIlegible, _ruta_manifiesto
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    otro = ("ticks", "dukascopy", "XAUUSD", date(2024, 3, 6))
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *otro)
    p = _ruta_manifiesto(tmp_path, ruta_relativa(*ARGS))
    p.write_text(p.read_text(encoding="utf-8")[:40], encoding="utf-8")  # JSON cortado
    with pytest.warns(ManifiestoIlegible, match="ilegible"):
        assert estado(tmp_path, *ARGS) == "incompleto"
    with pytest.warns(ManifiestoIlegible):
        assert [m.periodo for m in listar_manifiestos(tmp_path)] == ["2024-03-06"]
    with pytest.warns(ManifiestoIlegible):
        assert leer_manifiesto(tmp_path, str(ruta_relativa(*ARGS))) is None
    # Sin Parquet, un manifiesto ilegible cuenta como que falta.
    (tmp_path / ruta_relativa(*ARGS)).unlink()
    with pytest.warns(ManifiestoIlegible):
        assert estado(tmp_path, *ARGS) == "falta"
    # Y se puede reescribir encima sin problema.
    with pytest.warns(ManifiestoIlegible):
        escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    assert estado(tmp_path, *ARGS) == "ok"


def test_manifiesto_con_clave_desconocida_se_lee(tmp_path):
    from datos.almacen import _ruta_manifiesto
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)
    p = _ruta_manifiesto(tmp_path, ruta_relativa(*ARGS))
    datos = json.loads(p.read_text(encoding="utf-8"))
    datos["campo_del_futuro"] = 123
    p.write_text(json.dumps(datos), encoding="utf-8")
    assert leer_manifiesto(tmp_path, str(ruta_relativa(*ARGS))).filas == 5
    assert estado(tmp_path, *ARGS) == "ok"
    assert len(list(listar_manifiestos(tmp_path))) == 1


def test_fsync_antes_de_renombrar(tmp_path, monkeypatch):
    from datos import almacen
    eventos = []
    fsync_real, replace_real = almacen.os.fsync, almacen.os.replace
    monkeypatch.setattr(almacen.os, "fsync", lambda fd: (eventos.append("fsync"), fsync_real(fd)))
    monkeypatch.setattr(almacen.os, "replace",
                        lambda a, b: (eventos.append("replace"), replace_real(a, b)))
    escribir(tmp_path, ticks_sinteticos(INICIO, 5), *ARGS)  # Parquet y manifiesto
    marcar_vacio(tmp_path, "ticks", "dukascopy", "XAUUSD", date(2024, 3, 6))  # manifiesto
    assert eventos == ["fsync", "replace"] * 3
    assert estado(tmp_path, *ARGS) == "ok"
