"""Pruebas del orquestador de descarga (sin red: fuente falsa, sin catálogo)."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from datos import descarga
from datos.__main__ import main
from datos.almacen import estado
from datos.config import Config
from datos.descarga import bajar
from datos.fuentes.base import ResultadoDia
from datos.simbolos import Simbolo
from tests.datos.ayudas import ticks_sinteticos

HOY = date(2026, 11, 12)


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


def _dias(n, hasta=HOY - timedelta(days=1)):
    return [hasta - timedelta(days=i) for i in range(n)]


def test_baja_de_hoy_hacia_atras(cfg):
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in _dias(5)})
    r = bajar(cfg, [_sim()], f, None, desde=HOY - timedelta(days=5), hoy=HOY)
    dias = [d for _, d in f.llamadas]
    assert dias == sorted(dias, reverse=True)
    assert dias[0] == HOY - timedelta(days=1)
    assert r.dias_ok == 5 and r.dias_error == 0
    assert estado(cfg.data_dir, "ticks", "dukascopy", "XAUUSD", HOY - timedelta(days=1)) == "ok"


def test_reanuda_sin_duplicar(cfg):
    f = FuenteFalsa({("XAUUSD", d): _ok(d) for d in _dias(3)})
    desde = HOY - timedelta(days=5)
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
    d = HOY - timedelta(days=1)
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
    bajar(cfg, [_sim()], f, None, desde=HOY - timedelta(days=1), hoy=HOY)
    assert f.llamadas
    assert not (carpeta / "descarga.lock").exists()  # se libera al terminar


def test_candado_se_libera_si_hay_excepcion(cfg):
    class Rompe(FuenteFalsa):
        def bajar_dia(self, sim, dia):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        bajar(cfg, [_sim()], Rompe(), None, desde=HOY - timedelta(days=1), hoy=HOY)
    assert not (cfg.data_dir / "_registro" / "descarga.lock").exists()


def test_un_simbolo_que_falla_no_para_los_demas(cfg):
    d = HOY - timedelta(days=1)
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
