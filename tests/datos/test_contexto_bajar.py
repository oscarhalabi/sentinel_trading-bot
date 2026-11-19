"""Pruebas de `python -m datos contexto bajar|reprocesar` con clientes HTTP falsos (sin red).

Los cuerpos usados son los fixtures PROVISIONALES de fixtures/contexto y CSV del COT hechos a mano
(ver test_cftc.py); se sustituyen por respuestas reales recortadas en la Task 12.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from datos import simbolos as sim
from datos.__main__ import main
from datos.almacen import escribir, listar_crudos
from datos.config import cargar_config
from datos.fuentes.calendario_api import bajar_calendario, bajar_corporativos
from datos.fuentes.cftc import bajar_cot
from datos.fuentes.forexfactory import URLS_FF, guardar_foto
from datos.fuentes.fred import bajar_series
from datos.fuentes.spdr import bajar_gld
from datos.simbolos import Simbolo
from tests.datos.ayudas import m1_sintetica

FIX = Path(__file__).parent / "fixtures" / "contexto"
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
CLAVES = {"FRED_API_KEY": "CLAVE_FRED_X", "FMP_API_KEY": "CLAVE_FMP_X",
          "EODHD_API_KEY": "CLAVE_EODHD_X"}

LEGACY = ("Market and Exchange Names,As of Date in Form YYYY-MM-DD,CFTC Contract Market Code,"
          "Open Interest (All),Noncommercial Positions-Long (All)\n"
          '"GOLD - COMEX",2026-11-10,088691,500000,200000\n')
OTROS = "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,CFTC_Contract_Market_Code\n"


def leer(nombre):
    return (FIX / nombre).read_bytes()


class HttpFalso:
    """Responde según el primer trozo de la URL que coincida; un entero es un estado de error."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.urls = []

    def get(self, url, timeout, headers=None):
        self.urls.append(url)
        for trozo, cuerpo in self.respuestas:
            if trozo in url:
                return (cuerpo, b"") if isinstance(cuerpo, int) else (200, cuerpo)
        return 404, b""


def respuestas(ff=None):
    return [
        (URLS_FF[0], ff if ff is not None else leer("ff_thisweek_provisional.json")),
        (URLS_FF[1], b"[]"),
        ("realtime_start", leer("alfred_payems.json")),
        ("stlouisfed", leer("fred_dgs10.json")),
        ("deacot", LEGACY.encode()),
        ("fut_", OTROS.encode()),
        ("GLD_US_archive", leer("gld.csv")),
        ("economic-calendar", leer("fmp_calendario.json")),
        ("splits", leer("fmp_splits.json")),
        ("dividends", leer("fmp_dividendos.json")),
    ]


@pytest.fixture
def cfg(tmp_path):
    datos = tmp_path / "datos"
    datos.mkdir()
    return cargar_config({"DATA_DIR": str(datos), **CLAVES})


@pytest.fixture
def registro(tmp_path):
    ruta = tmp_path / "simbolos.json"
    sim.guardar({"NVDA.US": Simbolo("NVDA.US", "accion", 1, {"dukascopy": "NVDA.US"}, 2,
                                    "accion_us", "USD")}, ruta)
    return ruta


def test_bajar_una_fuente_falla_las_demas_siguen(cfg, registro, capsys):
    http = HttpFalso([(URLS_FF[0], 503)] + respuestas())
    assert main(["contexto", "bajar"], ruta=registro, config=cfg, http=http) == 1
    salida = capsys.readouterr().out
    assert "ff: FALLÓ" in salida and "1 de 7" in salida
    raiz = Path(cfg.data_dir)
    for rel in ("serie/fuente=alfred/simbolo=PAYEMS/completo.parquet",
                "serie/fuente=fred/simbolo=DGS10/completo.parquet",
                "cot/fuente=cftc/simbolo=ORO/anio=2026.parquet",
                "etf/fuente=spdr/simbolo=GLD/completo.parquet",
                "corporativo/fuente=fmp/simbolo=NVDA.US/completo.parquet"):
        assert (raiz / rel).is_file(), rel
    for clave in CLAVES.values():
        assert clave not in salida
    # Calendario normal: últimos 30 días y próximos 14, con la fuente de cfg (fmp).
    cal = [u for u in http.urls if "economic-calendar" in u]
    assert cal and all("financialmodelingprep" in u for u in cal)


def test_bajar_solo_las_fuentes_pedidas(cfg, registro, capsys):
    http = HttpFalso(respuestas())
    assert main(["contexto", "bajar", "gld", "ff"], ruta=registro, config=cfg, http=http) == 0
    assert len(http.urls) == 3
    assert all(u in URLS_FF or "GLD_US_archive" in u for u in http.urls)
    salida = capsys.readouterr().out
    assert "gld: " in salida and "ff: " in salida and "alfred" not in salida


def test_historico_exige_desde(cfg, registro, capsys):
    assert main(["contexto", "bajar", "calendario", "--historico"], ruta=registro,
                config=cfg, http=HttpFalso([])) == 2
    assert "--desde" in capsys.readouterr().err


def test_historico_baja_calendario_con_sufijo_hist(cfg, registro):
    http = HttpFalso(respuestas())
    assert main(["contexto", "bajar", "--historico", "--desde", "2024-01-01"], ruta=registro,
                config=cfg, http=http) == 0
    assert all("economic-calendar" in u for u in http.urls)   # sin fuentes: solo calendario
    nombres = [m.ruta for m in listar_crudos(Path(cfg.data_dir), "fmp")]
    assert any("fmp_cal_2024-01_hist." in r for r in nombres)


def _foto(raiz: Path) -> dict:
    """Todas las filas de los Parquet de contexto, por ruta (sin crudos ni manifiestos)."""
    filas = {}
    for p in sorted(raiz.rglob("*.parquet")):
        rel = p.relative_to(raiz).as_posix()
        if rel.split("/")[0] in ("ticks", "m1"):
            continue
        t = pq.read_table(p).to_pylist()
        filas[rel] = sorted(t, key=lambda f: json.dumps(f, sort_keys=True, default=str))
    return filas


def _bajar_todo(cfg):
    """Descargas en vivo con clientes falsos: dos fotos de FF (cambia una previsión), ALFRED,
    FRED, COT, GLD, calendario (normal e histórico, FMP y EODHD) y corporativos."""
    guardar_foto(cfg, HttpFalso(respuestas()), T0)
    datos = json.loads(leer("ff_thisweek_provisional.json"))
    datos[0]["forecast"] = "260K"
    guardar_foto(cfg, HttpFalso(respuestas(json.dumps(datos).encode())), T0 + timedelta(hours=4))
    bajar_series(cfg, HttpFalso(respuestas()), ids=["PAYEMS", "DGS10"])
    bajar_cot(cfg, anios=[2026], http=HttpFalso(respuestas()), ahora=T0)
    bajar_gld(cfg, HttpFalso(respuestas()))
    bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), True,
                     http=HttpFalso(respuestas()))
    bajar_calendario(cfg, "fmp", date(2024, 1, 1), date(2024, 1, 31), False,
                     http=HttpFalso(respuestas()))
    bajar_calendario(cfg, "eodhd", date(2024, 1, 1), date(2024, 1, 31), False,
                     http=HttpFalso([("economic-events", leer("eodhd_eventos.json"))]))
    bajar_corporativos(cfg, "fmp", [Simbolo("NVDA.US", "accion", 1, {}, 2, "us", "USD")],
                       http=HttpFalso(respuestas()))


def test_reprocesar_da_las_mismas_filas(cfg, capsys):
    raiz = Path(cfg.data_dir)
    m1 = escribir(raiz, m1_sintetica(datetime(2024, 1, 2), 5), "m1", "dukascopy", "XAUUSD",
                  date(2024, 1, 1))
    _bajar_todo(cfg)
    antes = _foto(raiz)
    assert "calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet" in antes
    nfp = [f for f in antes["calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet"]
           if f["evento"] == "US_NFP"]
    assert len(nfp) == 2   # la previsión cambió entre las dos fotos
    assert "calendario/fuente=fmp/simbolo=USD/anio=2024.parquet" in antes
    assert "serie/fuente=alfred/simbolo=PAYEMS/completo.parquet" in antes
    n_crudos = len(list(listar_crudos(raiz)))

    assert main(["contexto", "reprocesar"], config=cfg) == 0
    assert _foto(raiz) == antes
    assert len(list(listar_crudos(raiz))) == n_crudos   # no crea crudos nuevos
    assert (raiz / m1.ruta).is_file()                    # M1 no se toca
    assert not (raiz / "_respaldo").exists()
    assert "crudos" in capsys.readouterr().out


def test_reprocesar_una_fuente_no_toca_las_demas(cfg):
    raiz = Path(cfg.data_dir)
    _bajar_todo(cfg)
    antes = _foto(raiz)
    alfred = raiz / "serie/fuente=alfred/simbolo=PAYEMS/completo.parquet"
    mtime = alfred.stat().st_mtime_ns
    ff = raiz / "calendario/fuente=forexfactory/simbolo=USD/anio=2026.parquet"
    ff.unlink()   # se rehace desde los crudos
    assert main(["contexto", "reprocesar", "--fuente", "ff"], config=cfg) == 0
    assert alfred.stat().st_mtime_ns == mtime
    assert _foto(raiz) == antes


def test_reprocesar_salta_los_crudos_de_prueba(cfg):
    raiz = Path(cfg.data_dir)
    from datos.almacen import escribir_crudo
    escribir_crudo(raiz, leer("fmp_calendario.json"), "fmp", "prueba_fmp_cal_2024-01_hist",
                   T0, "json")
    assert main(["contexto", "reprocesar", "--fuente", "fmp"], config=cfg) == 0
    assert not (raiz / "calendario").exists()


def test_catalogo_registra_lo_escrito(cfg, registro, monkeypatch):
    import datos.catalogo as catalogo

    class CatalogoFalso:
        instancias = []

        def __init__(self, dsn):
            self.registrados, self.corridas = [], []
            CatalogoFalso.instancias.append(self)

        def abrir_corrida(self, orden):
            self.corridas.append(orden)
            return 7

        def cerrar_corrida(self, id, resultado, resumen):
            self.cierre = (id, resultado, resumen)

        def registrar(self, m):
            self.registrados.append(m)

        def cerrar(self):
            self.cerrado = True

    monkeypatch.setattr(catalogo, "Catalogo", CatalogoFalso)
    cfg = cargar_config({"DATA_DIR": str(cfg.data_dir), "DATOS_PG_DSN": "dsn", **CLAVES})
    assert main(["contexto", "bajar", "gld"], ruta=registro, config=cfg,
                http=HttpFalso(respuestas())) == 0
    c = CatalogoFalso.instancias[0]
    assert c.corridas == ["contexto bajar"] and c.cierre[:2] == (7, "ok") and c.cerrado
    assert [m.ruta for m in c.registrados] == ["etf/fuente=spdr/simbolo=GLD/completo.parquet"]


def test_catalogo_sin_conexion_sale_1(cfg, registro, monkeypatch, capsys):
    import datos.catalogo as catalogo

    def falla(dsn):
        raise RuntimeError("postgresql://usuario:secreto@host")

    monkeypatch.setattr(catalogo, "Catalogo", falla)
    cfg = cargar_config({"DATA_DIR": str(cfg.data_dir), "DATOS_PG_DSN": "dsn", **CLAVES})
    http = HttpFalso(respuestas())
    assert main(["contexto", "bajar", "gld"], ruta=registro, config=cfg, http=http) == 1
    err = capsys.readouterr().err
    assert "catálogo" in err and "secreto" not in err and http.urls == []
