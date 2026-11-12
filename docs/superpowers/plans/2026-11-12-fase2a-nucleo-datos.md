# Fase 2A — Núcleo de datos de mercado: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paquete `datos/` que baja ticks bid/ask de todo el universo de Dukascopy a Parquet en la laptop de Oscar, los cataloga en Postgres, calcula M1, verifica la calidad y ofrece una consulta sin ver el futuro y con reserva de 12 meses.

**Architecture:** Piezas pequeñas con una sola responsabilidad: la fuente baja y convierte; el almacén es lo único que escribe archivos (Parquet + manifiesto, escritura segura); el catálogo Postgres se alimenta de los manifiestos y se puede reconstruir desde ellos; la descarga orquesta; la consulta lee los Parquet con DuckDB sin depender de Postgres.

**Tech Stack:** Python ≥ 3.12 (aquí 3.13; en la laptop, la versión instalada), pyarrow, duckdb, pandas, requests, psycopg2 (ya en `requirements.txt`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-fase2a-nucleo-datos-design.md` (mapa de la fase: `docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md`).

## Global Constraints

- Rama `datos/base-almacen` desde `main` (con el PR #3 ya mergeado). Un PR al final. Commits en español `tipo(datos): ...`, identidad `git -c user.name="Oscar" -c user.email="oscarhalabi@proton.me"`, terminando con la línea `Co-Authored-By` del entorno.
- `motor/` nunca importa `datos/`. `datos/` solo reutiliza `motor.registry.read_env_file` y `scripts.localdb_migrate.apply_pending`.
- Todos los `ts` son timestamp µs con zona **UTC**. Un `ts` sin zona se rechaza al escribir.
- Rutas bajo `DATA_DIR` siempre **relativas** en manifiestos y catálogo.
- Tests sin red, sin Dukascopy real y sin Postgres real (los de base real se saltan si falta `DATOS_PG_DSN_TEST`). `python -m pytest -q` pasa entero.
- No se toca Supabase, producción ni `motor/`.
- Comentarios, mensajes y docs en español sencillo. DSN y claves nunca en logs ni informes.
- Valores fijos de la spec: 4 peticiones en paralelo (`DUKAS_MAX_PARALELO`), parada hacia atrás tras **8 semanas** sin datos, `DATOS_DISCO_MIN_GB` = **50**, conteo sospechoso < **20 %** de la mediana, saltos > **8×** la volatilidad, reserva = **hoy − 12 meses** salvo `DATOS_CORTE_RESERVA`, D1 corta a las **17:00 America/New_York**.

## Review Focus

1. **Archivo de Dukascopy truncado o corrupto** (LZMA inválido, tamaño no múltiplo de 20): esa hora cuenta como fallo y el día queda `error`, nunca `ok` a medias. Test `test_bi5_corrupto_es_fallo` (Task 4).
2. **Día con cambio de horario o de fin de semana**: un día UTC de domingo con solo 2 h de mercado es válido y no se marca como hueco. Test `test_domingo_parcial_no_es_hueco` (Task 10).
3. **Registro editado a mano con un error** (clase inventada, `decimales` en texto): aborta antes de bajar nada, con el nombre del símbolo en el mensaje. Test `test_registro_invalido_aborta` (Task 2).
4. **Dos descargas a la vez** (Oscar lanza una a mano mientras corre otra): la segunda se niega con un mensaje claro en vez de pisar archivos. Test `test_candado_evita_doble_descarga` (Task 7).
5. **`DATA_DIR` movido a otro disco**: tras cambiar la variable, la consulta y el estado siguen funcionando sin rehacer nada. Test `test_mover_data_dir_sigue_funcionando` (Task 3).

---

## Estructura de archivos

```
datos/
  __init__.py
  __main__.py            CLI (subcomandos)
  config.py              Config y cargar_config
  simbolos.py            Simbolo, cargar, guardar, fusionar_descubiertos
  simbolos.json          registro del universo
  horarios.py            plantillas de horario y abierto()
  almacen.py             esquemas, rutas, escribir, manifiestos, estado
  fuentes/__init__.py
  fuentes/base.py        Fuente, ResultadoDia, InstrumentoFuente, ClienteHttp
  fuentes/dukascopy.py   url_hora, parsear_bi5, Dukascopy
  catalogo.py            Catalogo (Postgres) + pendientes()
  migrations/001_catalogo.sql
  descarga.py            bajar()
  velas.py               construir_m1()
  calidad.py             verificar() + informe
  consulta.py            conectar, cargar, ticks, cargar_varios
requirements-datos.txt
tests/datos/             un test_*.py por pieza (+ ayudas.py con fábricas de datos sintéticos)
docs/datos-de-mercado.md
.claude/skills/datos-mercado/SKILL.md
```

---

### Task 1: Paquete, configuración y CLI base

**Files:**
- Create: `datos/__init__.py`, `datos/config.py`, `datos/__main__.py`, `datos/fuentes/__init__.py`, `requirements-datos.txt`, `tests/datos/__init__.py`, `tests/datos/test_config.py`, `tests/datos/ayudas.py`
- Modify: `.gitignore` (añadir `sentinel_datos/` por si alguien pone `DATA_DIR` dentro del repo)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Config(data_dir: Path, pg_dsn: str, disco_min_gb: float, corte_reserva: date | None)`
  - `cargar_config(entorno: Mapping[str, str] | None = None) -> Config`: une `.env` de la raíz del repo (si existe, vía `read_env_file`) con `os.environ` (gana `os.environ`); `entorno` explícito reemplaza ambos (para tests). Variables: `DATA_DIR` (obligatoria), `DATOS_PG_DSN` (opcional, ""), `DATOS_DISCO_MIN_GB` (50), `DATOS_CORTE_RESERVA` (AAAA-MM-DD u omitida).
  - `class ConfigInvalida(SystemExit)` con mensaje en español.
  - `python -m datos --help` lista: `simbolos`, `bajar`, `velas`, `verificar`, `catalogo`. Cada uno, mientras no exista, sale con código 2 y "Orden aún no disponible".
  - `tests/datos/ayudas.py`: `ticks_sinteticos(inicio: datetime, n: int, precio=2000.0, paso_ms=500) -> pa.Table` con el esquema de ticks (se completa en la Task 3).

- [ ] **Step 1: Tests** en `tests/datos/test_config.py`:
  - `test_falta_data_dir_aborta`: `cargar_config({})` lanza `ConfigInvalida` con "DATA_DIR" en el mensaje.
  - `test_data_dir_inexistente_aborta`: `cargar_config({"DATA_DIR": str(tmp_path / "no_existe")})` → `ConfigInvalida` que nombra la ruta.
  - `test_valores_por_defecto`: `cargar_config({"DATA_DIR": str(tmp_path)})` → `disco_min_gb == 50`, `corte_reserva is None`, `pg_dsn == ""`.
  - `test_corte_reserva_se_parsea`: `"DATOS_CORTE_RESERVA": "2025-11-01"` → `date(2025, 11, 1)`; `"2025-13-01"` → `ConfigInvalida`.
  - `test_motor_no_importa_datos`: recorre `motor/*.py` con `ast` y afirma que ningún import empieza por `datos`.
  - `test_cli_lista_ordenes`: `subprocess.run([sys.executable, "-m", "datos", "--help"])` contiene las 5 órdenes.
- [ ] **Step 2:** `python -m pytest tests/datos/test_config.py -v` → FAIL (no existe `datos`).
- [ ] **Step 3:** Implementar. `requirements-datos.txt`: `pyarrow`, `duckdb`, `pandas`, `requests`. `pip install -r requirements-datos.txt`.
- [ ] **Step 4:** `python -m pytest tests/datos/test_config.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(datos): paquete datos, configuración y CLI base`.

### Task 2: Registro de símbolos

**Files:**
- Create: `datos/simbolos.py`, `datos/simbolos.json`, `datos/horarios.py` (solo la constante), `tests/datos/test_simbolos.py`

**Interfaces:**
- Produces:
  - `horarios.HORARIOS = frozenset({"fx", "metal", "indice_us", "indice_eu", "indice_asia", "accion_us", "accion_eu", "energia", "materia_prima", "cripto_24x7"})`
  - `simbolos.CLASES = frozenset({"metal", "fx_mayor", "fx_cruce", "fx_exotico", "indice", "energia", "materia_prima", "accion", "etf", "cripto"})`
  - `@dataclass(frozen=True) Simbolo(simbolo: str, clase: str, prioridad: int, fuentes: dict[str, str], decimales: int, horario: str, divisa: str, activo: bool = True, ajustado_a_mano: tuple[str, ...] = ())`
  - `RUTA_REGISTRO: Path` (= `datos/simbolos.json`)
  - `class RegistroInvalido(SystemExit)`
  - `cargar(ruta: Path = RUTA_REGISTRO) -> dict[str, Simbolo]` (valida todo; el mensaje nombra el símbolo y el campo)
  - `guardar(simbolos: dict[str, Simbolo], ruta: Path = RUTA_REGISTRO) -> None` (JSON ordenado por prioridad y nombre, `indent=2`, escritura segura con `.tmp` + rename)
  - `ordenar_para_descarga(simbolos) -> list[Simbolo]`: activos, prioridad 1 antes que 2, después por nombre.

- [ ] **Step 1: Tests:**
  - `test_registro_valido`: un JSON mínimo con XAUUSD y NVDA.US carga 2 símbolos con sus campos.
  - `test_registro_invalido_aborta` (parametrizado): clase `"metalico"`, horario desconocido, `decimales: "3"`, `decimales: 9`, prioridad 3, nombre duplicado, `fuentes: {}` → `RegistroInvalido`, y el mensaje contiene el nombre del símbolo.
  - `test_guardar_y_cargar_es_identico`.
  - `test_orden_de_descarga`: inactivos fuera; prioridad 1 primero.
  - `test_registro_del_repo_es_valido`: `cargar()` del `simbolos.json` real no lanza.
- [ ] **Step 2:** `python -m pytest tests/datos/test_simbolos.py -v` → FAIL.
- [ ] **Step 3:** Implementar y sembrar `datos/simbolos.json` con la **prioridad 1** de la spec. Códigos y decimales de Dukascopy según su convención conocida (`XAUUSD`/3, `XAGUSD`/3, divisas/5, pares con JPY/3, `USA500IDXUSD`, `USATECHIDXUSD`, `USA30IDXUSD`, `DEUIDXEUR`, `GBRIDXGBP`, `JPNIDXJPY`, `LIGHTCMDUSD`, `BRENTCMDUSD`, `BTCUSD`, `ETHUSD`, acciones `NVDAUSUSD`, `AAPLUSUSD`...). Los códigos y decimales que no se conozcan con certeza se dejan con el mejor valor conocido: la Task 5 (descubrir) y la comprobación de escala de la Task 4 los corrigen en la laptop.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(datos): registro de símbolos con validación`.

### Task 3: Almacén Parquet con manifiestos

**Files:**
- Create: `datos/almacen.py`, `tests/datos/test_almacen.py`
- Modify: `tests/datos/ayudas.py` (completar `ticks_sinteticos` y añadir `m1_sintetica`)

**Interfaces:**
- Produces:
  - `ESQUEMA_TICKS` (pa.Schema): `ts` timestamp("us", tz="UTC"), `bid` float64, `ask` float64, `bid_vol` float32, `ask_vol` float32.
  - `ESQUEMA_M1`: `ts`, `open`, `high`, `low`, `close`, `ask_close` (float64), `spread_min`, `spread_medio`, `spread_max` (float64), `ticks` (int32), `bid_vol`, `ask_vol` (float64).
  - `VERSION_ESQUEMA = {"ticks": 1, "m1": 1}`
  - `ruta_relativa(tipo: Literal["ticks","m1"], fuente: str, simbolo: str, periodo: date) -> PurePosixPath`: ticks → `ticks/fuente=F/simbolo=S/anio=AAAA/AAAA-MM-DD.parquet`; m1 → `m1/fuente=F/simbolo=S/anio=AAAA/AAAA-MM.parquet`.
  - `@dataclass Manifiesto(ruta: str, tipo: str, fuente: str, simbolo: str, periodo: str, estado: Literal["ok","vacio"], filas: int, ts_min: str | None, ts_max: str | None, sha256: str | None, esquema_version: int, descargado_en: str, commit: str, hashes_anteriores: list[str], origen: dict[str, str])` con `to_json()` / `from_json()`.
  - `class EsquemaInvalido(ValueError)`
  - `escribir(raiz: Path, tabla: pa.Table, tipo, fuente, simbolo, periodo, origen: dict[str, str] | None = None) -> Manifiesto`: valida el esquema (columnas comunes obligatorias, extras permitidas, `ts` con zona UTC), ordena por `ts`, escribe `*.tmp` (zstd), `os.replace`, y después el manifiesto en `_manifiestos/<ruta>.json` (también `.tmp` + replace). Si ya había un manifiesto `ok`, su sha256 pasa a `hashes_anteriores`. `commit` = `git rev-parse --short HEAD` o `"desconocido"`.
  - `marcar_vacio(raiz, tipo, fuente, simbolo, periodo) -> Manifiesto` (sin Parquet).
  - `estado(raiz, tipo, fuente, simbolo, periodo) -> Literal["ok","vacio","incompleto","falta"]`: `incompleto` si hay Parquet sin manifiesto, o si el manifiesto es `ok` y falta el Parquet o su sha256 no coincide (el hash se comprueba **solo** con `verificar_hash=True`; por defecto se comparan tamaño y existencia, para que listar miles de días sea rápido).
  - `leer_manifiesto(raiz, ruta_rel) -> Manifiesto | None`, `listar_manifiestos(raiz, tipo: str | None = None) -> Iterator[Manifiesto]`.
  - `espacio_libre_gb(raiz: Path) -> float` (vía `shutil.disk_usage`).
  - El manifiesto guarda también `bytes` (tamaño del Parquet) para la comprobación rápida.

- [ ] **Step 1: Tests:**
  - `test_escribe_y_lee`: escribe 100 ticks y `pq.read_table` devuelve 100 filas ordenadas; el manifiesto tiene `filas == 100`, `ts_min`/`ts_max` correctos y `estado == "ok"`.
  - `test_ts_sin_zona_se_rechaza` → `EsquemaInvalido`.
  - `test_falta_columna_se_rechaza` (sin `ask`) → `EsquemaInvalido`.
  - `test_escritura_interrumpida_se_rehace`: escribe el Parquet a mano sin manifiesto → `estado == "incompleto"`; tras `escribir(...)` → `"ok"`.
  - `test_hash_distinto_es_incompleto`: altera un byte del Parquet → `estado(..., verificar_hash=True) == "incompleto"`.
  - `test_dia_vacio`: `marcar_vacio` → `estado == "vacio"` y no hay Parquet.
  - `test_reescribir_guarda_hash_anterior`.
  - `test_mover_data_dir_sigue_funcionando`: escribe en `tmp/a`, mueve la carpeta a `tmp/b` → `estado(tmp/b, ...) == "ok"` y `leer_manifiesto` funciona.
  - `test_rutas_relativas`: ningún manifiesto contiene la raíz absoluta.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(datos): almacén Parquet con escritura segura y manifiestos`.

### Task 4: Fuente Dukascopy (un día de ticks)

**Files:**
- Create: `datos/fuentes/base.py`, `datos/fuentes/dukascopy.py`, `tests/datos/test_dukascopy.py`

**Interfaces:**
- Consumes: `Simbolo` (Task 2), `ESQUEMA_TICKS` (Task 3).
- Produces:
  - `base.ClienteHttp` (Protocol): `get(url: str, timeout: float) -> tuple[int, bytes]`; `base.ClienteRequests` lo implementa con `requests` y un User-Agent genérico (`"sentinel-datos/1.0"`).
  - `@dataclass base.ResultadoDia(estado: Literal["ok","vacio","error"], tabla: pa.Table | None, error: str | None = None)`.
  - `@dataclass base.InstrumentoFuente(codigo: str, nombre: str, clase: str, decimales: int, divisa: str, inicio: date | None)`.
  - `base.Fuente` (Protocol): `nombre: str`; `bajar_dia(sim: Simbolo, dia: date) -> ResultadoDia`; `listar_instrumentos() -> list[InstrumentoFuente]`.
  - `dukascopy.url_hora(codigo: str, momento: datetime) -> str` = `https://datafeed.dukascopy.com/datafeed/{codigo}/{AAAA}/{mes-1:02d}/{DD:02d}/{HH:02d}h_ticks.bi5`.
  - `dukascopy.parsear_bi5(datos: bytes, hora: datetime, decimales: int) -> pa.Table`: `b""` → tabla vacía; si no, `lzma.decompress`, registros `>IIIff` (ms, ask, bid, ask_vol, bid_vol), `ts = hora + ms`, precio = entero / 10**decimales. Lanza `ValueError` si el LZMA falla o el tamaño no es múltiplo de 20.
  - `dukascopy.escala_razonable(sim: Simbolo, tabla: pa.Table) -> str | None`: devuelve un mensaje si menos del 99 % de los ticks cumplen bid ≤ ask, o si la mediana de bid sale del rango de su clase: `fx_*` 0.0001–1000, `metal` 0.5–20000, `indice` 50–200000, `energia` 1–1000, `materia_prima` 0.01–100000, `accion`/`etf` 0.01–100000, `cripto` 0.0001–1000000.
  - `class Dukascopy(http: ClienteHttp | None = None, max_paralelo: int = 4, max_por_segundo: float = 20.0, reintentos: int = 4, espera_base: float = 1.0, dormir: Callable[[float], None] = time.sleep)` con `nombre = "dukascopy"`. `bajar_dia` pide las 24 horas (`ThreadPoolExecutor(max_paralelo)` + limitador de ritmo); 200 → parsear; 404 → hora vacía; 429/503 → espera `espera_base * 2**intento * 3`; otros errores o excepciones → espera `espera_base * 2**intento`; si una hora falla tras `reintentos`, devuelve `ResultadoDia("error", None, "<hora>: <motivo>")`. Si todas las horas son vacías → `"vacio"`. Si no → `"ok"` con la tabla concatenada y ordenada.

- [ ] **Step 1: Tests** (el `.bi5` lo arma el test: `lzma.compress(struct.pack(">IIIff", ...))`; `ClienteFalso` con un dict url → (status, cuerpo) y un contador de llamadas; `dormir` falso que no espera):
  - `test_parsea_bi5`: 3 ticks de XAUUSD (decimales 3, ask 2345123 → 2345.123) con `ts` correctos en UTC.
  - `test_url_mes_de_00_a_11`: enero → `/00/`, diciembre → `/11/`, hora 07 → `07h_ticks.bi5`.
  - `test_hora_vacia_es_valida`: cuerpo `b""` → 0 filas.
  - `test_bi5_corrupto_es_fallo`: bytes no LZMA en una hora → tras los reintentos, `estado == "error"` y el mensaje nombra la hora.
  - `test_dia_con_hora_fallida_queda_error`: 23 horas 200 y una 500 siempre → `"error"`.
  - `test_reintenta_y_recupera`: una hora devuelve 503 dos veces y luego 200 → `"ok"`.
  - `test_todo_vacio_es_vacio`: 24 respuestas 404 → `"vacio"`.
  - `test_escala_absurda_para_el_simbolo`: XAUUSD con `decimales=5` (oro a ~23) → mensaje; con 3 → `None`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(datos): fuente Dukascopy (ticks por día desde .bi5)`.

### Task 5: Descubrir instrumentos (`datos simbolos descubrir`)

**Files:**
- Modify: `datos/fuentes/dukascopy.py` (`listar_instrumentos`), `datos/simbolos.py`, `datos/__main__.py`
- Create: `tests/datos/test_descubrir.py`, `tests/datos/fixtures/dukascopy_instrumentos.json`

**Interfaces:**
- Consumes: `InstrumentoFuente`, `Simbolo`, `cargar`, `guardar`.
- Produces:
  - `Dukascopy.listar_instrumentos() -> list[InstrumentoFuente]`: pide la lista pública de instrumentos que usa la web de datos históricos de Dukascopy (constante `URL_INSTRUMENTOS`, con la cabecera `Referer` que exija) y la convierte con `parsear_instrumentos(cuerpo: bytes) -> list[InstrumentoFuente]`. El mapeo de grupos de Dukascopy a nuestras `clase`/`horario` queda en un dict `MAPEO_GRUPOS` en el mismo archivo.
  - `simbolos.nombre_canonico(inst: InstrumentoFuente) -> str`: divisas y metales tal cual (`EURUSD`), acciones `<TICKER>.<PAÍS>` (`NVDA.US`), índices con un dict fijo de los principales (`USA500IDXUSD` → `US500`...), el resto, el código de la fuente.
  - `simbolos.fusionar_descubiertos(actuales: dict[str, Simbolo], descubiertos: list[InstrumentoFuente], fuente: str) -> tuple[dict[str, Simbolo], list[str], list[str]]` → (registro nuevo, nombres nuevos, nombres desaparecidos). Nuevos con prioridad 2. Campos en `ajustado_a_mano` no se pisan. Desaparecidos → `activo=False` (nunca se borran).
  - CLI: `python -m datos simbolos descubrir [--aplicar]`. Sin `--aplicar` solo muestra el resumen (nuevos, desaparecidos, cambios); con `--aplicar` guarda.

- [ ] **Step 1: Averiguar el formato real** (necesita red a Dukascopy: hacerlo en la laptop o en una sesión que la tenga). Bajar la lista una vez, guardar una muestra recortada (unos 15 instrumentos de clases distintas) en `tests/datos/fixtures/dukascopy_instrumentos.json` y anotar en `URL_INSTRUMENTOS` la URL y las cabeceras que funcionaron.
- [ ] **Step 2: Tests:**
  - `test_parsea_instrumentos`: la muestra da `InstrumentoFuente` con código, clase y decimales correctos para un caso de divisa, metal, índice, acción y cripto.
  - `test_nombre_canonico`: `NVDAUSUSD` → `NVDA.US`; `USA500IDXUSD` → `US500`; `EURUSD` → `EURUSD`.
  - `test_descubrir_no_pisa_ajustes_manuales`: XAUUSD con `prioridad=1`, `ajustado_a_mano=("prioridad",)` sigue con prioridad 1 después de fusionar.
  - `test_desaparecido_queda_inactivo`.
  - `test_descubrir_sin_aplicar_no_escribe`.
- [ ] **Step 3:** FAIL → implementar → PASS.
- [ ] **Step 4:** Commit `feat(datos): descubrir el universo de instrumentos de Dukascopy`.

### Task 6: Catálogo Postgres

**Files:**
- Create: `datos/migrations/001_catalogo.sql`, `datos/catalogo.py`, `tests/datos/test_catalogo.py`

**Interfaces:**
- Consumes: `Manifiesto`, `listar_manifiestos` (Task 3), `apply_pending` (de `scripts.localdb_migrate`).
- Produces:
  - SQL: tablas `fuentes(nombre pk, tipo, de_pago bool, notas)`, `simbolos(simbolo pk, clase, prioridad, activo, datos jsonb, actualizado)`, `inicios(simbolo, fuente, inicio date, pk(simbolo,fuente))`, `particiones(tipo, fuente, simbolo, periodo text, ruta, estado, filas, ts_min, ts_max, sha256, intentos int, ultimo_error text, actualizado, pk(tipo,fuente,simbolo,periodo))`, `corridas(id bigserial, orden, inicio, fin, resultado, resumen jsonb)`, `problemas(id bigserial, tipo, simbolo, fuente, desde timestamptz, hasta timestamptz, gravedad, detalle jsonb, revisado bool default false, creado, unique(tipo, simbolo, fuente, desde))`, `accesos_reserva(id bigserial, momento, quien, simbolo, desde, hasta, unique(momento, quien, simbolo))`.
  - `dias_pendientes(hechos: Mapping[date, str], desde: date, hasta: date) -> list[date]` (función **pura**): días de `hasta` a `desde` (de hoy hacia atrás) cuyo estado no sea `ok` ni `vacio`.
  - `class Catalogo(dsn: str)` con: `migrar() -> list[str]`, `estados(tipo, fuente, simbolo) -> dict[date, str]`, `registrar(m: Manifiesto) -> None` (upsert), `registrar_error(tipo, fuente, simbolo, periodo: date, error: str) -> None` (estado `error`, `intentos += 1`), `fijar_inicio(simbolo, fuente, inicio: date)`, `abrir_corrida(orden: str) -> int`, `cerrar_corrida(id: int, resultado: str, resumen: dict)`, `anotar_problema(tipo, simbolo, fuente, desde, hasta, gravedad, detalle: dict) -> bool` (False si ya existía), `sincronizar_simbolos(simbolos: dict[str, Simbolo])`, `importar_accesos_reserva(raiz: Path) -> int`, `reconstruir(raiz: Path) -> int` (vacía `particiones` e `inicios` y los rehace desde los manifiestos; el inicio de cada símbolo = el `periodo` más antiguo con estado `ok`).
  - CLI: `python -m datos catalogo migrar|reconstruir|estado`. `estado` imprime, por símbolo, días `ok`/`vacio`/`error`/faltantes y su inicio.

- [ ] **Step 1: Tests:**
  - `test_dias_pendientes_de_hoy_hacia_atras`: con 3 días `ok` en medio, devuelve el resto en orden descendente.
  - `test_dias_pendientes_ignora_vacios`.
  - Con `DATOS_PG_DSN_TEST` (si no está, `skip`): `test_migrar_es_idempotente`, `test_registrar_upsert`, `test_registrar_error_cuenta_intentos`, `test_problema_no_se_duplica`, `test_catalogo_se_reconstruye_desde_manifiestos` (escribe 3 días con el almacén en un `tmp_path`, `reconstruir` → 3 filas `ok` e inicio = el día más antiguo).
- [ ] **Step 2:** FAIL → implementar → PASS (en la laptop, con `DATOS_PG_DSN_TEST` puesto, se ejecutan también los de base real).
- [ ] **Step 3:** Commit `feat(datos): catálogo Postgres reconstruible desde manifiestos`.

### Task 7: Orquestador de descarga (`datos bajar`)

**Files:**
- Create: `datos/descarga.py`, `tests/datos/test_descarga.py`
- Modify: `datos/__main__.py`

**Interfaces:**
- Consumes: `Config`, `ordenar_para_descarga`, `Fuente`/`ResultadoDia`, `escribir`/`marcar_vacio`/`espacio_libre_gb`, `escala_razonable`, `dias_pendientes` y `Catalogo` (opcional).
- Produces:
  - `@dataclass Resumen(dias_ok: int, dias_vacios: int, dias_error: int, simbolos_parados: dict[str, str], parado_por: str | None)`.
  - `bajar(cfg: Config, simbolos: list[Simbolo], fuente: Fuente, catalogo: Catalogo | None, desde: date | None = None, hasta: date | None = None, max_horas: float | None = None, hoy: date | None = None, reloj: Callable[[], float] = time.monotonic) -> Resumen`. Por símbolo: el estado de cada día sale del catálogo si lo hay, si no, del almacén (`estado`). Recorre de `hasta` (por defecto ayer UTC) hacia atrás hasta `desde` (por defecto sin límite). **Parada hacia atrás:** sin `desde` explícito, tras 56 días seguidos `vacio` se para ese símbolo y se fija su inicio (el día `ok` más antiguo). **Primer día `ok` de cada símbolo en la corrida:** `escala_razonable`; si devuelve mensaje, el símbolo se para (va a `simbolos_parados`) y no se escribe ese día. Antes de cada día, `espacio_libre_gb < cfg.disco_min_gb` → para todo con `parado_por="disco"`. `max_horas` agotado → `parado_por="tiempo"`. `KeyboardInterrupt` → termina el día en curso sin escribirlo, cierra la corrida y re-lanza.
  - **Candado:** `DATA_DIR/_registro/descarga.lock` creado con `os.open(O_CREAT | O_EXCL)` con el PID dentro; si existe y su PID sigue vivo → `SystemExit("Ya hay una descarga en marcha (PID n)")`; si el PID está muerto, se reemplaza.
  - CLI: `python -m datos bajar [--simbolo S ...] [--prioridad 1|2] [--desde] [--hasta] [--max-horas H]`. Código de salida 0 si `dias_error == 0` y no hay símbolos parados; si no, 1. Imprime el resumen.

- [ ] **Step 1: Tests** con una `FuenteFalsa` (dict (símbolo, día) → `ResultadoDia`) y sin catálogo:
  - `test_baja_de_hoy_hacia_atras`: el orden de llamadas a `bajar_dia` es descendente.
  - `test_reanuda_sin_duplicar`: dos corridas seguidas → la segunda no llama a la fuente para días `ok`/`vacio`.
  - `test_error_se_reintenta_en_la_siguiente`: un día `error` en la corrida 1 se vuelve a pedir en la 2.
  - `test_para_tras_8_semanas_vacias`: 56 días `vacio` seguidos → deja de pedir más atrás.
  - `test_escala_mala_para_el_simbolo`: el símbolo queda en `simbolos_parados` y no se escribió nada; los demás símbolos siguen.
  - `test_disco_bajo_para_todo` (con `espacio_libre_gb` parcheado).
  - `test_max_horas_para` (con `reloj` falso).
  - `test_candado_evita_doble_descarga`: un lock con el PID del propio test → `SystemExit` con "Ya hay una descarga".
  - `test_candado_huerfano_se_reemplaza`: lock con un PID inexistente → la descarga corre.
  - `test_un_simbolo_que_falla_no_para_los_demas`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): orden bajar (reanudable, por prioridad, con candado)`.

### Task 8: Velas M1 (`datos velas`)

**Files:**
- Create: `datos/velas.py`, `tests/datos/test_velas.py`
- Modify: `datos/__main__.py`

**Interfaces:**
- Consumes: `listar_manifiestos`, `escribir`, `ESQUEMA_M1`.
- Produces:
  - `velas_de_ticks(tabla_ticks: pa.Table) -> pa.Table` (función pura con DuckDB en memoria): agrupa por `date_trunc('minute', ts)`; `open`/`close` = primer y último bid por `ts`; `high`/`low` = max/min de bid; `ask_close` = último ask; `spread_*` = min/avg/max de `ask - bid`; `ticks` = conteo; volúmenes sumados. Solo minutos con ticks.
  - `construir_m1(cfg: Config, fuente: str = "dukascopy", simbolos: list[str] | None = None, desde: date | None = None) -> int` (meses escritos): para cada (símbolo, mes) con manifiestos de ticks `ok`, si `origen` de la M1 existente (dict ruta-de-ticks → sha256) no coincide con los manifiestos actuales, la rehace leyendo los Parquet del mes; si coincide, la salta.
  - CLI: `python -m datos velas [--simbolo ...] [--desde]`.

- [ ] **Step 1: Tests:**
  - `test_m1_ohlc_y_spread`: 6 ticks en dos minutos dan 2 velas con OHLC, `ask_close`, spreads y `ticks` exactos.
  - `test_minuto_sin_ticks_no_existe`: ticks a las 10:00 y a las 10:03 → 2 velas, no 4.
  - `test_m1_solo_rehace_meses_cambiados`: segunda llamada → 0 meses; al reescribir un día de ticks → 1 mes.
  - `test_m1_incluye_dias_de_dos_meses`: los días 31 y 1 caen en archivos distintos.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): velas M1 a partir de los ticks`.

### Task 9: Consulta sin ver el futuro y con reserva

**Files:**
- Create: `datos/consulta.py`, `tests/datos/test_consulta.py`

**Interfaces:**
- Consumes: `cargar_config`, rutas del almacén (lee los Parquet con `read_parquet(..., hive_partitioning=true)`).
- Produces:
  - `TEMPORALIDADES = {"m1": 1, "m5": 5, "m15": 15, "m30": 30, "h1": 60, "h4": 240, "d1": None}` (minutos).
  - `class ReservaBloqueada(PermissionError)`: no se lanza en lecturas normales (el rango se recorta en silencio); solo cuando el rango pedido está **entero** dentro de la reserva, para que nadie lo confunda con "no hay datos".
  - `corte_reserva(cfg: Config, hoy: date | None = None) -> datetime` (UTC) = `cfg.corte_reserva` o `hoy − 365 días`.
  - `conectar(cfg: Config | None = None) -> duckdb.DuckDBPyConnection` con vistas `ticks` y `m1` sobre `DATA_DIR` (si una vista no tiene archivos, se crea vacía con el esquema).
  - `cargar(simbolo: str, desde, hasta, tf: str = "m1", fuente: str = "dukascopy", incluir_reservado: bool = False, corte_diario: Literal["ny","utc"] = "ny", cfg: Config | None = None) -> pd.DataFrame` con columnas `ts`, `ts_cierre` + las de M1 (agregadas: `open` primero, `high` máx, `low` mín, `close` último, `ask_close` último, `spread_min` mín, `spread_medio` media ponderada por `ticks`, `spread_max` máx, `ticks` y volúmenes sumados). Intradía: `ts` = inicio del bloque en UTC alineado a múltiplos de `tf`; `ts_cierre = ts + tf`. **D1 corte NY:** la vela del día D va de las 17:00 America/New_York del día anterior a las 17:00 de D (convertidas a UTC con `zoneinfo`, así que en verano el corte es 21:00 UTC y en invierno 22:00 UTC); `ts`/`ts_cierre` son esos dos límites en UTC. **D1 corte UTC:** de 00:00 a 00:00.
  - `ticks(simbolo, desde, hasta, fuente="dukascopy", incluir_reservado=False, cfg=None) -> pd.DataFrame`.
  - `cargar_varios(simbolos: list[str], desde, hasta, tf: str, fuente="dukascopy", rellenar: Literal["ultimo_conocido"] | None = None, incluir_reservado=False, cfg=None) -> pd.DataFrame`: formato largo (`simbolo`, `ts_cierre`, columnas), con la rejilla = unión de los `ts_cierre`. Con `rellenar="ultimo_conocido"`, por símbolo, cada `ts_cierre` toma la última vela con `ts_cierre ≤` ese instante (`merge_asof(..., direction="backward")`) y añade `relleno: bool`.
  - Reserva: si `incluir_reservado=False`, `hasta = min(hasta, corte)`. Si `True`, se añade una línea JSON a `DATA_DIR/_registro/reserva.jsonl` con `momento`, `quien` (`getpass.getuser()` + nombre del script llamador), `simbolo`, `desde`, `hasta`.

- [ ] **Step 1: Tests** (M1 sintética escrita con el almacén en un `tmp_path`):
  - `test_ts_cierre_m1`: `ts_cierre - ts == 1 min` en todas las filas.
  - `test_h1_desde_m1`: 120 minutos dan 2 velas de H1 con OHLC correctos y `ts_cierre = ts + 1 h`.
  - `test_d1_corte_nueva_york_en_cambio_de_horario`: con M1 continua del 2024-03-08 al 2024-03-12 (EE. UU. cambió de horario el 10 de marzo), las velas D1 empiezan a las 22:00 UTC antes del cambio y a las 21:00 UTC después.
  - `test_reserva_bloquea_por_defecto`: con `DATOS_CORTE_RESERVA=2025-01-01`, `cargar(..., hasta=2025-06-01)` no devuelve nada ≥ 2025-01-01; un rango entero en 2025 → `ReservaBloqueada`.
  - `test_acceso_a_reserva_queda_registrado`: con `incluir_reservado=True` → 1 línea más en `reserva.jsonl` con el símbolo y el rango.
  - `test_cargar_varios_nunca_usa_dato_posterior`: dos símbolos con M1 desfasadas; con `rellenar="ultimo_conocido"`, para cada fila, el `ts_cierre` del dato usado es ≤ el `ts_cierre` de la fila.
  - `test_consulta_no_necesita_postgres`: sin `DATOS_PG_DSN` funciona.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): consulta multi-mercado sin ver el futuro y con reserva`.

### Task 10: Verificación de calidad e informe (`datos verificar`)

**Files:**
- Modify: `datos/horarios.py` (añadir `abierto`)
- Create: `datos/calidad.py`, `tests/datos/test_calidad.py`, `tests/datos/test_horarios.py`

**Interfaces:**
- Consumes: almacén, consulta (`ticks`, `cargar` con `incluir_reservado=True` **sin** registrar el acceso: la verificación no es investigación, así que usa una función interna `_leer_ticks_sin_registro`), `Catalogo.anotar_problema` (opcional), `Simbolo.horario`.
- Produces:
  - `horarios.abierto(horario: str, momento: datetime) -> bool` (`momento` en UTC; las reglas se evalúan en la zona indicada con `zoneinfo`):
    - `fx`, `metal`, `energia`, `materia_prima`: abierto desde el domingo 17:00 hasta el viernes 17:00 America/New_York. `metal`, `energia` y `materia_prima` cierran además cada día de 17:00 a 18:00 America/New_York.
    - `indice_us`, `accion_us`: lunes a viernes, 09:30–16:00 America/New_York (solo la sesión principal, para no dar falsas alarmas fuera de ella).
    - `indice_eu`, `accion_eu`: lunes a viernes, 09:00–17:30 Europe/Berlin.
    - `indice_asia`: lunes a viernes, 09:00–15:00 Asia/Tokyo.
    - `cripto_24x7`: siempre.
    - `FERIADOS_FIJOS = {(1, 1), (12, 25)}` (mes, día): cerrado para todos menos `cripto_24x7`.
  - `@dataclass Problema(tipo: Literal["coherencia","hueco","conteo","salto","posible_split"], simbolo, fuente, desde: datetime, hasta: datetime, gravedad: Literal["info","aviso","grave"], detalle: dict)`.
  - Funciones puras, una por chequeo, que reciben la tabla de ticks de un día y devuelven `list[Problema]`: `chequear_coherencia` (bid > ask, `ts` no creciente, duplicados exactos, precio ≤ 0 → grave), `chequear_huecos(tabla, sim, dia)` (horas abiertas según `abierto` sin ningún tick → aviso; 3 o más horas seguidas → grave), `chequear_saltos(tabla)` (|Δmid| > 8 × la desviación de Δmid en una ventana móvil de 200 ticks y que revierte más de la mitad en ≤ 5 ticks → aviso). `chequear_conteo(conteos: dict[date, int])` (día con < 20 % de la mediana → aviso). `chequear_split(cierres_diarios: pd.Series)` solo para `accion`/`etf` (cociente entre la apertura y el cierre anterior a ≤ 3 % de 2, 3, 4, 5 o 10, o de sus inversos → aviso `posible_split`).
  - `verificar(cfg, simbolos, catalogo | None, desde: date | None = None) -> list[Problema]` y `escribir_informe(cfg, problemas, resumen_descarga: dict | None) -> Path` (`_informes/AAAA-MM-DD.html`, HTML simple: bajado, faltante, tamaño por clase y problemas nuevos agrupados por gravedad).
  - CLI: `python -m datos verificar [--simbolo ...] [--desde]`. Sale con 1 si hay problemas `grave` nuevos.

- [ ] **Step 1: Tests:**
  - `test_domingo_parcial_no_es_hueco`: XAUUSD el domingo 2024-05-12, con ticks desde las 22:00 UTC → sin huecos.
  - `test_detecta_hueco_en_horario_abierto`: un martes sin ticks entre 10:00 y 14:00 UTC → un problema `grave` con ese rango.
  - `test_fin_de_semana_no_es_hueco`, `test_feriado_fijo_no_es_hueco` (25-dic).
  - `test_accion_us_fuera_de_sesion_no_es_hueco`.
  - `test_bid_mayor_que_ask_es_grave`, `test_duplicados_y_desorden`.
  - `test_salto_que_revierte_se_marca` y `test_tendencia_fuerte_no_es_salto`.
  - `test_dia_con_pocos_ticks`.
  - `test_posible_split_2_a_1` (cierre 100, apertura 50.2 → marcado) y `test_caida_normal_no_es_split` (100 → 93).
  - `test_verificar_no_duplica_problemas` (con un catálogo falso en memoria).
  - `test_informe_no_contiene_dsn`: con `pg_dsn="postgresql://u:secreto@h/db"`, el HTML no contiene `secreto`.
- [ ] **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Commit `feat(datos): verificación de calidad e informe`.

### Task 11: Puesta en marcha en la laptop, docs, memoria y skill

**Files:**
- Create: `docs/datos-de-mercado.md`, `.claude/skills/datos-mercado/SKILL.md`
- Modify: `CLAUDE.md` (sección "Cómo está armado": `datos/`), `docs/memory/fase2-datos-decisiones.md`, `docs/memory/MEMORY.md` si hace falta, `datos/simbolos.json` (con lo descubierto)

- [ ] **Step 1 (laptop):** en el clon de la laptop, `git switch datos/base-almacen`, `pip install -r requirements.txt -r requirements-datos.txt`, `.env` de la raíz con `DATA_DIR` (p. ej. `C:\sentinel_datos`), `DATOS_PG_DSN` y `DATOS_PG_DSN_TEST`. `python -m pytest -q` → todo pasa, incluidos los de base real.
- [ ] **Step 2 (laptop):** `python -m datos catalogo migrar` → "001_catalogo.sql".
- [ ] **Step 3 (laptop):** Task 5, Step 1 si no se hizo: fixture real de instrumentos. `python -m datos simbolos descubrir` → revisar el resumen → `--aplicar`. Marcar la prioridad 1 con `ajustado_a_mano`. Commit del registro.
- [ ] **Step 4 (laptop):** `python -m datos bajar --simbolo XAUUSD --desde 2024-05-13 --hasta 2024-05-13`. Comparar unos cuantos precios con un gráfico de referencia (TradingView XAUUSD M1 del mismo día y hora UTC). Anotar en la memoria filas, tamaño y segundos que tardó.
- [ ] **Step 5 (laptop):** `python -m datos bajar --prioridad 1 --max-horas 6`, repetido hasta terminar. Después `python -m datos velas` y `python -m datos verificar`. Revisar el informe y explicar o corregir los problemas `grave`.
- [ ] **Step 6 (laptop):** medir `datos.consulta.cargar("XAUUSD", <un año>, tf="h1")` (debe tardar pocos segundos) y probar `catalogo reconstruir` en `sentinel_datos_test`: los conteos coinciden.
- [ ] **Step 7:** `docs/datos-de-mercado.md`: qué hay, dónde está, cómo se baja, cómo se consulta (ejemplos de `cargar` y `cargar_varios`), qué es la reserva y por qué existe. Skill `datos-mercado` (frontmatter `name` y `description` que diga cuándo usarla): añadir un símbolo o una fuente, relanzar una descarga, leer el informe, mover `DATA_DIR`. `CLAUDE.md`: una línea para `datos/` en "Cómo está armado" y la skill en la lista. Memoria: velocidad real, tamaños reales, URL de instrumentos, umbrales ajustados, inicio real de la prioridad 1.
- [ ] **Step 8:** `/code-review`, corregir y abrir el PR `datos/base-almacen` → `main` con un resumen en lenguaje sencillo. La prioridad 2 puede seguir bajando después del merge.
