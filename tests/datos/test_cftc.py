"""Pruebas del COT de la CFTC. Los zips se arman en memoria con CSV hechos a mano:
son PROVISIONALES (nombres de columnas supuestos) hasta sustituirlos por un archivo real
recortado en la laptop (Task 12)."""
import io
import zipfile
from datetime import date, datetime, timezone

import pyarrow.parquet as pq
import pytest

from datos.config import cargar_config
from datos.contexto_conf import cargar_contexto
from datos.fuentes.cftc import URLS_COT, bajar_cot, normalizar_cot

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
COT = cargar_contexto()["cot"]

LEGACY = (
    "Market and Exchange Names,As of Date in Form YYYY-MM-DD,CFTC Contract Market Code,"
    "Open Interest (All),Noncommercial Positions-Long (All),Noncommercial Positions-Short (All),"
    "Commercial Positions-Long (All),Commercial Positions-Short (All)\n"
    '"GOLD - COMEX",2026-11-03,088691,500000,200000,50000,100000,300000\n'
    '"OTRO - X",2026-11-03,999999,10,1,1,1,1\n'
)
DISAGG = (
    "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,CFTC_Contract_Market_Code,"
    "Open_Interest_All,M_Money_Positions_Long_All,M_Money_Positions_Short_All\n"
    '"GOLD - COMEX",2008-12-30,088691,400000,150000,30000\n'
)
TFF = (
    "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,CFTC_Contract_Market_Code,"
    "Open_Interest_All,Lev_Money_Positions_Long_All,Lev_Money_Positions_Short_All\n"
    '"EURO FX",2026-11-03,099741,700000,60000,90000\n'
)


def zip_de(texto, nombre="datos.txt"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(nombre, texto)
    return b.getvalue()


def valores(tabla):
    return {(f["mercado"], f["campo"]): f for f in tabla.to_pylist()}


def test_cot_viernes():
    v = valores(normalizar_cot(LEGACY.encode(), "legacy", COT, T0))
    f = v[("ORO", "no_comerciales_largos")]
    assert f["valor"] == 200000.0 and f["fecha_informe"] == date(2026, 11, 3)
    assert f["ts_conocido"] == datetime(2026, 11, 6, 20, 30, tzinfo=timezone.utc)  # vie 15:30 NY
    assert f["ts_version"] == T0 and f["publicacion_tardia"] is False
    assert ("ORO", "interes_abierto") in v and ("ORO", "comerciales_cortos") in v
    assert ("ORO", "gestores_largos") not in v   # legacy no tiene ese campo


def test_cot_disaggregated_viejo_publicacion_tardia():
    f = valores(normalizar_cot(DISAGG.encode(), "disaggregated", COT, T0))[("ORO", "gestores_largos")]
    assert f["publicacion_tardia"] is True
    assert f["ts_conocido"] == datetime(2009, 9, 4, 19, 30, tzinfo=timezone.utc)  # 15:30 NY (EDT)


def test_cot_tff_fondos():
    f = valores(normalizar_cot(TFF.encode(), "tff", COT, T0))[("EUR", "fondos_cortos")]
    assert f["valor"] == 90000.0


def test_cot_codigo_desconocido_se_ignora(caplog):
    with caplog.at_level("INFO"):
        t = normalizar_cot(LEGACY.encode(), "legacy", COT, T0)
    assert {f["mercado"] for f in t.to_pylist()} == {"ORO"}
    assert "1" in caplog.text and "desconocid" in caplog.text


@pytest.fixture
def cfg(tmp_path):
    return cargar_config({"DATA_DIR": str(tmp_path)})


class HttpFalso:
    def __init__(self, cuerpos):
        self.cuerpos, self.pedidas = cuerpos, []

    def get(self, url, timeout, headers=None):
        self.pedidas.append(url)
        c = self.cuerpos.get(url, 404)
        return (c, b"") if isinstance(c, int) else (200, c)


def http_con(legacy=LEGACY):
    return HttpFalso({
        URLS_COT["legacy"].format(anio=2026): zip_de(legacy),
        URLS_COT["disaggregated"].format(anio=2026): zip_de(DISAGG.replace("2008-12-30", "2026-11-03")),
        URLS_COT["tff"].format(anio=2026): zip_de(TFF),
        URLS_COT["legacy"].format(anio=2025): zip_de(LEGACY.replace("2026-11-03", "2025-11-11")),
        URLS_COT["disaggregated"].format(anio=2025): zip_de(DISAGG.replace("2008-12-30", "2025-11-11")),
        URLS_COT["tff"].format(anio=2025): zip_de(TFF.replace("2026-11-03", "2025-11-11")),
    })


def leer(cfg, mercado, anio):
    ruta = cfg.data_dir / "cot" / "fuente=cftc" / f"simbolo={mercado}" / f"anio={anio}.parquet"
    return pq.read_table(ruta).to_pylist()


def test_bajar_cot_por_defecto_dos_anios(cfg):
    bajar_cot(cfg, anios=[2026], http=http_con(), ahora=T0)   # ya hay datos: no es primera vez
    h = http_con()
    res = bajar_cot(cfg, http=h, ahora=T0)
    assert res == {"legacy": 5, "disaggregated": 3, "tff": 3}   # solo lo de 2025
    assert len(h.pedidas) == 6
    assert {f["informe"] for f in leer(cfg, "ORO", 2026)} == {"legacy", "disaggregated"}
    assert leer(cfg, "ORO", 2025)
    assert bajar_cot(cfg, http=http_con(), ahora=T0) == {"legacy": 0, "disaggregated": 0, "tff": 0}


def test_cot_correccion_crea_version(cfg):
    bajar_cot(cfg, anios=[2026], http=http_con(), ahora=T0)
    corregido = LEGACY.replace("200000,50000", "210000,50000")
    t1 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    res = bajar_cot(cfg, anios=[2026], http=http_con(corregido), ahora=t1)
    assert res["legacy"] == 1
    filas = [f for f in leer(cfg, "ORO", 2026) if f["campo"] == "no_comerciales_largos"]
    assert sorted(f["valor"] for f in filas) == [200000.0, 210000.0]
    # La primera carga del año vale desde su publicación; la corrección, desde su descarga.
    publicado = datetime(2026, 11, 6, 20, 30, tzinfo=timezone.utc)
    assert sorted(f["ts_version"] for f in filas) == [publicado, t1]


def test_primera_vez_ts_version_es_ts_conocido(cfg):
    bajar_cot(cfg, anios=[2026], http=HttpFalso({
        URLS_COT["legacy"].format(anio=2026): zip_de(LEGACY),
        URLS_COT["disaggregated"].format(anio=2026): zip_de(DISAGG.replace("2008-12-30", "2026-11-03")),
        URLS_COT["tff"].format(anio=2026): zip_de(TFF)}), ahora=T0, primera_vez=True)
    for f in leer(cfg, "ORO", 2026):
        assert f["ts_version"] == f["ts_conocido"]


class HttpPorInforme:
    """Responde igual para cualquier año; guarda las URLs pedidas."""
    def __init__(self, cuerpos):
        self.cuerpos, self.pedidas = cuerpos, []

    def get(self, url, timeout, headers=None):
        self.pedidas.append(url)
        for informe, plantilla in URLS_COT.items():
            if url.startswith(plantilla.split("{")[0]):
                return 200, zip_de(self.cuerpos[informe])
        return 404, b""


def anios_pedidos(h):
    res = {}
    for url in h.pedidas:
        for informe, plantilla in URLS_COT.items():
            pref = plantilla.split("{")[0]
            if url.startswith(pref) and url.endswith(".zip"):
                res.setdefault(informe, []).append(int(url[len(pref):-4]))
    return res


def test_almacen_vacio_baja_toda_la_historia(cfg):
    vacios = {"legacy": LEGACY.split("\n")[0] + "\n",
              "disaggregated": DISAGG.split("\n")[0] + "\n", "tff": TFF.split("\n")[0] + "\n"}
    h = HttpPorInforme(vacios)
    bajar_cot(cfg, http=h, ahora=T0)
    a = anios_pedidos(h)
    assert a["legacy"] == list(range(1986, 2027))
    assert a["disaggregated"] == list(range(2006, 2027)) and a["tff"] == a["disaggregated"]


def test_correccion_tras_carga_completa_lleva_ts_descarga(cfg):
    cuerpos = {"legacy": LEGACY, "disaggregated": DISAGG.replace("2008-12-30", "2026-11-03"),
               "tff": TFF}
    bajar_cot(cfg, http=HttpPorInforme(cuerpos), ahora=T0)   # primera vez automática
    antes = leer(cfg, "ORO", 2026)
    assert all(f["ts_version"] == f["ts_conocido"] for f in antes)
    t1 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    cuerpos["legacy"] = LEGACY.replace("200000,50000", "210000,50000")
    res = bajar_cot(cfg, http=HttpPorInforme(cuerpos), ahora=t1, primera_vez=True)
    assert res["legacy"] == 1
    filas = [f for f in leer(cfg, "ORO", 2026) if f["campo"] == "no_comerciales_largos"]
    nueva = next(f for f in filas if f["valor"] == 210000.0)
    assert nueva["ts_version"] == t1
    todas = leer(cfg, "ORO", 2026)
    claves = [(f["informe"], f["fecha_informe"], f["campo"], f["ts_version"]) for f in todas]
    assert len(claves) == len(set(claves))


def test_error_de_red_sin_detalles(cfg):
    class Malo:
        def get(self, url, timeout, headers=None):
            raise ConnectionError("fallo con ?clave=SECRETA")
    with pytest.raises(RuntimeError) as e:
        bajar_cot(cfg, anios=[2026], http=Malo(), ahora=T0)
    assert "SECRETA" not in str(e.value) and "deacot2026.zip" in str(e.value)


# --- I2: "primera vez" por (mercado, informe, año), no por la forma de lanzar la carga -------

DISAGG_2006 = DISAGG.replace("2008-12-30", "2006-06-13")


def _http_2006():
    return HttpPorInforme({"legacy": LEGACY.replace("2026-11-03", "2006-06-13"),
                           "disaggregated": DISAGG_2006,
                           "tff": TFF.replace("2026-11-03", "2006-06-13")})


def _visible_2006(cfg):
    from datos.consulta import contexto
    r = contexto("cot", date(2006, 1, 1), date(2007, 1, 1), conocido_en=date(2006, 6, 30), cfg=cfg)
    return r


def test_desde_con_almacen_vacio_historial_visible(cfg):
    from datos.contexto_bajar import bajar_contexto
    t = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    res = bajar_contexto(cfg, ["cot"], [], desde=date(2006, 1, 1), http=_http_2006(),
                         hoy=t.date())
    assert not res.fallos
    r = _visible_2006(cfg)
    assert set(r["informe"]) == {"legacy"}   # Disaggregated/TFF de 2006: tardíos (2009-09-04)
    assert (r["ts_version"] == r["ts_conocido"]).all()
    assert len(r) == 5


def test_carga_cortada_y_relanzada_historial_visible(cfg):
    class Cortada(HttpPorInforme):
        def get(self, url, timeout, headers=None):
            if "2010" in url:
                raise ConnectionError("cortada")
            return super().get(url, timeout, headers)
    h = Cortada(_http_2006().cuerpos)
    with pytest.raises(RuntimeError):
        bajar_cot(cfg, anios=list(range(2006, 2012)), http=h, ahora=T0)
    bajar_cot(cfg, anios=list(range(2006, 2012)), http=_http_2006(), ahora=T0)
    r = _visible_2006(cfg)
    assert len(r) == 5 and (r["ts_version"] == r["ts_conocido"]).all()


def test_reprocesar_cot_da_lo_mismo(cfg):
    from datos.contexto_bajar import reprocesar
    bajar_cot(cfg, anios=[2006], http=_http_2006(), ahora=T0)
    corregido = _http_2006()
    corregido.cuerpos["legacy"] = corregido.cuerpos["legacy"].replace("200000,50000",
                                                                       "210000,50000")
    t1 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    bajar_cot(cfg, anios=[2006], http=corregido, ahora=t1)
    antes = sorted(map(str, leer(cfg, "ORO", 2006)))
    r = reprocesar(cfg, ["cftc"])
    assert not r.fallidos
    assert sorted(map(str, leer(cfg, "ORO", 2006))) == antes
    filas = [f for f in leer(cfg, "ORO", 2006) if f["campo"] == "no_comerciales_largos"]
    assert sorted(f["ts_version"] for f in filas)[1] == t1
    assert len(_visible_2006(cfg)) == 5
