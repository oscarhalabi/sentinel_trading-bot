# Fase 2C — Datos de contexto: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ampliar `datos/` para guardar y consultar, sin ver el futuro, el calendario económico (API + fotos de Forex Factory), FRED/ALFRED con versiones, COT, GLD y splits/dividendos, en la laptop de Oscar.

**Architecture:** El almacén de 2A gana tipos de contexto declarados en una tabla (esquema, columna de tiempo, partición) y un guardado de crudo. Cada fuente es un módulo que baja, guarda el crudo y normaliza a su tipo. Las reglas de "cuándo se conoció" viven en funciones puras con su configuración en `datos/contexto.json`. La consulta añade `contexto()` y `unir_contexto()`, que filtran por `ts_conocido`/`ts_anunciado`/`ts_version` con latencia y supuestos como parámetros.

**Tech Stack:** Python ≥ 3.12, pyarrow, duckdb, pandas, requests, psycopg2, pytest (lo mismo que 2A; sin dependencias nuevas).

**Spec:** `docs/superpowers/specs/2026-11-18-fase2c-contexto-design.md` (se apoya en `docs/superpowers/specs/2026-11-12-fase2a-nucleo-datos-design.md`).

## Global Constraints

- Rama de código `datos/contexto` desde `main` (que ya tiene 2A). Un PR al final. Commits `tipo(datos): ...` en español con `git -c user.name="Oscar" -c user.email="oscarhalabi@proton.me"` y la línea `Co-Authored-By` del entorno.
- No se toca `motor/`, Supabase, el VPS ni producción. Nada se baja en el VPS.
- Todo timestamp es `timestamp("us", tz="UTC")`. Las horas locales de publicación se convierten con `zoneinfo` (America/New_York salvo que se diga otra).
- Lo ya guardado por 2A (ticks y M1) no cambia de ruta ni de esquema; los tests de 2A siguen pasando.
- Claves de API solo en el `.env` de la laptop: `FRED_API_KEY`, `FMP_API_KEY`, `EODHD_API_KEY`, `TE_API_KEY`. Nunca en el repo, logs, informes ni manifiestos (los parámetros de URL con la clave se quitan antes de guardar el crudo).
- Tests sin red ni APIs reales (`python -m pytest tests/datos -q`); los de Postgres se saltan sin `DATOS_PG_DSN_TEST`. Las respuestas de ejemplo son *provisionales* hasta que se sustituyan por una real recortada en la laptop (Task 12).
- Valores fijos de la spec: latencia por defecto **5 s**; horizonte de anuncio por defecto **7 días** (0 para no programados); foto de FF cada **4 h** (`DATOS_FF_CADA_HORAS`); emparejar FF↔API por `evento` + `divisa` + `ts_evento` a **±90 min**; COT viernes **15:30** NY; GLD **18:00** NY; Disaggregated/TFF anteriores a su primera publicación → **2009-09-04** con `publicacion_tardia = true`; series H.15 (DGS10, DGS2, DFII10, T10YIE) y DFF → **día hábil siguiente 16:30** NY; VIXCLS → mismo día 17:00 NY; DTWEXBGS → lunes siguiente 16:30 NY; serie sin retraso configurado → día hábil siguiente 16:30 NY.
- Divisas del calendario: USD, EUR, GBP, JPY, CHF, AUD, CAD, NZD, NOK, SEK, CNY; impacto medio y alto.
- Comentarios, mensajes y docs en español sencillo.

## Review Focus

1. **Evento sin hora exacta** (la API da solo la fecha, o FF dice "All Day"/"Tentative"): no se trata como medianoche exacta; lleva `hora_exacta = false` y no entra en `min_hasta_proximo`. Test `test_evento_sin_hora_no_cuenta_como_exacto` (Task 5).
2. **Re-descarga con diferencias de redondeo** (0.3 frente a 0.30000001): no crea una versión nueva. Test `test_versionar_ignora_ruido_de_redondeo` (Task 5).
3. **Valor "." de FRED** (dato faltante): se guarda como nulo, no rompe la descarga. Test `test_fred_punto_es_nulo` (Task 4).
4. **Mercado del COT con código nuevo o desconocido**: se ignora y se cuenta en el resumen, no rompe. Test `test_cot_codigo_desconocido_se_ignora` (Task 7).
5. **Hora de FF con desplazamiento** ("2026-11-20T08:30:00-05:00", cambio de horario): se convierte a UTC exacto. Test `test_ff_hora_con_desplazamiento` (Task 5).

---

## Estructura de archivos

```
datos/
  almacen.py              (modificado) tipos de contexto, particiones anio/completo, crudo
  config.py               (modificado) claves de API, ff_cada_horas, fuentes por defecto
  contexto.json           configuración versionada (series, retrasos, COT, ETF, calendario)
  eventos.json            mapa de nombres de evento → evento común
  contexto_conf.py        carga y valida contexto.json y eventos.json
  tiempos.py              reglas puras de "cuándo se conoció"
  versiones.py            versionado solo-añadir (calendario y COT)
  fuentes/fred.py         FRED y ALFRED → tipo serie
  fuentes/forexfactory.py foto de FF → calendario
  fuentes/calendario_api.py  FMP y EODHD (y TE si llega) → calendario; corporativos
  fuentes/cftc.py         COT → tipo cot
  fuentes/spdr.py         GLD → tipo etf
  contexto_bajar.py       orquesta `contexto bajar|reprocesar`
  prueba_calendario.py    `contexto probar-calendario` e informe
  consulta.py             (modificado) contexto(), unir_contexto()
  calidad_contexto.py     chequeos de contexto
  calidad.py              (modificado) informe: tamaño por tipo y crecimiento
  __main__.py             (modificado) orden `contexto`
scripts/instalar_tarea_ff.ps1
tests/datos/test_*.py     uno por pieza; fixtures en tests/datos/fixtures/contexto/
```

---

### Task 1: Almacén con tipos de contexto y crudo

**Files:**
- Modify: `datos/almacen.py`
- Test: `tests/datos/test_almacen_contexto.py`

**Interfaces:**
- Produces:
  - Esquemas `ESQUEMA_CALENDARIO`, `ESQUEMA_SERIE`, `ESQUEMA_COT`, `ESQUEMA_ETF`, `ESQUEMA_CORPORATIVO` (todos los timestamps `us` UTC):
    - calendario: `ts_evento, ts_anunciado, ts_conocido, ts_version, ts_foto` (timestamp, `ts_foto` nulo salvo FF); `fuente, id_fuente, pais, divisa, evento, evento_fuente, categoria, impacto, unidad` (string); `programado, hora_exacta, prevision_es_pit` (bool); `real, prevision, anterior, anterior_revisado` (float64, nulos permitidos).
    - serie: `serie` (string), `periodo` (date32), `valor` (float64 nulo), `ts_conocido`, `ts_hasta` (timestamp, nulo = vigente), `fuente` (string), `publicacion_estimada` (bool).
    - cot (formato largo): `mercado, informe, campo` (string), `fecha_informe` (date32), `valor` (float64), `ts_conocido`, `ts_version` (timestamp), `publicacion_tardia` (bool).
    - etf: `etf` (string), `fecha` (date32), `toneladas`, `valor_usd` (float64), `ts_conocido`.
    - corporativo: `simbolo, tipo, fuente` (string), `fecha_ex`, `fecha_anuncio` (date32, nulo), `razon`, `importe` (float64, nulos), `divisa` (string nulo), `ts_conocido`.
  - `TIPOS: dict[str, TipoDatos]` con `@dataclass(frozen=True) TipoDatos(esquema: pa.Schema, columna_tiempo: str, particion: Literal["dia","mes","anio","completo"], version: int)`. ticks → (`ts`, dia), m1 → (`ts`, mes), calendario → (`ts_evento`, anio), serie → (`ts_conocido`, completo), cot → (`ts_conocido`, anio), etf → (`ts_conocido`, completo), corporativo → (`ts_conocido`, completo). `_ESQUEMAS`/`VERSION_ESQUEMA` pasan a derivarse de `TIPOS`.
  - `ruta_relativa(tipo, fuente, simbolo, periodo: date | None) -> PurePosixPath`: dia/mes igual que hoy; anio → `tipo/fuente=F/simbolo=S/anio=AAAA.parquet`; completo → `tipo/fuente=F/simbolo=S/completo.parquet` (`periodo` ignorado). Texto de periodo en el manifiesto: `"AAAA"` (anio) y `"completo"`.
  - `escribir`, `marcar_vacio`, `estado` aceptan los nuevos tipos con `periodo: date | None`. `_validar` exige las columnas del esquema y que `columna_tiempo` sea timestamp `us` UTC; ordena por `columna_tiempo`; `ts_min/ts_max` salen de esa columna.
  - `escribir_crudo(raiz, contenido: bytes, fuente: str, nombre: str, momento: datetime, extension: str) -> Manifiesto`: guarda en `crudo/fuente=F/AAAA/MM/DD/<HHMMSS>_<nombre>.<extension>.zst` comprimido con `pyarrow.CompressedOutputStream(..., "zstd")`; manifiesto `tipo="crudo"`, `periodo="AAAA-MM-DD"`, `ts_min=ts_max=momento`, `filas=0`.
  - `leer_crudo(raiz, ruta_rel: str) -> bytes` y `listar_crudos(raiz, fuente: str | None = None) -> Iterator[Manifiesto]` (por orden de `ts_min`).

- [ ] **Step 1: Tests:**
  - `test_rutas_anio_y_completo`: calendario/USD/2024 → `calendario/fuente=fmp/simbolo=USD/anio=2024.parquet`; serie/PAYEMS → `serie/fuente=alfred/simbolo=PAYEMS/completo.parquet`.
  - `test_escribe_y_estado_de_cada_tipo` (parametrizado por los 5 tipos con una tabla mínima): `estado == "ok"`, `periodo` del manifiesto `"2024"` o `"completo"`.
  - `test_columna_tiempo_sin_zona_se_rechaza` (calendario con `ts_evento` sin zona → `EsquemaInvalido`).
  - `test_crudo_ida_y_vuelta`: `leer_crudo(escribir_crudo(b"{...}").ruta) == b"{...}"` y el archivo en disco está comprimido (no contiene el texto plano).
  - `test_ticks_y_m1_no_cambian`: las rutas de ticks/m1 son las mismas que en 2A.
- [ ] **Step 2:** `python -m pytest tests/datos/test_almacen_contexto.py -v` → FAIL.
- [ ] **Step 3:** Implementar en `datos/almacen.py`.
- [ ] **Step 4:** `python -m pytest tests/datos -q` → todo PASS (incluido 2A).
- [ ] **Step 5:** Commit `feat(datos): almacén con tipos de contexto y crudo`.

### Task 2: Configuración de contexto y claves

**Files:**
- Create: `datos/contexto.json`, `datos/eventos.json`, `datos/contexto_conf.py`
- Modify: `datos/config.py`
- Test: `tests/datos/test_contexto_conf.py`

**Interfaces:**
- Produces:
  - `Config` gana: `fred_api_key: str = ""`, `fmp_api_key: str = ""`, `eodhd_api_key: str = ""`, `te_api_key: str = ""`, `ff_cada_horas: float = 4.0` (`DATOS_FF_CADA_HORAS`), `fuente_calendario: str = "fmp"` (`DATOS_FUENTE_CALENDARIO`), `fuente_corporativo: str = "fmp"` (`DATOS_FUENTE_CORPORATIVO`). `repr(Config)` no muestra las claves.
  - `datos/contexto.json` con claves: `divisas_calendario` (lista de §Global), `impacto_min` (`"medio"`), `horizonte_anuncio_dias` (7), `latencia_por_defecto` (`"5s"`), `series_alfred` (lista de `{"id", "hora", "zona", "retraso_tipico_dias"}`: PAYEMS 08:30/7, UNRATE 08:30/7, CPIAUCSL 08:30/14, CPILFESL 08:30/14, PCEPI 08:30/30, PCEPILFE 08:30/30, GDP 08:30/30, RSAFS 08:30/15, ICSA 08:30/5), `series_fred` (lista de `{"id", "retraso"}` con `retraso` en `"habil+1@16:30"`, `"mismo@17:00"`, `"lunes@16:30"`), `cot` (`{"mercados": {nombre: codigo_cftc}, "campos": [...], "excepciones_publicacion": {"AAAA-MM-DD": "AAAA-MM-DDTHH:MM"}, "primera_publicacion": {"disaggregated": "2009-09-04", "tff": "2009-09-04"}}`), `etf` (`{"GLD": {"hora": "18:00"}}`).
  - `datos/eventos.json`: `{"<fuente>:<nombre original>": {"evento": "US_NFP", "categoria": "empleo"}}` con los eventos de impacto alto de USD y EUR como mínimo (NFP, desempleo, IPC, IPC subyacente, PCE, PIB, ventas minoristas, ISM, decisión de tipos de la Fed, del BCE y del BoE, solicitudes semanales).
  - `contexto_conf.cargar_contexto(ruta=...) -> dict` y `contexto_conf.cargar_eventos(ruta=...) -> dict` validan forma y valores (hora `HH:MM`, zona válida para `zoneinfo`, retraso con formato conocido, divisas de 3 letras) y abortan con `ConfigInvalida` nombrando la clave.
  - `contexto_conf.normalizar_evento(fuente: str, nombre: str, mapa: dict) -> tuple[str, str]` → `(evento, categoria)`; sin mapear → `(nombre, "otros")`.

- [ ] **Step 1: Tests:** `test_contexto_del_repo_es_valido`, `test_hora_mal_formada_aborta`, `test_zona_desconocida_aborta`, `test_retraso_desconocido_aborta`, `test_normalizar_mapeado_y_sin_mapear`, `test_config_lee_claves_y_no_las_muestra` (`"secreto" not in repr(cfg)`).
- [ ] **Step 2:** FAIL → implementar → `python -m pytest tests/datos -q` PASS.
- [ ] **Step 3:** Commit `feat(datos): configuración de contexto y claves de API`.

### Task 3: Reglas de "cuándo se conoció" y versionado

**Files:**
- Create: `datos/tiempos.py`, `datos/versiones.py`
- Test: `tests/datos/test_tiempos.py`, `tests/datos/test_versiones.py`

**Interfaces:**
- Consumes: `contexto_conf.cargar_contexto()`, `horarios` (festivos de EE. UU. ya existentes en `datos/horarios.py` para "día hábil").
- Produces (funciones puras):
  - `local_a_utc(dia: date, hora: str, zona: str) -> datetime`.
  - `publicacion_serie_mercado(dia: date, retraso: str) -> datetime` para `"habil+1@HH:MM"` (siguiente día hábil de EE. UU., saltando fines de semana y festivos federales), `"mismo@HH:MM"`, `"lunes@HH:MM"` (lunes siguiente a la fecha).
  - `publicacion_alfred(fecha_vintage: date, serie_conf: dict) -> datetime` (fecha + hora local de la serie).
  - `estimar_publicacion(periodo: date, serie_conf: dict) -> datetime` = fin del periodo mensual/trimestral + `retraso_tipico_dias`, a la hora de la serie.
  - `detectar_carga_en_bloque(vintages: pd.DataFrame) -> pd.Series[bool]`: marca las filas cuya fecha de publicación la comparten ≥ 24 periodos que abarcan > 365 días.
  - `publicacion_cot(fecha_informe: date, informe: str, cot_conf: dict) -> tuple[datetime, bool]`: viernes de esa semana 15:30 NY, o la excepción configurada; si `fecha_informe` es anterior a la primera publicación del informe, devuelve esa fecha a las 15:30 y `True`.
  - `publicacion_etf(fecha: date, etf_conf: dict) -> datetime`.
  - `versiones.versionar(previas: pd.DataFrame, nuevas: pd.DataFrame, clave: list[str], campos: list[str], ts_version: datetime, tolerancia: float = 1e-9) -> pd.DataFrame`: devuelve previas + solo las filas nuevas cuya combinación de `campos` difiere de la **última versión** de su `clave` (floats comparados con tolerancia relativa; nulos iguales entre sí), con `ts_version` puesto.

- [ ] **Step 1: Tests:**
  - `test_habil_siguiente_salta_festivo` (miércoles 2024-07-03 → viernes 2024-07-05 a 16:30 NY, porque el 4 de julio es festivo) y `test_habil_siguiente_salta_fin_de_semana` (viernes 2024-05-10 → lunes 2024-05-13).
  - `test_mismo_dia_en_cambio_de_horario` (VIXCLS el 2024-03-11: 17:00 NY = 21:00 UTC).
  - `test_cot_viernes_y_excepcion` (informe del martes 2024-11-26 con excepción configurada a lunes 2024-12-02 15:30).
  - `test_cot_disaggregated_2007_publicacion_tardia` → (2009-09-04 15:30 NY, True).
  - `test_estimar_publicacion_payems` (periodo 1985-03 → 1985-04-07 08:30 NY con 7 días).
  - `test_detectar_carga_en_bloque` (300 periodos con la misma fecha → todos True; 3 periodos con su fecha propia → False).
  - `test_versionar_agrega_solo_cambios`, `test_versionar_ignora_ruido_de_redondeo` (0.3 vs 0.30000001 → sin fila nueva), `test_versionar_nulos_iguales`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): reglas de publicación sin futuro y versionado`.

### Task 4: FRED y ALFRED

**Files:**
- Create: `datos/fuentes/fred.py`, `tests/datos/fixtures/contexto/alfred_payems.json`, `tests/datos/fixtures/contexto/fred_dgs10.json`
- Test: `tests/datos/test_fred.py`

**Interfaces:**
- Consumes: `ClienteHttp` (2A), `escribir`, `escribir_crudo` (Task 1), `tiempos.*` (Task 3), `cargar_contexto` (Task 2).
- Produces:
  - `URL_OBSERVACIONES = "https://api.stlouisfed.org/fred/series/observations"`.
  - `normalizar_alfred(cuerpo: bytes, serie_conf: dict) -> pa.Table` (ESQUEMA_SERIE): una fila por (periodo, `realtime_start`); `ts_conocido = publicacion_alfred(realtime_start)`, `ts_hasta` = siguiente versión de ese periodo o nulo; filas marcadas por `detectar_carga_en_bloque` → `ts_conocido = estimar_publicacion(periodo)` y `publicacion_estimada = True`; valor `"."` → nulo.
  - `normalizar_fred(cuerpo: bytes, serie_conf: dict) -> pa.Table`: sin versiones, `ts_conocido = publicacion_serie_mercado(fecha, retraso)`, `ts_hasta` nulo.
  - `bajar_series(cfg, http=None, ids: list[str] | None = None) -> dict[str, int]` (filas por serie): pide con `realtime_start=1776-07-04&realtime_end=9999-12-31` (ALFRED) o sin ellos (FRED de mercado), guarda el crudo **sin** `api_key` en la URL guardada, normaliza y escribe `serie/fuente=alfred|fred/simbolo=<id>/completo.parquet`. Sin `FRED_API_KEY` → `ConfigInvalida` en español.
- [ ] **Step 1: Tests** (fixtures provisionales hechos a mano con la forma de la API: `{"observations": [{"realtime_start","realtime_end","date","value"}]}`):
  - `test_alfred_versiones_y_ts_hasta` (dos versiones de 2024-03 → la primera con `ts_hasta` = conocido de la segunda).
  - `test_alfred_carga_en_bloque_estimada`.
  - `test_fred_punto_es_nulo`.
  - `test_fred_dgs10_dia_habil_siguiente`.
  - `test_crudo_no_guarda_la_clave` (el manifiesto y el crudo no contienen `api_key=`).
  - `test_sin_clave_aborta`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): FRED y ALFRED con versiones y fechas de publicación`.

### Task 5: Foto de Forex Factory y modelo de calendario

**Files:**
- Create: `datos/fuentes/forexfactory.py`, `tests/datos/fixtures/contexto/ff_thisweek.json`
- Test: `tests/datos/test_forexfactory.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `URLS_FF = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")`.
  - `normalizar_ff(cuerpo: bytes, ts_foto: datetime, mapa_eventos: dict, divisas: list[str]) -> pa.Table` (ESQUEMA_CALENDARIO): `fuente="forexfactory"`, `ts_evento` desde la fecha ISO con desplazamiento convertida a UTC; horas "All Day", "Tentative" o vacías → `hora_exacta=False` y `ts_evento` = medianoche NY de ese día; `impacto` de "High/Medium/Low"; `prevision`, `anterior`, `real` parseados de texto ("1.2%", "250K", "-0.3M" → número; unidad aparte); `ts_foto = ts_anunciado = ts_version = ts_foto`; `ts_conocido = ts_evento`; `prevision_es_pit = True`; `programado = True`.
  - `guardar_foto(cfg, http=None, ahora=None) -> int` (filas nuevas): guarda los dos JSON en crudo y mezcla en `calendario/fuente=forexfactory/simbolo=<divisa>/anio=AAAA.parquet` con `versionar(clave=["evento","divisa","ts_evento"], campos=["prevision","real","anterior","ts_evento","impacto"])`. `ts_anunciado` de un evento = el de su **primera** versión.
- [ ] **Step 1: Tests:**
  - `test_ff_hora_con_desplazamiento` ("2026-11-20T08:30:00-05:00" → 13:30 UTC).
  - `test_evento_sin_hora_no_cuenta_como_exacto`.
  - `test_ff_parsea_unidades` ("250K" → 250000; "0.3%" → 0.3 con unidad "%").
  - `test_dos_fotos_sin_cambios_no_duplican` y `test_prevision_cambiada_crea_version`.
  - `test_ts_anunciado_es_la_primera_foto`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): foto de Forex Factory al calendario`.

### Task 6: Calendario de la API (FMP, EODHD) y corporativos

**Files:**
- Create: `datos/fuentes/calendario_api.py`, fixtures `fmp_calendario.json`, `eodhd_eventos.json`, `fmp_splits.json`, `fmp_dividendos.json`, `eodhd_splits.json`, `eodhd_dividendos.json`
- Test: `tests/datos/test_calendario_api.py`

**Interfaces:**
- Consumes: Tasks 1–3, registro de símbolos (2A) para la lista de acciones y ETFs.
- Produces:
  - `class FuenteCalendario(Protocol)`: `nombre: str`; `bajar_eventos(desde: date, hasta: date) -> bytes`; `normalizar_eventos(cuerpo: bytes, ts_descarga: datetime, historico: bool) -> pa.Table`; `bajar_corporativos(ticker: str) -> list[bytes]`; `normalizar_corporativos(cuerpos: list[bytes], simbolo: str) -> pa.Table`.
  - `class FMP(cfg, http=None)` (`https://financialmodelingprep.com/stable/economic-calendar?from=&to=&apikey=`; splits `/stable/splits?symbol=`; dividendos `/stable/dividends?symbol=`) y `class EODHD(cfg, http=None)` (`https://eodhd.com/api/economic-events?from=&to=&limit=1000&offset=&fmt=json&api_token=`; `/api/splits/<TICKER>.US`, `/api/div/<TICKER>.US`). Rutas exactas provisionales hasta la Task 12.
  - `FUENTES_CALENDARIO = {"fmp": FMP, "eodhd": EODHD}` (TE se añade si llega su cotización).
  - Normalización: país → divisa (tabla en el módulo), solo divisas de `divisas_calendario` e impacto ≥ `impacto_min`; fecha sin hora → `hora_exacta=False`; `prevision_es_pit = not historico`; `ts_version = ts_anunciado = ts_descarga` si no es histórico; en histórico `ts_anunciado = ts_evento − horizonte_anuncio_dias` y `ts_version = ts_anunciado`.
  - `bajar_calendario(cfg, fuente: str, desde: date, hasta: date, historico: bool, http=None) -> int` por meses, crudo de cada página, mezcla con `versionar` (misma clave/campos que Task 5) en `calendario/fuente=<fuente>/simbolo=<divisa>/anio=AAAA.parquet`.
  - `bajar_corporativos(cfg, fuente: str, simbolos: list[Simbolo], http=None) -> int`: `simbolo` canónico (`NVDA.US`) → ticker de la fuente; `ts_conocido` = `fecha_anuncio` a las 09:30 NY si existe, si no apertura del día ex (09:30 NY); escribe `corporativo/fuente=<fuente>/simbolo=<simbolo>/completo.parquet`.
- [ ] **Step 1: Tests:** `test_fmp_normaliza_y_filtra_divisas`, `test_eodhd_pagina_con_offset` (cliente falso con 2 páginas), `test_historico_ts_anunciado_horizonte`, `test_no_historico_prevision_es_pit`, `test_fecha_sin_hora`, `test_corporativo_split_y_dividendo`, `test_corporativo_sin_anuncio_usa_apertura_ex`, `test_crudo_no_guarda_la_clave`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): calendario y corporativos de FMP y EODHD`.

### Task 7: COT de la CFTC

**Files:**
- Create: `datos/fuentes/cftc.py`, fixtures `cot_legacy.csv`, `cot_disagg.csv`, `cot_tff.csv` (pocas filas)
- Test: `tests/datos/test_cftc.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `URLS_COT = {"legacy": "https://www.cftc.gov/files/dea/history/deacot{anio}.zip", "disaggregated": "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{anio}.zip", "tff": "https://www.cftc.gov/files/dea/history/fut_fin_txt_{anio}.zip"}` (provisionales hasta Task 12).
  - `normalizar_cot(csv: bytes, informe: str, cot_conf: dict, ts_descarga: datetime) -> pa.Table` (ESQUEMA_COT largo): filtra por los códigos de `cot.mercados`, una fila por (mercado, fecha_informe, campo) para los `campos` configurados, `ts_conocido`/`publicacion_tardia` de `publicacion_cot`; códigos desconocidos se ignoran y se cuentan.
  - `bajar_cot(cfg, anios: list[int] | None = None, http=None) -> dict[str, int]`: baja los zip por año e informe, crudo, normaliza y mezcla con `versionar(clave=["mercado","informe","fecha_informe","campo"], campos=["valor"])` en `cot/fuente=cftc/simbolo=<mercado>/anio=AAAA.parquet`. Por defecto: el año actual y el anterior; la primera vez, todos desde 1986 (legacy) y 2006 (resto).
- [ ] **Step 1: Tests:** `test_cot_viernes`, `test_cot_disaggregated_viejo_publicacion_tardia`, `test_cot_codigo_desconocido_se_ignora`, `test_cot_correccion_crea_version`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): posicionamiento COT de la CFTC`.

### Task 8: GLD (SPDR)

**Files:**
- Create: `datos/fuentes/spdr.py`, fixture `gld.csv`
- Test: `tests/datos/test_spdr.py`

**Interfaces:**
- Produces: `URL_GLD = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"` (provisional); `normalizar_gld(csv: bytes, etf_conf: dict) -> pa.Table` (ESQUEMA_ETF; filas sin fecha o sin toneladas se saltan y se cuentan; si faltan las columnas esperadas → `ValueError` con el nombre de la columna); `bajar_gld(cfg, http=None) -> int` (crudo + `etf/fuente=spdr/simbolo=GLD/completo.parquet`).
- [ ] **Step 1: Tests:** `test_gld_normaliza_y_ts_conocido_18h`, `test_gld_formato_cambiado_avisa`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): tenencias de GLD`.

### Task 9: Órdenes `contexto bajar`, `reprocesar` y `probar-calendario`

**Files:**
- Create: `datos/contexto_bajar.py`, `datos/prueba_calendario.py`
- Modify: `datos/__main__.py`
- Test: `tests/datos/test_contexto_bajar.py`, `tests/datos/test_prueba_calendario.py`

**Interfaces:**
- Consumes: Tasks 4–8, catálogo de 2A (`registrar`, `abrir_corrida`, `cerrar_corrida`).
- Produces:
  - CLI: `python -m datos contexto bajar [calendario|ff|alfred|fred|cot|gld|corporativo ...] [--desde AAAA-MM-DD] [--historico]`; sin argumentos baja todo menos `calendario --historico`. Una fuente que falla no para las demás; sale 1 si alguna falló.
  - `python -m datos contexto reprocesar [--fuente F]`: rehace los Parquet de contexto desde `listar_crudos` en orden de `ts_min`, sin red. Debe dar las mismas filas.
  - `python -m datos contexto probar-calendario --fuentes fmp,eodhd[,te] [--desde 2008-01-01 --hasta 2008-01-31] [--desde2 2024-01-01 --hasta2 2024-01-31]`: para cada fuente baja ambos meses (USD, EUR, JPY), y escribe `_informes/prueba-calendario-AAAA-MM-DD.html` con: primer año con datos, % con hora exacta, % con previsión, coincidencia de hora y de dato real con ALFRED en eventos de EE. UU. mapeados (requiere ALFRED bajado; si no, lo dice), divisas cubiertas y errores o límites encontrados. Una fuente sin clave aparece como "no probada (falta clave)".
  - `cfg.fuente_calendario` decide la fuente de `contexto bajar calendario`.
- [ ] **Step 1: Tests** (fuentes falsas): `test_bajar_una_fuente_falla_las_demas_siguen`, `test_reprocesar_da_las_mismas_filas`, `test_prueba_informe_compara_con_alfred`, `test_prueba_sin_clave_no_probada`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): órdenes de contexto y prueba de APIs de calendario`.

### Task 10: Consulta de contexto sin futuro

**Files:**
- Modify: `datos/consulta.py`
- Test: `tests/datos/test_consulta_contexto.py`

**Interfaces:**
- Consumes: Tasks 1, 3; reserva y `_registrar_acceso` de 2A.
- Produces:
  - `contexto(tipo: str, desde, hasta, conocido_en=None, claves: list[str] | None = None, fuente: str | None = None, latencia: str | timedelta = "5s", prevision: Literal["ff","api","mejor"] = "mejor", horizonte_dias: float | None = None, excluir_estimadas: bool = False, incluir_reservado: bool = False, cfg=None) -> pd.DataFrame`
    - Por fila, la última versión con `ts_version ≤ conocido_en` (tipos con `ts_version`).
    - `calendario`: eventos con `ts_anunciado ≤ conocido_en` (si `horizonte_dias` se da, se recalcula `ts_anunciado = ts_evento − horizonte` para filas históricas); `real` y `sorpresa` nulos salvo si `ts_conocido + latencia ≤ conocido_en`. Añade `sorpresa = real − prevision`, `fuente_prevision` y `revision_anterior = anterior_revisado − anterior`. `prevision="ff"` usa la última foto de FF **anterior a `ts_evento`** emparejada (±90 min); `"api"` la de la API; `"mejor"` FF si existe, si no API.
    - `serie`: versión vigente en `conocido_en` (`ts_conocido + latencia ≤ t < ts_hasta`); `excluir_estimadas` quita `publicacion_estimada`.
    - Resto: `ts_conocido + latencia ≤ conocido_en`.
    - Reserva de 12 meses por `ts_conocido` (o `ts_anunciado` en calendario), mismo `ReservaBloqueada` y registro de accesos que 2A.
  - `unir_contexto(precios: pd.DataFrame, tipos: dict[str, list[str]], latencia="5s", prevision="mejor", impacto_min="alto", divisas: list[str] | None = None, cfg=None) -> pd.DataFrame`: `precios` debe tener `ts_cierre`. Para `serie`/`cot`/`etf`, una columna por clave (y por campo en `cot`: `cot_<mercado>_<campo>`) con el último valor conocido en `ts_cierre` (`merge_asof` hacia atrás). Para `calendario` añade `min_hasta_proximo`, `proximo_evento` (solo `hora_exacta`), `min_desde_ultimo`, `sorpresa_ultimo`.
- [ ] **Step 1: Tests:**
  - `test_ipc_antes_de_su_hora_anunciado_sin_dato`, `test_latencia_retrasa_el_dato`.
  - `test_evento_reprogramado_hora_vieja_y_nueva`.
  - `test_revision_alfred_no_se_ve_antes`, `test_excluir_estimadas`.
  - `test_h15_dia_habil_siguiente` (vía consulta).
  - `test_cot_disaggregated_2007_no_existe_antes_de_2009`.
  - `test_prevision_ff_usa_foto_anterior_al_evento` (foto posterior al evento ignorada).
  - `test_unir_contexto_nunca_usa_dato_posterior` (para cada vela, el `ts_conocido` usado ≤ `ts_cierre`).
  - `test_unir_contexto_proximo_evento_y_sorpresa`.
  - `test_reserva_bloquea_contexto`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): consulta de contexto sin ver el futuro`.

### Task 11: Verificación de contexto e informe

**Files:**
- Create: `datos/calidad_contexto.py`
- Modify: `datos/calidad.py`, `datos/__main__.py` (`verificar` también llama al contexto)
- Test: `tests/datos/test_calidad_contexto.py`

**Interfaces:**
- Produces: `verificar_contexto(cfg, catalogo, desde: date | None = None) -> list[Problema]` con chequeos: fotos de FF que faltan (aviso; huecos > `ff_cada_horas` × 1,5 entre fotos), eventos reprogramados (info), semanas de COT que faltan por mercado/informe (aviso), huecos en series FRED de mercado en días hábiles (aviso), versiones ALFRED con `ts_hasta ≤ ts_conocido` (grave), dato real API ≠ ALFRED en primera publicación (grave; tolerancia relativa 1e-6), diferencia previsión API − FF por evento (info, con mediana y p90 en el informe), eventos sin mapear (info). `escribir_informe` añade una sección "Tamaño por tipo" (GB por tipo de manifiesto y crecimiento del último mes) para todos los tipos.
- [ ] **Step 1: Tests:** `test_falta_foto_ff`, `test_alfred_fechas_incoherentes_grave`, `test_real_api_distinto_de_alfred_grave`, `test_cot_semana_faltante`, `test_informe_tamano_por_tipo`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): verificación de contexto y tamaño por tipo`.

### Task 12: Tarea de la foto de FF, puesta en marcha en la laptop y docs

**Files:**
- Create: `scripts/instalar_tarea_ff.ps1`
- Modify: `docs/datos-de-mercado.md`, `.claude/skills/datos-mercado/SKILL.md`, `docs/memory/fase2-datos-decisiones.md`, `CLAUDE.md`, fixtures de contexto (sustituir por respuestas reales recortadas)
- Test: `tests/test_instalar_tarea_ff_ps1.py` (estilo del test de `run_motor.ps1` de Fase 1: el script tiene `-DryRun`, nombre `SentinelDatosFF`, repetición `DATOS_FF_CADA_HORAS`)

- [ ] **Step 1:** `scripts/instalar_tarea_ff.ps1 [-DryRun] [-Horas 4]`: registra `SentinelDatosFF` (usuario actual, cada N horas, `python -m datos contexto bajar ff` en la carpeta del repo, log en `DATA_DIR/_registro/ff.log`). Con `-DryRun` solo muestra la tarea. Test del script → PASS. Commit `feat(datos): tarea programada de la foto de Forex Factory`.
- [ ] **Step 2 (laptop):** `.env` con `FRED_API_KEY`, `FMP_API_KEY`, `EODHD_API_KEY`. `python -m pytest -q` completo → pasa.
- [ ] **Step 3 (laptop):** primera foto `python -m datos contexto bajar ff`; **Oscar registra** la tarea con `scripts/instalar_tarea_ff.ps1` (primero `-DryRun`).
- [ ] **Step 4 (laptop):** `contexto bajar alfred fred`; sustituir fixtures de FRED/ALFRED por respuestas reales recortadas; ajustar `normalizar_*` si el formato real difiere; tests → PASS.
  - Calibrar `retraso_tipico_dias` de `series_alfred` en `datos/contexto.json` con los vintages reales (máximo retraso observado por serie; hoy son valores prudentes a mano).
- [ ] **Step 5 (laptop):** `contexto probar-calendario --fuentes fmp,eodhd` (y `te` si llegó la cotización y Oscar la aprobó). Revisar el informe con Oscar; **Oscar elige la fuente y aprueba el pago**; fijar `DATOS_FUENTE_CALENDARIO` y `DATOS_FUENTE_CORPORATIVO`. Sustituir fixtures del calendario por reales.
- [ ] **Step 6 (laptop):** `contexto bajar calendario --historico`, `cot`, `gld`, `corporativo`; sustituir fixtures por reales; `verificar`; explicar o corregir los graves.
  - Rellenar `cot.excepciones_publicacion` con las fechas reales de publicación de la CFTC en cierres del gobierno (2013, 2018-19) y festivos especiales.
- [ ] **Step 7 (laptop):** comprobar a mano 3 NFP: `unir_contexto(cargar("XAUUSD", ..., tf="m1"), {"calendario": ["USD"]})` con la sorpresa y el oro 30 min antes y después; tiempo de consulta en segundos.
- [ ] **Step 8:** docs (`docs/datos-de-mercado.md`: contexto, sin futuro, parámetros, foto de FF), skill `datos-mercado` (bajar contexto, probar APIs, reprocesar), `CLAUDE.md` (línea de `datos/` con contexto y la tarea `SentinelDatosFF`), memoria (fuente elegida, precios, formatos reales, tamaños). Una semana con la foto corriendo sola. `/code-review` y PR `datos/contexto` → `main`.
