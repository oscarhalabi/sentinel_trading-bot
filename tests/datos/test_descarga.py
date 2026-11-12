"""Pruebas del orquestador de descarga (sin red: fuente falsa, sin catálogo)."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from datos import descarga
from datos.__main__ import main
from datos.almacen import escribir, estado
from datos.config import Config
from datos.descarga import bajar
from datos.fuentes.base import ResultadoDia
from datos.simbolos import Simbolo
from tests.datos.ayudas import ticks_sinteticos

HOY = date(2026, 11, 12)
HASTA = HOY - timedelta(days=2)  # último día que se pide por defecto


def _sim(nombre="XAUUSD", clase="metal", prioridad=1):
    return Simbolo(nombre, clase, prioridad, {"dukascopy": nombre}, 3, "metal", "USD")


def _tabla(dia: date, precio=2000.0):
    return ticks_sinteticos(datetime(dia.year, dia.month, dia.day, 1, tzinfo=timezone.utc),
                            5, precio=precio)


class FuenteFalsa:
    """(símbolo, día) -> ResultadoDia. Lo que no esté en el dict es `vacio`."""
    nombre = "dukascopy"

    def __init__(self, respuestas=None, precio=2000.0, falla_simbolos=()):
        self.respuestas = dict(respuestas or {})
        self.llamadas: list[tuple[str, date]] = []
        self.precio = precio
        self.falla_simbolos = set(falla_simbolos)

    def bajar_dia(self, sim, dia):
        self.llamadas.append((sim.simbolo, dia))
        if sim.simbolo in self.falla_simbolos:
            raise RuntimeError("fallo inventado")
        r = self.respuestas.get((sim.simbolo, dia))
        if r is not None:
            return r
        return ResultadoDia("vacio", None)

    def listar_instrumentos(self):
        return []


def _ok(dia, precio=2000.0):
    return ResultadoDia("ok", _tabla(dia, precio))


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path, pg_dsn="", disco_min_gb=1.0, corte_reserva=None)


def _dias(n, hasta=HASTA):
    return [hasta - timedelta(days=i) for i in range(n)]


def test_baja_de_hoy_hacia_atras(cfg):
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in _dias(5)})
    r = bajar(cfg, [_sim()], f, None, desde=HASTA - timedelta(days=4), hoy=HOY)
    dias = [d for _, d in f.llamadas]
    assert dias == sorted(dias, reverse=True)
    assert dias[0] == HASTA
    assert r.dias_ok == 5 and r.dias_error == 0
    assert estado(cfg.data_dir, "ticks", "dukascopy", "XAUUSD", HASTA) == "ok"


def test_reanuda_sin_duplicar(cfg):
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in _dias(3)})
    desde = HASTA - timedelta(days=4)
    bajar(cfg, [_sim()], f, None, desde=desde, hoy=HOY)
    n = len(f.llamadas)
    assert n == 5
    r = bajar(cfg, [_sim()], f, None, desde=desde, hoy=HOY)
    assert len(f.llamadas) == n  # ni ok ni vacio se piden otra vez
    assert r.dias_ok == 0


def test_error_se_reintenta_en_la_siguiente(cfg):
    malo = HOY - timedelta(days=2)
    f = FuenteFalsa({("XAUUSD", malo): ResultadoDia("error", None, "HTTP 503")})
    desde = HOY - timedelta(days=3)
    r1 = bajar(cfg, [_sim()], f, None, desde=desde, hoy=HOY)
    assert r1.dias_error == 1
    assert estado(cfg.data_dir, "ticks", "dukascopy", "XAUUSD", malo) == "falta"
    f.llamadas.clear()
    f.respuestas[("XAUUSD", malo)] = _ok(malo)
    r2 = bajar(cfg, [_sim()], f, None, desde=desde, hoy=HOY)
    assert f.llamadas == [("XAUUSD", malo)]
    assert r2.dias_ok == 1 and r2.dias_error == 0


def test_para_tras_8_semanas_vacias(cfg):
    ultimo_ok = HOY - timedelta(days=10)
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in
                     [ultimo_ok, ultimo_ok - timedelta(days=1)]})
    r = bajar(cfg, [_sim()], f, None, hoy=HOY)
    mas_antiguo = ultimo_ok - timedelta(days=1)
    pedidos = [d for _, d in f.llamadas]
    assert min(pedidos) == mas_antiguo - timedelta(days=56)
    assert r.dias_ok == 2 and r.simbolos_parados == {}
    # segunda corrida: los vacíos ya guardados cuentan para la parada y no se piden de nuevo
    f.llamadas.clear()
    bajar(cfg, [_sim()], f, None, hoy=HOY)
    assert f.llamadas == []


def test_escala_mala_para_el_simbolo(cfg):
    d = HASTA
    f = FuenteFalsa({("XAUUSD", d): _ok(d, precio=200000.0),  # oro a 200 000: mala escala
                     ("EURUSD", d): _ok(d, precio=1.1)})
    syms = [_sim("XAUUSD"), _sim("EURUSD", clase="fx_mayor")]
    r = bajar(cfg, syms, f, None, desde=d - timedelta(days=1), hoy=HOY)
    assert "XAUUSD" in r.simbolos_parados
    assert "EURUSD" not in r.simbolos_parados
    assert estado(cfg.data_dir, "ticks", "dukascopy", "XAUUSD", d) == "falta"
    assert estado(cfg.data_dir, "ticks", "dukascopy", "EURUSD", d) == "ok"
    assert r.dias_ok == 1


def test_disco_bajo_para_todo(cfg, monkeypatch):
    monkeypatch.setattr(descarga, "espacio_libre_gb", lambda raiz: 0.5)
    f = FuenteFalsa()
    r = bajar(cfg, [_sim(), _sim("EURUSD")], f, None,
              desde=HOY - timedelta(days=3), hoy=HOY)
    assert r.parado_por == "disco"
    assert f.llamadas == []


def test_max_horas_para(cfg):
    reloj_falso = iter([0.0, 10.0, 10.0, 10.0, 7200.0] + [7200.0] * 20)
    f = FuenteFalsa()
    r = bajar(cfg, [_sim()], f, None, desde=HOY - timedelta(days=30), hoy=HOY,
              max_horas=1.0, reloj=lambda: next(reloj_falso))
    assert r.parado_por == "tiempo"
    assert 0 < len(f.llamadas) < 30


def test_candado_evita_doble_descarga(cfg):
    carpeta = cfg.data_dir / "_registro"
    carpeta.mkdir()
    (carpeta / "descarga.lock").write_text(str(os.getpid()))
    with pytest.raises(SystemExit, match="Ya hay una descarga"):
        bajar(cfg, [_sim()], FuenteFalsa(), None, desde=HOY, hasta=HOY, hoy=HOY)
    assert (carpeta / "descarga.lock").exists()  # el candado ajeno no se toca


def test_candado_huerfano_se_reemplaza(cfg):
    carpeta = cfg.data_dir / "_registro"
    carpeta.mkdir()
    (carpeta / "descarga.lock").write_text("999999999")
    f = FuenteFalsa()
    bajar(cfg, [_sim()], f, None, desde=HASTA, hoy=HOY)
    assert f.llamadas
    assert not (carpeta / "descarga.lock").exists()  # se libera al terminar


def test_candado_se_libera_si_hay_excepcion(cfg):
    class Rompe(FuenteFalsa):
        def bajar_dia(self, sim, dia):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        bajar(cfg, [_sim()], Rompe(), None, desde=HASTA, hoy=HOY)
    assert not (cfg.data_dir / "_registro" / "descarga.lock").exists()


def test_un_simbolo_que_falla_no_para_los_demas(cfg):
    d = HASTA
    f = FuenteFalsa({("EURUSD", d): _ok(d, precio=1.1)}, falla_simbolos={"XAUUSD"})
    syms = [_sim("XAUUSD"), _sim("EURUSD", clase="fx_mayor")]
    r = bajar(cfg, syms, f, None, desde=d - timedelta(days=1), hoy=HOY)
    assert r.dias_error >= 1
    assert estado(cfg.data_dir, "ticks", "dukascopy", "EURUSD", d) == "ok"
    assert r.dias_ok == 1


def test_simbolo_con_errores_seguidos_se_para(cfg):
    f = FuenteFalsa(falla_simbolos={"XAUUSD"})
    r = bajar(cfg, [_sim()], f, None, hoy=HOY)  # sin desde: no debe irse hasta el año 2000
    assert "XAUUSD" in r.simbolos_parados
    assert len(f.llamadas) < 50


# --- CLI ---

def _registro(tmp_path):
    from datos.simbolos import guardar
    ruta = tmp_path / "simbolos.json"
    guardar({"XAUUSD": _sim("XAUUSD"), "EURUSD": _sim("EURUSD", "fx_mayor", 2)}, ruta)
    return ruta


def test_cli_simbolo_desconocido_sale_con_2(cfg, tmp_path, capsys):
    ruta = _registro(tmp_path)
    assert main(["bajar", "--simbolo", "NOEXISTE"], fuente=FuenteFalsa(), ruta=ruta,
                config=cfg) == 2
    assert "NOEXISTE" in capsys.readouterr().err


def test_cli_prioridad_y_salida(cfg, tmp_path, capsys):
    ruta = _registro(tmp_path)
    f = FuenteFalsa()
    hasta = (date.today() - timedelta(days=1)).isoformat()
    code = main(["bajar", "--prioridad", "2", "--desde", hasta, "--hasta", hasta],
                fuente=f, ruta=ruta, config=cfg)
    assert code == 0
    assert {s for s, _ in f.llamadas} == {"EURUSD"}
    assert "Días descargados" in capsys.readouterr().out


def test_cli_sale_1_si_hay_errores(cfg, tmp_path):
    ruta = _registro(tmp_path)
    f = FuenteFalsa(falla_simbolos={"XAUUSD"})
    hasta = (date.today() - timedelta(days=1)).isoformat()
    assert main(["bajar", "--simbolo", "XAUUSD", "--desde", hasta, "--hasta", hasta],
                fuente=f, ruta=ruta, config=cfg) == 1


def test_pid_vivo_posix():
    assert descarga._pid_vivo(os.getpid()) is True
    assert descarga._pid_vivo(999999999) is False


# --- correcciones de la revisión ---

def test_leer_un_simbolo_no_lee_manifiestos_de_otro(cfg, monkeypatch):
    from datos import almacen
    d = HOY - timedelta(days=1)
    f = FuenteFalsa({("XAUUSD", d): _ok(d), ("EURUSD", d): _ok(d, 1.1)})
    syms = [_sim("XAUUSD"), _sim("EURUSD", "fx_mayor")]
    bajar(cfg, syms, f, None, desde=d, hasta=d, hoy=HOY)
    leidos = []
    original = almacen.Manifiesto.from_json.__func__
    monkeypatch.setattr(almacen.Manifiesto, "from_json",
                        classmethod(lambda cls, t: (leidos.append(t), original(cls, t))[1]))
    hechos = descarga._hechos_del_almacen(cfg, "dukascopy", "XAUUSD")
    assert hechos == {d: "ok"}
    assert leidos and all('"simbolo": "XAUUSD"' in t for t in leidos)


def test_cli_disco_bajo_sale_1_y_max_horas_sale_0(cfg, tmp_path, monkeypatch):
    ruta = _registro(tmp_path)
    hasta = (date.today() - timedelta(days=1)).isoformat()
    args = ["bajar", "--simbolo", "XAUUSD", "--desde", hasta, "--hasta", hasta]
    monkeypatch.setattr(descarga, "espacio_libre_gb", lambda raiz: 0.1)
    assert main(args, fuente=FuenteFalsa(), ruta=ruta, config=cfg) == 1
    monkeypatch.setattr(descarga, "espacio_libre_gb", lambda raiz: 100.0)
    assert main(args + ["--max-horas", "0"], fuente=FuenteFalsa(), ruta=ruta, config=cfg) == 0


class CatalogoFalso:
    """Catálogo en memoria con los mismos métodos que Catalogo."""

    def __init__(self, estados=None):
        self._estados = estados or {}
        self.registrados, self.errores, self.inicios = [], [], []
        self.corridas = {}
        self.consultas = []

    def estados(self, tipo, fuente, simbolo):
        self.consultas.append((tipo, fuente, simbolo))
        return dict(self._estados)

    def registrar(self, m):
        self.registrados.append((m.periodo, m.estado))

    def registrar_error(self, tipo, fuente, simbolo, periodo, error):
        self.errores.append((periodo, error))

    def fijar_inicio(self, simbolo, fuente, inicio):
        self.inicios.append((simbolo, fuente, inicio))

    def abrir_corrida(self, orden):
        self.corridas[1] = None
        self.orden = orden
        return 1

    def cerrar_corrida(self, id, resultado, resumen):
        self.corridas[id] = (resultado, resumen)


def test_con_catalogo_registra_y_decide_por_el_almacen(cfg):
    ok1, ok2, malo = HOY - timedelta(days=10), HOY - timedelta(days=11), HOY - timedelta(days=3)
    ya_hecho = HASTA
    escribir(cfg.data_dir, _tabla(ya_hecho), "ticks", "dukascopy", "XAUUSD", ya_hecho)
    cat = CatalogoFalso()  # vacío: no sabe nada de `ya_hecho`
    f = FuenteFalsa({("XAUUSD", ok1): _ok(ok1), ("XAUUSD", ok2): _ok(ok2),
                     ("XAUUSD", malo): ResultadoDia("error", None, "HTTP 503")})
    r = bajar(cfg, [_sim()], f, cat, hoy=HOY)
    assert cat.consultas == []  # el estado de los días no sale del catálogo
    assert ya_hecho not in [d for _, d in f.llamadas]  # salió del almacén
    assert ("2026-11-02", "ok") in cat.registrados and ("2026-11-01", "ok") in cat.registrados
    assert any(e == "vacio" for _, e in cat.registrados)
    assert [p for p, _ in cat.errores] == [malo]
    assert cat.inicios == [("XAUUSD", "dukascopy", ok2)]  # el día ok más antiguo
    assert cat.orden == "bajar"
    resultado, resumen = cat.corridas[1]
    assert resultado == "parcial"  # hubo un error
    assert resumen["dias_ok"] == 2 and resumen["dias_error"] == 1 and "parado_por" in resumen
    assert r.dias_ok == 2


def test_catalogo_dice_ok_pero_el_parquet_falta_o_esta_roto_se_rebaja(cfg):
    sin_nada, truncado = HASTA, HASTA - timedelta(days=1)
    m = escribir(cfg.data_dir, _tabla(truncado), "ticks", "dukascopy", "XAUUSD", truncado)
    ruta = cfg.data_dir / m.ruta
    ruta.write_bytes(ruta.read_bytes()[:20])  # Parquet cortado
    cat = CatalogoFalso({sin_nada: "ok", truncado: "ok"})
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in (sin_nada, truncado)})
    r = bajar(cfg, [_sim()], f, cat, desde=truncado, hoy=HOY)
    assert sorted(d for _, d in f.llamadas) == [truncado, sin_nada]
    assert r.dias_ok == 2
    for d in (sin_nada, truncado):
        assert estado(cfg.data_dir, "ticks", "dukascopy", "XAUUSD", d) == "ok"


def test_catalogo_vacio_con_almacen_lleno_no_rebaja_nada(cfg):
    desde = HASTA - timedelta(days=4)
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in _dias(3)})
    bajar(cfg, [_sim()], f, None, desde=desde, hoy=HOY)
    f.llamadas.clear()
    cat = CatalogoFalso()
    r = bajar(cfg, [_sim()], f, cat, desde=desde, hoy=HOY)
    assert f.llamadas == []
    assert r.dias_ok == 0 and cat.registrados == []


def test_excepcion_inesperada_cierra_la_corrida_con_error(cfg, monkeypatch):
    def _rompe(*a, **k):
        raise RuntimeError("fallo inesperado")
    monkeypatch.setattr(descarga, "_hechos_del_almacen", _rompe)
    cat = CatalogoFalso()
    with pytest.raises(RuntimeError, match="fallo inesperado"):
        bajar(cfg, [_sim()], FuenteFalsa(), cat, desde=HASTA, hoy=HOY)
    assert cat.corridas[1][0] == "error"
    assert not (cfg.data_dir / "_registro" / "descarga.lock").exists()


class FuenteSiempreOk(FuenteFalsa):
    def bajar_dia(self, sim, dia):
        self.llamadas.append((sim.simbolo, dia))
        return _ok(dia)


@pytest.mark.parametrize("funcion, fuente", [("escribir", FuenteSiempreOk),
                                             ("marcar_vacio", FuenteFalsa)])
def test_fallo_persistente_al_guardar_para_el_simbolo(cfg, monkeypatch, funcion, fuente):
    def _disco_roto(*a, **k):
        raise OSError("disco roto")
    monkeypatch.setattr(descarga, funcion, _disco_roto)
    f = fuente()
    cat = CatalogoFalso()
    r = bajar(cfg, [_sim()], f, cat, hoy=HOY)  # sin desde: no debe irse hasta el año 2000
    assert "XAUUSD" in r.simbolos_parados and "disco roto" in r.simbolos_parados["XAUUSD"]
    assert len(f.llamadas) == descarga.ERRORES_SEGUIDOS_PARA_PARAR
    assert r.dias_error == descarga.ERRORES_SEGUIDOS_PARA_PARAR
    assert len(cat.errores) == descarga.ERRORES_SEGUIDOS_PARA_PARAR


def test_hasta_por_defecto_es_hoy_menos_2_dias(cfg):
    f = FuenteFalsa()
    bajar(cfg, [_sim()], f, None, desde=HOY - timedelta(days=5), hoy=HOY)
    pedidos = [d for _, d in f.llamadas]
    assert max(pedidos) == HOY - timedelta(days=2)
    assert HOY - timedelta(days=1) not in pedidos


def test_simbolo_sin_ningun_dato_se_para_con_aviso(cfg):
    f = FuenteFalsa()  # todo vacío: como un código de fuente mal puesto
    cat = CatalogoFalso()
    r = bajar(cfg, [_sim()], f, cat, hoy=HOY)
    assert r.simbolos_parados == {"XAUUSD": "ningún dato: ¿código de fuente mal puesto?"}
    assert len(f.llamadas) == descarga.DIAS_VACIOS_PARA_PARAR
    assert cat.inicios == []


def test_simbolo_con_dato_antiguo_en_almacen_no_se_marca_sin_datos(cfg):
    viejo = HASTA - timedelta(days=200)
    escribir(cfg.data_dir, _tabla(viejo), "ticks", "dukascopy", "XAUUSD", viejo)
    r = bajar(cfg, [_sim()], FuenteFalsa(), None, hoy=HOY)
    assert r.simbolos_parados == {}


def test_con_catalogo_ctrl_c_cierra_interrumpida(cfg):
    class Rompe(FuenteFalsa):
        def bajar_dia(self, sim, dia):
            raise KeyboardInterrupt
    cat = CatalogoFalso()
    with pytest.raises(KeyboardInterrupt):
        bajar(cfg, [_sim()], Rompe(), cat, desde=HASTA, hoy=HOY)
    assert cat.corridas[1][0] == "interrumpida"
    assert not (cfg.data_dir / "_registro" / "descarga.lock").exists()
