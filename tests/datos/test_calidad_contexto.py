"""Pruebas de la verificación de contexto y del tamaño por tipo en el informe."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

from datos.almacen import (ESQUEMA_CALENDARIO, ESQUEMA_COT, ESQUEMA_SERIE, escribir,
                           escribir_crudo)
from datos.calidad import escribir_informe
from datos.calidad_contexto import verificar_contexto
from datos.config import cargar_config


def _t(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, **extra):
    return cargar_config({"DATA_DIR": str(tmp_path), "DATOS_CORTE_RESERVA": "2030-01-01", **extra})


def _evento(ts_evento, ts_version, fuente="fmp", id_fuente="1", evento="US_DESEMPLEO",
            real=None, prevision=None, categoria="empleo"):
    return {
        "ts_evento": ts_evento, "ts_anunciado": ts_version, "ts_conocido": ts_evento,
        "ts_version": ts_version, "ts_foto": None, "fuente": fuente, "id_fuente": id_fuente,
        "pais": None, "divisa": "USD", "evento": evento, "evento_fuente": evento,
        "categoria": categoria, "impacto": "alto", "unidad": "%", "programado": True,
        "hora_exacta": True, "prevision_es_pit": False, "real": real, "prevision": prevision,
        "anterior": None, "anterior_revisado": None,
    }


def _guardar_calendario(raiz, filas, fuente="fmp"):
    tabla = pa.Table.from_pylist(filas, schema=ESQUEMA_CALENDARIO)
    escribir(raiz, tabla, "calendario", fuente, "USD", date(filas[0]["ts_evento"].year, 1, 1))


def _serie(raiz, filas, fuente="alfred", sid="UNRATE"):
    base = {"serie": sid, "ts_hasta": None, "fuente": fuente, "publicacion_estimada": False}
    tabla = pa.Table.from_pylist([{**base, **f} for f in filas], schema=ESQUEMA_SERIE)
    escribir(raiz, tabla, "serie", fuente, sid, None)


def _tipos(problemas):
    return {p.tipo for p in problemas}


def test_falta_foto_ff(tmp_path):
    for h in (0, 4, 8, 20):   # entre las 8 y las 20 faltan fotos (12 h > 4 × 1,5)
        escribir_crudo(tmp_path, b"[]", "forexfactory", "ff_thisweek", _t(2024, 5, 1, h), "json")
    r = [p for p in verificar_contexto(_cfg(tmp_path), None) if p.tipo == "ff_foto_faltante"]
    assert len(r) == 1
    assert r[0].gravedad == "aviso" and r[0].simbolo == "ff" and r[0].fuente == "forexfactory"
    assert (r[0].desde, r[0].hasta) == (_t(2024, 5, 1, 8), _t(2024, 5, 1, 20))


def test_alfred_fechas_incoherentes_grave(tmp_path):
    _serie(tmp_path, [
        {"periodo": date(2024, 1, 1), "valor": 3.7, "ts_conocido": _t(2024, 2, 2, 13, 30),
         "ts_hasta": _t(2024, 2, 2, 13, 30)},                       # ts_hasta == ts_conocido
        {"periodo": date(2024, 2, 1), "valor": 3.9, "ts_conocido": _t(2024, 3, 8, 13, 30)},
    ])
    r = [p for p in verificar_contexto(_cfg(tmp_path), None) if p.tipo == "alfred_fechas"]
    assert len(r) == 1 and r[0].gravedad == "grave" and r[0].simbolo == "UNRATE"


def test_real_api_distinto_de_alfred_grave(tmp_path):
    pub = _t(2024, 3, 8, 13, 30)
    _serie(tmp_path, [{"periodo": date(2024, 2, 1), "valor": 3.9, "ts_conocido": pub}])
    _guardar_calendario(tmp_path, [
        _evento(pub, pub, id_fuente="a", real=3.7),                     # distinto
        _evento(pub + timedelta(days=30), pub + timedelta(days=30), id_fuente="b", real=3.9),
    ])
    r = [p for p in verificar_contexto(_cfg(tmp_path), None) if p.tipo == "real_api_vs_alfred"]
    assert len(r) == 1 and r[0].gravedad == "grave"
    assert r[0].detalle["real_api"] == 3.7 and r[0].detalle["alfred"] == 3.9


def test_real_api_igual_a_alfred_no_es_problema(tmp_path):
    pub = _t(2024, 3, 8, 13, 30)
    _serie(tmp_path, [{"periodo": date(2024, 2, 1), "valor": 3.9, "ts_conocido": pub}])
    _guardar_calendario(tmp_path, [_evento(pub, pub, real=3.9)])
    assert "real_api_vs_alfred" not in _tipos(verificar_contexto(_cfg(tmp_path), None))


def test_cot_semana_faltante(tmp_path):
    martes = [date(2024, 5, 7), date(2024, 5, 14), date(2024, 5, 28)]   # falta el 21
    base = {"mercado": "oro", "informe": "legacy", "campo": "interes_abierto",
            "ts_conocido": None, "ts_version": _t(2024, 6, 1), "publicacion_tardia": False}
    filas = [{**base, "fecha_informe": d, "valor": 1.0,
              "ts_conocido": _t(d.year, d.month, d.day) + timedelta(days=3)} for d in martes]
    escribir(tmp_path, pa.Table.from_pylist(filas, schema=ESQUEMA_COT), "cot", "cftc", "oro",
             date(2024, 1, 1))
    r = [p for p in verificar_contexto(_cfg(tmp_path), None) if p.tipo == "cot_semana_faltante"]
    assert [p.desde.date() for p in r] == [date(2024, 5, 21)]
    assert r[0].simbolo == "oro:legacy" and r[0].gravedad == "aviso"


def test_cot_dos_informes_misma_semana_son_dos_filas(tmp_path):
    martes = [date(2024, 5, 7), date(2024, 5, 14), date(2024, 5, 28)]
    for informe in ("legacy", "tff"):
        base = {"mercado": "oro", "informe": informe, "campo": "interes_abierto",
                "ts_version": _t(2024, 6, 1), "publicacion_tardia": False}
        filas = [{**base, "fecha_informe": d, "valor": 1.0,
                  "ts_conocido": _t(d.year, d.month, d.day) + timedelta(days=3)}
                 for d in martes]
        escribir(tmp_path, pa.Table.from_pylist(filas, schema=ESQUEMA_COT), "cot", "cftc",
                 f"oro_{informe}", date(2024, 1, 1))

    class Cat:
        def __init__(self):
            self.filas = set()

        def anotar_problema(self, tipo, simbolo, fuente, desde, hasta, gravedad, detalle):
            clave = (tipo, simbolo, fuente, desde, hasta)
            nueva = clave not in self.filas
            self.filas.add(clave)
            return nueva

    cat = Cat()
    r = [p for p in verificar_contexto(_cfg(tmp_path), cat) if p.tipo == "cot_semana_faltante"]
    assert sorted(p.simbolo for p in r) == ["oro:legacy", "oro:tff"]
    assert len(cat.filas) == 2


def test_hueco_serie_fred_en_dias_habiles(tmp_path):
    dias = [date(2024, 5, 6), date(2024, 5, 7), date(2024, 5, 10), date(2024, 5, 13)]
    _serie(tmp_path, [{"periodo": d, "valor": 4.0, "ts_conocido": _t(2024, 6, 1)} for d in dias],
           fuente="fred", sid="DGS10")
    r = [p for p in verificar_contexto(_cfg(tmp_path), None) if p.tipo == "serie_hueco"]
    assert len(r) == 1 and r[0].gravedad == "aviso"
    assert (r[0].desde.date(), r[0].hasta.date()) == (date(2024, 5, 8), date(2024, 5, 9))


def test_reprogramado_sin_mapear_y_prevision(tmp_path):
    ev = _t(2024, 3, 8, 13, 30)
    _guardar_calendario(tmp_path, [
        _evento(ev, ev - timedelta(days=7), id_fuente="x", prevision=3.8, categoria="otros"),
        _evento(ev + timedelta(hours=1), ev - timedelta(days=1), id_fuente="x", prevision=3.8,
                categoria="otros"),
    ])
    ff = _evento(ev + timedelta(hours=1), ev - timedelta(days=1), fuente="forexfactory",
                 id_fuente=None, prevision=3.5)
    ff["ts_foto"] = ff["ts_version"]
    _guardar_calendario(tmp_path, [ff], fuente="forexfactory")
    r = verificar_contexto(_cfg(tmp_path), None)
    assert {"evento_reprogramado", "evento_sin_mapear", "prevision_api_vs_ff"} <= _tipos(r)
    pf = next(p for p in r if p.tipo == "prevision_api_vs_ff")
    assert pf.gravedad == "info" and abs(pf.detalle["mediana"] - 0.3) < 1e-9


def test_catalogo_solo_devuelve_nuevos(tmp_path):
    class Cat:
        def anotar_problema(self, *a):
            return False
    for h in (0, 12):
        escribir_crudo(tmp_path, b"[]", "forexfactory", "ff_thisweek", _t(2024, 5, 1, h), "json")
    assert verificar_contexto(_cfg(tmp_path), Cat()) == []


def test_informe_tamano_por_tipo(tmp_path):
    pub = _t(2024, 3, 8, 13, 30)
    _serie(tmp_path, [{"periodo": date(2024, 2, 1), "valor": 3.9, "ts_conocido": pub}])
    escribir_crudo(tmp_path, b"{}" * 100, "forexfactory", "ff_thisweek", _t(2024, 5, 1), "json")
    ruta = escribir_informe(_cfg(tmp_path, DATOS_PG_DSN="postgresql://u:secreto@h/db"), [])
    texto = ruta.read_text(encoding="utf-8")
    assert "Tamaño por tipo" in texto
    assert "<td>serie</td>" in texto and "<td>crudo</td>" in texto
    assert "Crecimiento" in texto
    assert "secreto" not in texto


def test_serie_semanal_no_da_hueco(tmp_path):
    # DTWEXBGS es semanal (retraso lunes@): un dato por semana no es hueco.
    dias = [date(2024, 5, 6), date(2024, 5, 13), date(2024, 5, 20)]
    _serie(tmp_path, [{"periodo": d, "valor": 120.0, "ts_conocido": _t(2024, 6, 1)} for d in dias],
           fuente="fred", sid="DTWEXBGS")
    assert "serie_hueco" not in _tipos(verificar_contexto(_cfg(tmp_path), None))


def test_viernes_santo_no_es_hueco_en_vixcls(tmp_path):
    # 2024-03-29 fue Viernes Santo: la bolsa cerró y VIXCLS no tiene dato.
    dias = [date(2024, 3, 27), date(2024, 3, 28), date(2024, 4, 1), date(2024, 4, 2)]
    _serie(tmp_path, [{"periodo": d, "valor": 13.0, "ts_conocido": _t(2024, 6, 1)} for d in dias],
           fuente="fred", sid="VIXCLS")
    assert "serie_hueco" not in _tipos(verificar_contexto(_cfg(tmp_path), None))


def test_informe_prevision_aparece_aunque_el_catalogo_ya_lo_tenga(tmp_path):
    from datos.calidad_contexto import estadisticas_prevision_contexto
    ev = _t(2024, 3, 8, 13, 30)
    _guardar_calendario(tmp_path, [_evento(ev, ev - timedelta(days=7), id_fuente="x",
                                           prevision=3.8)])
    ff = _evento(ev, ev - timedelta(days=1), fuente="forexfactory", id_fuente=None,
                 prevision=3.5)
    ff["ts_foto"] = ff["ts_version"]
    _guardar_calendario(tmp_path, [ff], fuente="forexfactory")

    class CatViejo:   # todo lo anota como ya conocido: no hay problemas nuevos
        def anotar_problema(self, *a):
            return False
    cfg = _cfg(tmp_path)
    nuevos = verificar_contexto(cfg, CatViejo())
    assert nuevos == []
    texto = escribir_informe(cfg, nuevos, {
        "previsiones": estadisticas_prevision_contexto(cfg)}).read_text(encoding="utf-8")
    assert "Previsión de la API frente a Forex Factory" in texto
    assert "<td>US_DESEMPLEO</td>" in texto and "<td>0.3</td>" in texto
