# Fase 2 — Datos históricos: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tener en el PC de investigación de Oscar ticks bid/ask y velas M1 de unos 30 símbolos (Dukascopy desde lo más antiguo y Vantage desde su servidor en adelante), más calendario, macro, COT y GLD, guardados en Parquet, catalogados en Postgres, verificados y al día solos, sin tocar producción.

**Architecture:** Paquete nuevo `datos/`, independiente de `motor/`. Cada fuente es una clase con la misma interfaz pequeña y escribe en un almacén Parquet particionado (`DATA_DIR`) con escritura atómica y un manifiesto por archivo. Un catálogo Postgres (`sentinel_datos`, solo en el PC) se alimenta de los manifiestos y se puede reconstruir desde ellos. El VPS solo captura Vantage desde un clon MT5 propio (`mt5_datos`) y sube a Cloudflare R2. El PC baja de R2, completa lo demás, verifica y expone vistas DuckDB.

**Tech Stack:** Python 3.14 (o la última con wheels de pyarrow/duckdb), pyarrow, duckdb, requests, boto3 (API S3 de R2), psycopg2, MetaTrader5 (solo VPS), pytest, PowerShell 5.1 (tareas programadas).

**Spec:** `docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md`

## Global Constraints

- Las reglas de convivencia de `CLAUDE.md` siguen vigentes. **Nunca** tocar `C:\Users\Administrator\Documents\sentinel_trading`, sus tareas `Sentinel*` (incluida `SentinelTickCollector`), `C:\MT5_instances` ni las cuentas 26329953/55/57.
- La Fase 2 **no escribe en Supabase** y no añade migraciones `n1_*`.
- El clon de datos vive en `MT5_CLONES_DIR` (`C:\MT5_instances_n1\mt5_datos`) y pasa `registry.validate_mt5_target`.
- Tareas programadas: en el VPS `SentinelN1Datos`; en el PC `SentinelDatosPC`. Se registran solo cuando una persona lo pide.
- Todas las columnas `ts` en UTC. La hora cruda del servidor MT5 va aparte (`ts_servidor`).
- Los datos crudos no se editan: se rehacen. Escritura atómica (temporal + renombrar) y manifiesto al final.
- Claves (R2, FRED, API de calendario) solo en el `.env` de cada máquina. Nunca en el repo ni en logs.
- Tests sin red, sin MT5, sin R2 y sin bases reales (los de Postgres real se saltan si falta `DATOS_PG_DSN_TEST`). `python -m pytest -q` pasa en Linux y en Windows.
- Git: ramas `datos/<tema>`, un PR por bloque a `main`, commits en español `tipo(ámbito): ...` con identidad de quien lo pide y la línea `Co-Authored-By`. Lo que toca el VPS lleva en el PR una explicación clara para Daniel.
- Documentación y comentarios en español sencillo.

## Review Focus

1. **Horas.** La conversión servidor Vantage → UTC acierta en las semanas en que EE. UU. y Europa cambian de horario en fechas distintas. Lo fija `test_utc_semanas_cambio_horario` (Task 6) y, con datos reales, el cruce con desfase 0 (Task 11).
2. **Archivos a medias.** Si un proceso muere mientras escribe, no queda ningún Parquet sin manifiesto que se tome por bueno, y la corrida siguiente lo rehace. Lo fija `test_escritura_interrumpida_se_rehace` (Task 2).
3. **Producción.** `capturar-mt5` se niega a usar un terminal de `C:\MT5_instances` o una cuenta de producción, y no se queda corriendo. Lo fijan `test_captura_rechaza_produccion` y `test_captura_desconecta_al_terminar` (Task 7).
4. **Reanudar sin duplicar.** Dos corridas seguidas (o una cortada a la mitad) dan exactamente las mismas filas. Lo fijan `test_dukascopy_reanuda` (Task 4) y `test_captura_solape_sin_duplicados` (Task 7).
5. **Mirar el futuro.** FRED y COT guardan fecha de publicación, y `cargar_contexto(..., segun_publicacion=True)` nunca devuelve un dato antes de publicarse. Lo fija `test_fred_vintages_sin_futuro` (Task 10).

---

## Bloque 0 — Comprobaciones previas (sin código, antes de empezar)

### Task 0: Verificar supuestos

- [ ] **Step 1 (PC, Oscar):** versión de Python instalada y si `pip install pyarrow duckdb` funciona en ella. Anotar la versión elegida.
- [ ] **Step 2 (PC, Oscar):** instalar PostgreSQL en el PC si no está. Crear rol y bases `sentinel_datos` y `sentinel_datos_test`. Variables de usuario `DATOS_PG_DSN` y `DATOS_PG_DSN_TEST`. (Ojo con los signos en la clave: van codificados en el DSN, igual que en la Fase 1.)
- [ ] **Step 3 (PC):** elegir `DATA_DIR` en el SSD (p. ej. `D:\sentinel_datos`) y comprobar el espacio libre.
- [ ] **Step 4 (Oscar):** activar R2 en la cuenta de Cloudflare de Oscar y crear dos buckets privados, `sentinel-datos` (respaldo completo) y `sentinel-datos-vps` (lo que sube el VPS), con dos claves de API: la del PC (lectura y escritura en los dos) y la del VPS (lectura y escritura solo en `sentinel-datos-vps`). Pedir también una clave gratis de FRED.
- [ ] **Step 5 (Daniel):** crear una demo **RAW** nueva en Vantage para datos (y, si se aprueba 7.10, una demo **Standard**). Confirmar la comisión por lote de RAW y anotarla.
- [ ] **Step 6:** comprobar qué símbolos del universo existen en Dukascopy y desde qué fecha (una petición de prueba por símbolo a la primera hora disponible). Comprobar sus nombres en Vantage con `mt5.symbols_get()` (solo lectura, en el clon de datos cuando exista). Rellenar `datos/simbolos.json` con lo real.
- [ ] **Step 7:** anotar todo en `docs/memory/fase2-datos-decisiones.md`.

---

## Bloque A — Base y almacén. Rama `datos/base-almacen`

### Task 1: Paquete `datos/`, configuración, CLI y registro de símbolos

**Files:**
- Create: `datos/__init__.py`, `datos/__main__.py` (CLI con subcomandos), `datos/config.py` (`DATA_DIR`, `DATOS_PG_DSN`, `R2_*`, `FRED_API_KEY`, `CALENDARIO_API_KEY`, leídos del entorno o del `.env` con `registry.read_env_file`), `datos/simbolos.py`, `datos/simbolos.json`, `requirements-datos.txt`, `tests/datos/__init__.py`, `tests/datos/test_simbolos.py`

**Interfaces:**
- `simbolos.cargar() -> dict[str, Simbolo]` valida el archivo: nombres únicos, `clase` y `horario` conocidos, cada `fuentes[...]` es una fuente registrada.
- `python -m datos --help` lista las órdenes; cada una sin implementar sale con un mensaje claro.

- [ ] **Step 1:** escribir `test_registro_valido`, `test_simbolo_duplicado_aborta` y `test_fuente_desconocida_aborta` → FAIL.
- [ ] **Step 2:** implementar. `simbolos.json` con el universo de la sección 7.2 del spec (lo que no esté verificado aún, con `"verificado": false`).
- [ ] **Step 3:** `pytest -q` → PASS. Confirmar que `motor/` no importa `datos/` (`test_motor_no_importa_datos`).
- [ ] **Step 4:** commit `feat(datos): paquete datos, CLI y registro de símbolos`.

### Task 2: Almacén Parquet con manifiestos

**Files:**
- Create: `datos/almacen.py`, `datos/esquemas.py`, `tests/datos/test_almacen.py`

**Interfaces:**
- `ruta(tipo, fuente, simbolo, fecha) -> Path` según el árbol del spec 7.5.
- `escribir(tabla: pa.Table, tipo, fuente, simbolo, fecha) -> Manifiesto`: valida el esquema, ordena por `ts`, escribe con zstd en `*.tmp`, renombra y luego escribe el manifiesto (filas, `ts` mínimo y máximo, sha256, versión de esquema, fuente, hora de descarga).
- `estado(ruta) -> "ok" | "vacio" | "incompleto"`: sin manifiesto o con hash distinto, `incompleto`.
- `marcar_vacio(...)`: manifiesto sin archivo para días sin mercado (no se vuelven a pedir).

- [ ] **Step 1:** tests `test_escribe_y_lee`, `test_esquema_incorrecto_aborta`, `test_escritura_interrumpida_se_rehace` (simula un fallo entre renombrar y manifiesto), `test_dia_vacio_no_se_repite` → FAIL.
- [ ] **Step 2:** implementar. Los `ts` se exigen con zona UTC; un `ts` sin zona aborta.
- [ ] **Step 3:** PASS. Commit `feat(datos): almacén Parquet con escritura atómica y manifiestos`.

### Task 3: Catálogo en Postgres

**Files:**
- Create: `datos/migrations/001_catalogo.sql` (`schema_migrations`, `fuentes`, `simbolos`, `particiones`, `corridas`, `problemas`, con índices por `(fuente, simbolo, fecha)`), `datos/catalogo.py`, `tests/datos/test_catalogo.py`

**Interfaces:**
- `python -m datos catalogo migrar`: reutiliza `scripts.localdb_migrate.apply_pending` con el directorio de `datos/migrations`.
- `registrar(manifiesto)`, `pendientes(fuente, simbolo, desde, hasta) -> list[date]`, `abrir_corrida()/cerrar_corrida()`, `anotar_problema(...)`.
- `python -m datos catalogo reconstruir`: vacía `particiones` y la rehace leyendo `_manifiestos/`.

- [ ] **Step 1:** tests con la lógica pura (`pendientes` sobre una lista en memoria) y, con `DATOS_PG_DSN_TEST`, `test_reconstruir_desde_manifiestos` → FAIL.
- [ ] **Step 2:** implementar. El catálogo nunca es la única copia de nada: todo lo que guarda se puede deducir de los manifiestos, salvo `corridas`.
- [ ] **Step 3:** PASS. Commit `feat(datos): catálogo Postgres reconstruible desde manifiestos`.

### Task 4: Fuente Dukascopy

**Files:**
- Create: `datos/fuentes/__init__.py` (interfaz `Fuente`: `nombre`, `listar_disponible`, `bajar_dia`, `esquema`), `datos/fuentes/dukascopy.py`, `tests/datos/test_dukascopy.py`, `tests/datos/fixtures/` (un `.bi5` sintético que arma el propio test con `lzma` + `struct`)

**Interfaces:**
- `parsear_bi5(bytes, hora_utc, decimales) -> pa.Table` (ms desde el inicio de la hora, ask, bid, ask_vol, bid_vol → esquema de ticks).
- `bajar_dia(simbolo, fecha)`: 24 horas, con un tope de peticiones en paralelo (`DUKAS_MAX_PARALELO`, 4 por defecto), reintentos con espera creciente y un cliente HTTP inyectable (en los tests, uno falso).
- `python -m datos bajar-dukascopy [--simbolo X] [--desde AAAA-MM-DD] [--hasta ...] [--max-dias N]`: baja lo que `catalogo.pendientes` diga y se puede cortar y retomar.

- [ ] **Step 1:** tests `test_parsea_bi5`, `test_decimales_por_instrumento` (XAUUSD, USDJPY, EURUSD, un índice), `test_hora_vacia_es_valida`, `test_reintenta_y_falla_limpio`, `test_dukascopy_reanuda` → FAIL.
- [ ] **Step 2:** implementar. Ojo: en las URLs de Dukascopy el mes va **de 00 a 11**. Hay un test para eso.
- [ ] **Step 3:** PASS. Probar a mano 1 día real de XAUUSD en el PC y comparar visualmente con un gráfico.
- [ ] **Step 4:** commit `feat(datos): descargador de ticks de Dukascopy`.

### Task 5: Velas M1 y consulta DuckDB

**Files:**
- Create: `datos/velas.py`, `datos/consulta.py`, `tests/datos/test_velas.py`, `tests/datos/test_consulta.py`

**Interfaces:**
- `python -m datos velas [--fuente] [--simbolo]`: calcula M1 desde los ticks (OHLC en bid, `ask_close`, spread mínimo/medio/máximo, número de ticks), un archivo por mes. Solo rehace los meses cuyos ticks cambiaron (compara hashes de manifiestos).
- `consulta.conectar() -> duckdb.Connection` con vistas `ticks`, `m1`, `calendario`, `fred`, `cot`, `etf` sobre `DATA_DIR` (`read_parquet(..., hive_partitioning=true)`).
- `consulta.velas(con, simbolo, tf, desde, hasta, fuente, corte_diario="ny"|"utc")` para M5…D1 al vuelo.
- `consulta.cargar(simbolo, desde, hasta, tf="m1", fuente="dukascopy") -> pandas.DataFrame`.

- [ ] **Step 1:** tests con ticks sintéticos: `test_m1_ohlc_correcto`, `test_minuto_sin_ticks_no_existe`, `test_h1_desde_m1`, `test_d1_corte_nueva_york` (incluye un cambio de horario) → FAIL.
- [ ] **Step 2:** implementar. PASS.
- [ ] **Step 3:** commit `feat(datos): velas M1 y vistas DuckDB para las fases 3 y 4`.
- [ ] **Step 4:** PR del Bloque A. Antes, revisión con `/code-review`.

---

## Bloque B — Captura Vantage y bucket. Rama `datos/captura-vantage`

### Task 6: Hora del servidor de Vantage → UTC

**Files:**
- Create: `datos/tiempo.py`, `tests/datos/test_tiempo.py`

**Interfaces:**
- `servidor_a_utc(ts_servidor_ms) -> datetime UTC` con la regla de Vantage (GMT+2 en invierno y GMT+3 en verano). Qué fechas de cambio usa (las de EE. UU. o las de Europa) se **confirma** en el Step 3 y queda fijo en una constante con su comentario.
- `offset_vivo(tick_time, ahora_utc) -> horas`: igual que hace el `news_exporter`, para comprobar la regla en cada captura. Si no coincide, la captura se detiene con un error claro.

- [ ] **Step 1:** tests `test_utc_invierno`, `test_utc_verano` y `test_utc_semanas_cambio_horario` (marzo y noviembre, las semanas en que EE. UU. y Europa difieren) → FAIL.
- [ ] **Step 2:** implementar. PASS.
- [ ] **Step 3:** cuando existan datos de Vantage y de Dukascopy del mismo periodo (Task 11), confirmar la regla con el desfase 0 en esas semanas. Anotarlo en la memoria.
- [ ] **Step 4:** commit `feat(datos): conversión de hora del servidor MT5 a UTC`.

### Task 7: Fuente Vantage MT5 (`capturar-mt5`)

**Files:**
- Create: `datos/fuentes/mt5.py`, `tests/datos/test_mt5_captura.py` (con un módulo `MetaTrader5` falso)
- Modify: `scripts/provision.py` (nuevo subcomando `clon-datos --login ... --server ... --password ... --nombre mt5_datos`: clona y siembra el perfil IPC como `alta`, pero **sin** Supabase ni `.env` en `instances/`, y escribe `datos.env` con `MT5_PATH`, `MT5_LOGIN` y `DATA_DIR`)

**Interfaces:**
- `python -m datos capturar-mt5 --env datos.env [--fuente vantage_raw] [--relleno-max-dias N]`:
  1. `registry.validate_mt5_target(env)` y la ruta del clon bajo `MT5_CLONES_DIR`; si no, aborta.
  2. `mt5.initialize(path=..., login=...)`, comprueba la regla de hora (Task 6).
  3. Por cada símbolo del registro que tenga nombre en Vantage: `copy_ticks_range` desde el último `ts` guardado menos 1 h (o, en el relleno inicial, día a día hacia atrás hasta que el servidor no dé nada, con tope de días por corrida) y `copy_rates_range` M1. Quita duplicados por `(ts_servidor, bid, ask, flags)`.
  4. Escribe por día con `almacen.escribir`. Solo se cierra un día cuando ya pasó en UTC; el día en curso se reescribe en cada corrida.
  5. `mt5.shutdown()` siempre (en `finally`).
- El proceso arranca con prioridad baja (`BELOW_NORMAL`) en Windows.

- [ ] **Step 1:** tests `test_captura_rechaza_produccion`, `test_captura_desconecta_al_terminar` (también si falla a la mitad), `test_captura_solape_sin_duplicados`, `test_relleno_para_cuando_no_hay_mas`, `test_dia_en_curso_se_reescribe` → FAIL.
- [ ] **Step 2:** implementar. PASS.
- [ ] **Step 3:** test para `provision clon-datos`: no llama a Supabase y se niega a usar `C:\MT5_instances`.
- [ ] **Step 4:** commit `feat(datos): captura de ticks y M1 de Vantage desde un clon propio`.

### Task 8: Bucket R2 (`subir` / `traer`)

**Files:**
- Create: `datos/nube.py`, `tests/datos/test_nube.py` (con un cliente S3 falso en memoria)

**Interfaces:**
- `python -m datos subir [--prefijo ...]`: sube los archivos cuyo manifiesto no está en el bucket o tiene otro hash, y el manifiesto al final.
- `python -m datos traer [--prefijo ...]`: baja lo que falta o cambió, verifica el hash, lo deja en `DATA_DIR` y lo registra en el catálogo.
- Endpoint R2 y claves desde `config` (`R2_ENDPOINT`, `R2_BUCKET`, `R2_BUCKET_VPS`, `R2_KEY_ID`, `R2_SECRET`). El VPS solo conoce `R2_BUCKET_VPS`; el PC trae de ahí y respalda todo en `R2_BUCKET`.

- [ ] **Step 1:** tests `test_subir_solo_lo_nuevo`, `test_traer_verifica_hash`, `test_manifiesto_al_final` y `test_claves_no_aparecen_en_logs` → FAIL.
- [ ] **Step 2:** implementar. PASS.
- [ ] **Step 3:** commit `feat(datos): sincronización con Cloudflare R2 por manifiestos`.
- [ ] **Step 4:** PR del Bloque B con una explicación para Daniel: qué se instala en el VPS (un clon, una carpeta y una tarea) y qué no se toca.

---

## Bloque C — Contexto. Rama `datos/contexto`

### Task 9: Calendario económico

**Files:**
- Create: `datos/fuentes/calendario.py`, `datos/fuentes/forexfactory.py`, `tests/datos/test_calendario.py`, `docs/fase2-eleccion-api-calendario.md`

- [ ] **Step 1 (prueba antes de pagar):** con la cuota gratuita o de prueba de 2 o 3 APIs (Financial Modeling Prep, EODHD, Trading Economics), bajar el mismo mes de 2015 y de 2024 y comparar: profundidad, hora exacta (contra una NFP y un FOMC conocidos), dato real, previsión y anterior, impacto, divisas cubiertas, límites y precio. Escribir la comparación en `docs/fase2-eleccion-api-calendario.md` y **Oscar elige** (y aprueba el pago).
- [ ] **Step 2:** esquema común de calendario: `ts` (UTC), `ts_original` + `zona`, `pais`, `divisa`, `evento`, `impacto` (normalizado a bajo/medio/alto), `real`, `prevision`, `anterior`, `fuente`, `id_fuente`.
- [ ] **Step 3:** tests con respuestas de muestra (`test_calendario_normaliza_impacto`, `test_calendario_hora_utc`, `test_ff_foto_guarda_crudo`) → FAIL. Implementar la fuente elegida (`python -m datos contexto calendario`) y la foto diaria de FF (`python -m datos contexto ff-foto`). Esta última corre en el VPS y es **una sola descarga al día**, independiente del `news_exporter`.
- [ ] **Step 4:** PASS. Commit `feat(datos): calendario económico histórico y foto diaria de Forex Factory`.

### Task 10: FRED, COT y GLD

**Files:**
- Create: `datos/fuentes/fred.py`, `datos/fuentes/cot.py`, `datos/fuentes/etf.py`, `tests/datos/test_contexto.py`

- [ ] **Step 1:** tests con muestras: `test_fred_vintages_sin_futuro` (con ALFRED: el valor que se veía en cada fecha), `test_cot_fecha_informe_y_publicacion`, `test_gld_normaliza` → FAIL.
- [ ] **Step 2:** implementar `python -m datos contexto fred|cot|gld`. Series FRED iniciales del spec 7.4 en una lista en `datos/simbolos.json` (sección `fred`). COT: archivos anuales de la CFTC, informes Disaggregated y Legacy, para oro, plata y futuros de divisas. GLD: CSV histórico de SPDR (si cambia de formato, error claro y problema en el catálogo).
- [ ] **Step 3:** `consulta.cargar_contexto(nombre, desde, hasta, segun_publicacion=True)`.
- [ ] **Step 4:** PASS. Commit `feat(datos): FRED con fechas de publicación, COT y tenencias GLD`.
- [ ] **Step 5:** PR del Bloque C.

---

## Bloque D — Calidad, orquestación y tareas. Rama `datos/calidad-y-tareas`

### Task 11: Verificación de calidad e informe

**Files:**
- Create: `datos/calidad.py`, `datos/horarios.py` (plantillas `fx`, `metal`, `indice_us`, `indice_eu`, `energia`, con feriados fijos conocidos), `datos/informe.py`, `tests/datos/test_calidad.py`

- [ ] **Step 1:** tests con datos sintéticos: `test_detecta_hueco_en_horario_abierto`, `test_fin_de_semana_no_es_hueco`, `test_bid_mayor_que_ask`, `test_salto_que_revierte`, `test_dia_con_pocos_ticks`, `test_cruce_desfase_cero` y `test_cruce_detecta_desfase_1h` → FAIL.
- [ ] **Step 2:** implementar `python -m datos verificar [--desde]`: escribe en `problemas` (sin duplicar los que ya existen) y genera `DATA_DIR/_informes/AAAA-MM-DD.html` con lo bajado, lo que falta, los problemas nuevos y el cruce Vantage contra Dukascopy (desfase, diferencia de precio y spread de cada uno por semana).
- [ ] **Step 3:** PASS. Commit `feat(datos): verificación de calidad, cruce entre fuentes e informe`.

### Task 12: Orden `actualizar` y tareas programadas

**Files:**
- Create: `datos/actualizar.py`, `scripts/instalar_tarea_datos_vps.ps1` (`SentinelN1Datos`, cada hora, usuario actual, `-DryRun`), `scripts/instalar_tarea_datos_pc.ps1` (`SentinelDatosPC`, diaria y "al iniciar sesión si se saltó"), `tests/datos/test_actualizar.py`

**Interfaces:**
- `python -m datos actualizar --maquina vps`: `capturar-mt5` → `contexto ff-foto` (una vez al día) → `subir`.
- `python -m datos actualizar --maquina pc`: `traer` → `bajar-dukascopy` (hasta ayer) → `contexto` (FRED, calendario, COT si toca, GLD) → `velas` → `verificar` → `subir`.
- Un candado (archivo de bloqueo) evita dos corridas a la vez. Un paso que falla se anota y los siguientes siguen, salvo que dependan de él.
- Aviso de disco: si el espacio libre baja del umbral (`DATOS_DISCO_MIN_GB`), no se baja nada nuevo y queda anotado.

- [ ] **Step 1:** tests `test_orden_de_pasos`, `test_paso_fallido_no_para_todo`, `test_candado_evita_doble_corrida`, `test_disco_lleno_no_baja` → FAIL.
- [ ] **Step 2:** implementar. Scripts PS1 con `-DryRun` que muestran la tarea sin registrarla. **No se registran** hasta que una persona lo pida.
- [ ] **Step 3:** PASS. Commit `feat(datos): orden actualizar y tareas programadas de datos`.
- [ ] **Step 4:** PR del Bloque D.

---

## Bloque E — Puesta en marcha y cierre de la Fase 2

### Task 13: Puesta en marcha

- [ ] **Step 1 (PC):** `pip install -r requirements-datos.txt`, `datos catalogo migrar`, y relleno inicial de Dukascopy **por tandas**: primero XAUUSD completo, después metales y mayores, después cruces, índices y energía. Revisar el informe al terminar cada tanda.
- [ ] **Step 2 (VPS, con Daniel):** foto de PIDs de producción (skill `lanzar-bot-nuevo`). Después, `provision clon-datos` con la demo RAW (y Standard si se aprobó), `datos.env` y una primera captura a mano (`capturar-mt5 --relleno-max-dias 30`, repetida hasta medir la profundidad). Comprobar que los PIDs de producción no cambiaron y que `restart_motor.ps1 -DryRun` sigue listando cero procesos de producción.
- [ ] **Step 3:** registrar `SentinelN1Datos` (VPS) y `SentinelDatosPC` (PC) cuando Oscar y Daniel lo pidan.
- [ ] **Step 4:** contexto completo (calendario elegido, FRED, COT, GLD) y `verificar`. Confirmar en el cruce el desfase 0 y anotar la regla de hora (Task 6, Step 3).
- [ ] **Step 5:** prueba de restauración: en una carpeta vacía, `datos traer` + `catalogo reconstruir` y comparar conteos con el original.
- [ ] **Step 6:** una semana corriendo sola. Repasar los criterios de la sección 8 del spec uno por uno.
- [ ] **Step 7:** docs y memoria: `docs/datos-de-mercado.md` (guía sencilla: qué hay, dónde y cómo se consulta), skill nueva `.claude/skills/datos-mercado/SKILL.md` (añadir un símbolo o una fuente, relanzar un relleno, leer el informe), `CLAUDE.md` (sección "Cómo está armado": `datos/` y tareas nuevas), `docs/convivencia-y-reemplazo.md` (clon `mt5_datos` y `SentinelN1Datos`) y `docs/memory/` (profundidad de Vantage, regla de hora confirmada, API elegida, tamaños reales).
- [ ] **Step 8:** PR final y tag `fase2-datos-AAAA-MM-DD`.
