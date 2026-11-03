# Fase 1 — Base del fork: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar el fork limpio de la estrategia vieja, con un esqueleto multi-motor que arranca, se reporta y se controla desde la app web, aislado de producción (e2) en el mismo VPS y preparado para reemplazarla.

**Architecture:** El paquete `bot/` se renombra a `motor/` y pierde toda la lógica de exp-1. Un registro (`motor/engines.json`) declara los motores; Python y PowerShell leen de ahí el magic, la clase y los símbolos de cada uno. Un único punto de entrada (`python -m motor.run --mode console|lite|tui`) arranca cualquier motor. Los scripts de proceso (reinicio, vigilante) eligen qué matar o relanzar mediante una función pura de Python que filtra por la ruta del repo y por la carpeta de clones. La app web pasa de `m15 | m5` fijos a motores dinámicos.

**Tech Stack:** Python 3.14, MetaTrader5, supabase-py, psycopg2 (Postgres 18 local), pytest, PowerShell 5.1, Next.js 16 / React 19 / TypeScript (webapp).

**Spec:** `docs/superpowers/specs/2026-10-28-fase1-base-del-fork-design.md`

## Global Constraints

- **Nunca** modificar `C:\Users\Administrator\Documents\sentinel_trading` (producción), sus tareas `Sentinel*` ni las cuentas MT5 26329953/55/57.
- Procesos de producción vivos que nada de este repo puede matar: `bot.lite_e2` y `scripts.news_exporter` lanzados desde `...\sentinel_trading`, y los terminales bajo `C:\MT5_instances\`.
- Carpeta de clones del bot nuevo: `MT5_CLONES_DIR`, por defecto `C:\MT5_instances_n1`.
- `bot_id` nuevos distintos de `m15`, `m5`, `e2`. Motores iniciales: `n1` y `n2` (ambos nulos). Magics por defecto: `MAGIC_N1=300100` y `MAGIC_N2=300200`.
- Supabase compartido: **solo cambios aditivos**. Migraciones del bot nuevo en `migrations/n1_NNN_<nombre>.sql`. Antes de escribir una, se inspecciona el esquema real. Cada una se revisa con Daniel en el PR antes de aplicarla.
- Tareas programadas propias con prefijo `SentinelN1`.
- El bot nuevo no lanza `news_exporter`: lee el JSON de `NEWS_CALENDAR_PATH`, que ya escribe producción.
- Git:
  - Una rama por bloque y PR a `main` del fork. Nunca push directo a `main`.
  - Commits en español con formato `tipo(ámbito): ...` y la línea `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - El VPS no tiene identidad global de git. Cada commit lleva la de quien lo pide: `git -c user.name=... -c user.email=...`. Para Oscar: `Oscar <oscarhalabi@proton.me>`.
- Webapp (`C:\Users\Administrator\Documents\sentinel_trading-webapp`, remoto `lozadaitm/sentinel_trading-webapp`): rama y PR revisado por Daniel, probado en el preview de Vercel. Lo que ven hoy los usuarios `m15`/`m5` no cambia.
- Documentación y comentarios en español sencillo.

## Review Focus

1. **Reinicio con producción viva.** `restart_motor.ps1 -DryRun` debe listar 0 procesos de producción aunque haya `python` y `terminal64` de los dos lados. Lo fija el test `test_seleccion_ignora_produccion` (Task 6).
2. **Terminal muerto por un reinicio de producción.** El vigilante relanza la instancia y el motor no se cae en bucle si MT5 tarda en volver. Lo fijan `test_vigilante_relanza_faltantes` (Task 7) y `test_loop_sobrevive_mt5_caido` (Task 3).
3. **`.env` con dos motores que comparten magic, o con un `BOTS=` desconocido.** Se aborta antes de tocar MT5. Lo fijan `test_magic_duplicado_aborta` y `test_bot_desconocido_aborta` (Task 2).
4. **Usuario de producción en el dashboard.** Un usuario con solo `m15`+`m5` sigue viendo las mismas métricas: la base sale de la fila `m15`. Lo fija `metrics.check.ts` (Task 11).
5. **Sin fila en `engine_config`.** El motor arranca, reporta heartbeat y no opera. No debe quedarse colgado en el `if not cfg: continue` sin heartbeat. Lo fija `test_sin_config_reporta_heartbeat` (Task 3).

---

## Bloque A — Repo del bot. Rama `fase1/limpieza-multimotor`

### Task 1: Renombrar `bot/` → `motor/`, borrar exp-1, arnés de tests

**Files:**
- Move: `bot/` → `motor/` (`git mv`)
- Delete: `motor/strategy.py`, `motor/strategy_m5.py`, `motor/ledger.py`, `motor/budget.py`, `motor/indicators.py`, `motor/main_m5.py`, `motor/lite_m5.py`, `motor/tui_m5.py`, `scripts/audit_margin.py`, `scripts/audit_m5_signals.py`, `scripts/launch_all.ps1`, `scripts/CalendarExporter.mq5` (si existe), `tests/test_ledger.py`, `tests/test_m5_and_budget.py`, `bot_backtesting/`, `original_MQL5.txt`, `Grinder_Anterior.mq5`, `experimento_1_logs.csv`, `schema.sql`, `migrations.sql`, `docs/plan-m5-m15-coexistencia.md`
- Modify: `motor/config.py` (quitar `DB_*` legacy, `MAGIC_M15/M5`, `ALL_MAGICS`, `PEER_MAGICS`, `EXPECTED_MAGIC`, `LEDGER_PATH`, `TIMEFRAME_M5`, `TIMEFRAME_STRUCT`, `ADMIN_USER_UUID` como default de `USER_ID`: `USER_ID` pasa a ser obligatorio), `motor/main.py` (sin import de `SentinelEngine`; `engine_cls` obligatorio)
- Create: `requirements-dev.txt` (`pytest`), `pytest.ini` (`testpaths = tests`), `tests/__init__.py`, `tests/conftest.py`, `tests/test_imports.py`

**Interfaces:**
- Produces: paquete `motor` importable. `tests/conftest.py` fija en el entorno `USER_ID=00000000-0000-0000-0000-000000000000`, `BOT_ID=n1` y `MAGIC_NUMBER=300100` antes de importar `motor.config`.

- [ ] **Step 1:** `pip install -r requirements-dev.txt` y escribir `tests/test_imports.py::test_paquete_importa`. El test importa `motor.config`, `motor.broker`, `motor.db`, `motor.logger`, `motor.news`, `motor.notify` y `motor.main`. Afirma que `motor.config` no tiene `MAGIC_M15` ni `LEDGER_PATH`, y que no existe ningún módulo `motor.strategy*`.
- [ ] **Step 2:** `pytest tests/test_imports.py -v` → FAIL (`motor` no existe).
- [ ] **Step 3:** Hacer los movimientos y borrados y limpiar `config.py`/`main.py`. Buscar con `grep -rn "from .strategy\|budget\|ledger\|indicators" motor/` y eliminar cada referencia. `USER_ID` vacío ⇒ `SystemExit` con un mensaje en español dentro de `validate_identity`; ese paso lo hace Task 2, así que aquí basta con quitar el default.
- [ ] **Step 4:** `pytest -v` → PASS.
- [ ] **Step 5:** Commit `refactor(motor): renombrar bot/ a motor/ y retirar la estrategia exp-1`.

### Task 2: Registro de motores

**Files:**
- Create: `motor/engines.json`, `motor/registry.py`, `tests/test_registry.py`
- Modify: `motor/config.py` (`validate_identity` delega en el registro)

**Interfaces:**
- Produces:
  - `motor/engines.json`: `{"engines": [{"bot_id": "n1", "engine_class": "motor.engines.nulo:NullEngine", "magic_env": "MAGIC_N1", "magic_default": 300100, "symbols": null, "description": "Motor nulo (esqueleto Fase 1)"}, {"bot_id": "n2", ... "MAGIC_N2", 300200, ...}]}`. `symbols: null` significa usar el `SYMBOL` del `.env`.
  - `@dataclass(frozen=True) class EngineSpec: bot_id: str; engine_class: str; magic_env: str; magic_default: int; symbols: tuple[str, ...] | None; description: str`
  - `load_registry(path: str | None = None) -> dict[str, EngineSpec]`
  - `magic_for(spec: EngineSpec, env: Mapping[str, str]) -> int`
  - `parse_bots(value: str | None) -> list[str]`: separa por comas, pasa a minúsculas y quita vacíos. Si no queda nada ⇒ `SystemExit`.
  - `validate_instance(registry, bots: list[str], env: Mapping[str, str]) -> None`: lanza `SystemExit` con un mensaje en español si hay un bot desconocido, si dos bots de `BOTS` comparten magic o si algún magic de `BOTS` coincide con `MAGIC_M15`, `MAGIC_M5` o `MAGIC_E2` cuando estén presentes en el entorno.
  - `symbols_for(spec, default_symbol: str) -> tuple[str, ...]`
  - `load_engine_class(spec) -> type`: resuelve `"modulo:Clase"` con importlib.
- `config.validate_identity()` comprueba que `MAGIC_NUMBER == magic_for(registry[BOT_ID], os.environ)` y que `USER_ID` no está vacío.

- [ ] **Step 1:** Tests en `tests/test_registry.py`:
  - `test_carga_registro`: `n1` y `n2` existen y `magic_for(n1, {}) == 300100`.
  - `test_magic_desde_env`: con `{"MAGIC_N1": "555"}` da 555.
  - `test_bot_desconocido_aborta`: `validate_instance(reg, ["n1", "zz"], {})` lanza `SystemExit` y el mensaje contiene `"zz"`.
  - `test_magic_duplicado_aborta`: con `{"MAGIC_N2": "300100"}` lanza `SystemExit`.
  - `test_choque_con_produccion_aborta`: con `{"MAGIC_E2": "300100"}` lanza `SystemExit`.
  - `test_parse_bots`: `" N1, n2 ,"` da `["n1", "n2"]`.
  - `test_symbols_default`: `symbols_for(n1, "XAUUSD+") == ("XAUUSD+",)`.
- [ ] **Step 2:** `pytest tests/test_registry.py -v` → FAIL.
- [ ] **Step 3:** Implementar `registry.py` y `engines.json`, y reescribir `config.validate_identity`. Quitar del docstring la justificación Healer/Hedge.
- [ ] **Step 4:** `pytest -v` → PASS.
- [ ] **Step 5:** Commit `feat(motor): registro de motores (engines.json) como fuente única de identidad`.

### Task 3: Motor nulo, entrada única `motor.run`, multi-símbolo y bucle robusto

**Files:**
- Create: `motor/engines/__init__.py`, `motor/engines/nulo.py`, `motor/run.py`, `tests/test_main_loop.py`
- Modify: `motor/main.py`, `motor/lite.py`, `motor/tui.py`

**Interfaces:**
- Consumes: `load_registry`, `load_engine_class`, `symbols_for` (Task 2).
- Produces:
  - `class NullEngine`, con la interfaz que exige `main.setup`:
    - `__init__(self, broker, logger)`
    - `init_history_cursor() -> None`
    - `on_tick() -> None`: no envía órdenes; cada 60 s loguea `SYSTEM "Motor nulo vivo"`.
    - `close_all(reason: str) -> bool`: cierra las posiciones de su magic y devuelve `True` cuando no queda ninguna.
    - Atributos `is_active`, `close_only`, `cfg`, `b`, `user_email`, `initial_balance`, `last_flow_ticket`, `baseline_applied_at`, y `symbols: tuple[str, ...]`.
  - `motor/run.py`: `main(argv: list[str] | None = None) -> None` con `--mode {console,lite,tui}` (por defecto `console`). Resuelve la clase con `BOT_ID` desde el registro y llama a `main.run` / `lite.run_lite` / `tui.run_tui` con `engine_cls`.
  - `main.setup` llama a `mt5.symbol_select` para cada símbolo de `symbols_for(spec, config.SYMBOL)` y asigna `engine.symbols`.
  - Velas del dashboard: las publica solo el primer motor de `BOTS` (`config.CANDLES_PUBLISHER = parse_bots(os.environ.get("BOTS"))[0]`), en lugar de `BOT_ID == "m15"`.
  - `trading_loop`: sin config, el heartbeat y el reporte se siguen haciendo. Solo se salta `engine.on_tick()`.
  - El log CSV se llama `f"Motor_{BOT_ID.upper()}"`.
  - `tui.render_params(cfg)` muestra genéricamente los pares clave→valor de `cfg.get("params", {})`.

- [ ] **Step 1:** Tests en `tests/test_main_loop.py`, con dobles de `db`, `broker` y `engine` y `mt5` parcheado con `monkeypatch`:
  - `test_sin_config_reporta_heartbeat`: con `db.load_config` devolviendo `({}, "INACTIVE")` y `HEARTBEAT_INTERVAL=0`, corre 3 iteraciones con `stop_event`. Afirma que se llamó `db.report` y que no se llamó `engine.on_tick`.
  - `test_loop_sobrevive_mt5_caido`: `mt5.account_info` lanza una excepción. El bucle sigue y `db.report` se sigue llamando.
  - `test_force_close_consumido_solo_si_plano`: `close_all` devuelve primero `False` y después `True`. `clear_force_close` se llama una sola vez.
  - `test_run_resuelve_motor`: `BOT_ID=n2` hace que `motor.run` elija `NullEngine` (parcheando `main.run` para capturar `engine_cls`).
- [ ] **Step 2:** `pytest tests/test_main_loop.py -v` → FAIL.
- [ ] **Step 3:** Implementar. Borrar `motor/main.py::__main__` (la entrada es `motor.run`).
- [ ] **Step 4:** `pytest -v` → PASS.
- [ ] **Step 5:** Commit `feat(motor): motor nulo, entrada única motor.run y bucle con heartbeat sin config`.

### Task 4: Etiquetas de operación genéricas y punto de enchufe del guardián de riesgo

**Files:**
- Create: `motor/risk.py`, `tests/test_broker_tags.py`, `tests/test_risk.py`
- Modify: `motor/broker.py`

**Interfaces:**
- Produces:
  - `Broker._open_log_type(comment: str) -> str`: si el comment tiene la forma `"TAG|texto"`, devuelve `TAG` en mayúsculas, recortado a 20 caracteres y con solo `[A-Z0-9_]`. En cualquier otro caso devuelve `"OPERACION"`. Se elimina la taxonomía de exp-1.
  - `class AccountRiskGuard`, con `allow_open(self, *, bot_id: str, symbol: str, side: str, lots: float) -> tuple[bool, str]`. La implementación por defecto siempre permite.
  - `Broker.__init__(self, logger, shadow=False, risk_guard: AccountRiskGuard | None = None)`. `market_order` consulta `risk_guard.allow_open` antes de enviar. Si el guardián veta, loguea `BLOQUEO` con el motivo y devuelve `None`, igual que un bloqueo por margen hoy.

- [ ] **Step 1:** Tests:
  - `test_tag_desde_comment`: `"SCALP|entrada" → "SCALP"`, `"sin tag" → "OPERACION"`, `"a b|x" → "AB"`.
  - `test_guardian_veta_apertura`: un guardián falso que devuelve `(False, "riesgo")` hace que `market_order` devuelva `None` sin llamar a `mt5.order_send` (parcheado).
  - `test_guardian_por_defecto_permite`.
- [ ] **Step 2:** `pytest tests/test_broker_tags.py tests/test_risk.py -v` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `pytest -v` → PASS.
- [ ] **Step 5:** Commit `feat(motor): etiquetas de operación por comment y gancho de guardián de riesgo por cuenta`.

### Task 5: `engine_config` en Supabase (migración aditiva) y lectura desde el bot

**Files:**
- Create: `migrations/n1_001_engine_config.sql`, `tests/test_db_config.py`
- Modify: `motor/db.py` (`load_config`)

**Interfaces:**
- Produces:
  - Tabla `engine_config(id bigserial pk, user_id uuid not null references auth.users, bot_id text not null, params jsonb not null default '{}', is_active boolean not null default true, updated_at timestamptz not null default now(), unique(user_id, bot_id))`. RLS: el usuario solo lee y escribe sus filas (`user_id = auth.uid()`); service-role sin restricción.
  - `Database.load_config() -> tuple[dict, str]` lee `engine_config` filtrando por `user_id`, `bot_id` e `is_active = true`. Devuelve `({"params": {...}, ...fila}, "ACTIVE")`, o la caché / `({}, "INACTIVE")` igual que hoy.

- [ ] **Step 1:** Inspeccionar el esquema real (solo lectura) con la service-role del `.env` de una instancia demo: listar las columnas, los checks y las políticas de `bot_instances`, `bot_state`, `bot_positions`, `bot_logs`, `provision_requests` y la tabla `engine_config`, si ya existe. Anotar el resultado en el PR, en particular cualquier `CHECK` sobre `bot_id`, `op_type` o `log_type` que impida `n1`/`n2` o tags libres. Si existe alguno, agregar en esta misma migración su ampliación aditiva.
- [ ] **Step 2:** Test `test_load_config_lee_engine_config` con un cliente falso de Supabase que registra la cadena `.table().select().eq()…`. Afirma que la tabla es `engine_config`, que hay filtro por `bot_id` y que `params` llega como dict.
- [ ] **Step 3:** `pytest tests/test_db_config.py -v` → FAIL.
- [ ] **Step 4:** Escribir la migración, con una cabecera en lenguaje sencillo (qué agrega y por qué), y cambiar `load_config`.
- [ ] **Step 5:** `pytest -v` → PASS.
- [ ] **Step 6:** Commit `feat(db): tabla engine_config para motores nuevos (migración aditiva n1_001)`. **No se aplica** hasta que Daniel la apruebe en el PR. La aplicación se hace en el SQL editor de Supabase y queda anotada en `docs/memory/`.

### Task 6: Lanzador, arranque y reinicio selectivo

**Files:**
- Create: `scripts/procs.py`, `scripts/run_motor.ps1` (reemplaza a `run_instance.ps1`), `scripts/restart_motor.ps1` (reemplaza a `restart_all.ps1`), `tests/test_procs.py`
- Delete: `scripts/run_instance.ps1`, `scripts/restart_all.ps1`
- Modify: `scripts/start_all.ps1`

**Interfaces:**
- Consumes: `load_registry`, `parse_bots`, `validate_instance`, `magic_for` (Task 2).
- Produces:
  - `scripts/procs.py`:
    - `@dataclass class Proc: pid: int; name: str; cmdline: str; exe: str`
    - `select_ours(procs: list[Proc], repo_root: str, clones_dir: str) -> list[Proc]`: devuelve un python/powershell solo si su `cmdline` contiene `-m motor.run`, `run_motor.ps1` o `scripts.vigilante` **y** contiene `repo_root` o su `cwd`/`exe` está bajo `repo_root`; un `terminal64.exe` solo si `exe` está bajo `clones_dir`. Las comparaciones de rutas son case-insensitive y normalizadas.
    - CLI: `python -m scripts.procs list --json` emite los procesos seleccionados, leídos con `wmic`/CIM vía `subprocess` sobre PowerShell `Get-CimInstance Win32_Process`.
  - `run_motor.ps1 -EnvFile <ruta> -BotId <id> [-Mode console|lite|tui]`: carga el `.env`, obtiene `MAGIC_NUMBER` con `python -m motor.registry magic <bot_id>` (añadir ese mini-CLI a `registry.py`) y ejecuta `python -m motor.run --mode <Mode>` con la ruta absoluta del repo como directorio de trabajo.
  - `start_all.ps1`, en cuatro fases:
    1. Preflight: `.env` de `instances\*.env` y `python -m motor.registry check <envfile>`, que corre `validate_instance`.
    2. `pytest -q` (se salta con `-SkipTests`).
    3. Lanzamiento: una ventana por motor de `BOTS`.
    4. Resumen.
    - Flags: `-Lite`, `-DryRun`, `-SkipTests`. Sin auditorías y sin `news_exporter`.
  - `restart_motor.ps1 [-DryRun] [-Lite]`: mata solo lo que devuelve `select_ours` y relanza `start_all.ps1 -SkipTests`. Escribe en `logs\restart_motor.log`.

- [ ] **Step 1:** Tests en `tests/test_procs.py`:
  - `test_seleccion_ignora_produccion`: la lista incluye `python -m bot.lite_e2` con la ruta de `...\sentinel_trading`, `python -m scripts.news_exporter` de producción, `terminal64` en `C:\MT5_instances\mt5_26329953\`, `python -m motor.run --mode lite` con la ruta de este repo y `terminal64` en `C:\MT5_instances_n1\mt5_123\`. Solo deben quedar los dos últimos.
  - `test_rutas_case_insensitive`.
  - `test_powershell_run_motor_incluido`.
- [ ] **Step 2:** `pytest tests/test_procs.py -v` → FAIL.
- [ ] **Step 3:** Implementar `procs.py`, el mini-CLI de `registry` y los `.ps1`.
- [ ] **Step 4:** `pytest -v` → PASS. Con producción viva, `powershell -File scripts\restart_motor.ps1 -DryRun` debe escribir en el log `0 procesos` (no hay motores nuevos corriendo todavía). Debe aparecer cero PIDs de los procesos `bot.lite_e2` y `terminal64` de `C:\MT5_instances`.
- [ ] **Step 5:** Commit `feat(scripts): lanzador run_motor y reinicio selectivo que no toca producción`.

### Task 7: Vigilante y tareas programadas propias

**Files:**
- Create: `scripts/vigilante.py`, `scripts/instalar_tareas.ps1`, `tests/test_vigilante.py`

**Interfaces:**
- Consumes: `select_ours`, `Proc` (Task 6); `parse_bots` (Task 2).
- Produces:
  - `expected_instances(instances_dir: str) -> set[tuple[str, str]]`: devuelve los pares `(env_path, bot_id)` de todos los `instances\*.env` con `BOTS=`. Ignora `example.env` y los archivos que empiezan por `_`.
  - `running_instances(procs: list[Proc]) -> set[tuple[str, str]]`: los obtiene del `cmdline` de `run_motor.ps1 -EnvFile <x> -BotId <y>`.
  - `missing(expected, running) -> set[tuple[str, str]]`.
  - `main()`: lanza `run_motor.ps1 -Mode lite` para cada faltante en una ventana nueva y lo loguea en `logs\vigilante.log`. `--dry-run` solo loguea.
  - `instalar_tareas.ps1`: registra `SentinelN1Vigilante` (cada 3 min, sesión interactiva del operador, `python -m scripts.vigilante`). Es idempotente y no toca ninguna tarea `Sentinel*` existente. `-Desinstalar` la quita.

- [ ] **Step 1:** Tests:
  - `test_vigilante_relanza_faltantes`: dos `.env` (uno con `BOTS=n1,n2`) y un proceso corriendo `n1`. Faltantes = `{(env_a, "n2"), (env_b, "n1")}`.
  - `test_ignora_example_env`.
- [ ] **Step 2:** `pytest tests/test_vigilante.py -v` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `pytest -v` → PASS, y `python -m scripts.vigilante --dry-run` corre sin error.
- [ ] **Step 5:** Commit `feat(scripts): vigilante que relanza motores caídos (tarea SentinelN1Vigilante)`.

### Task 8: Alta manual de cuentas (provisionador)

**Files:**
- Modify: `scripts/provision.py`
- Create: `tests/test_provision.py`

**Interfaces:**
- Produces:
  - `python -m scripts.provision alta --user-id <uuid> --email <e> --login <n> --server <s> --password <p> --bots n1,n2 [--symbol XAUUSD+] [--label <t>] [--dry-run]`:
    - Clona MT5 en `MT5_CLONES_DIR` (por defecto `C:\MT5_instances_n1`).
    - Siembra el perfil IPC, habilita Algo Trading y escribe `instances\<user_id>.env`.
    - Hace upsert en Supabase de las filas `bot_instances` (`is_active = false`) y `engine_config` (`params = {}`) por cada bot, y la fila `account_settings` del usuario solo si no existe (nunca pisa una existente).
    - **No** llama a `SentinelRestart`: `RESTART_TASK` se elimina y se reemplaza por el vigilante.
  - El modo PENDING/`--watch` se **elimina** mientras convivan los dos bots. El docstring explica por qué y que vuelve con el reemplazo.
  - `render_env(values: dict) -> str` (función pura): genera el `.env` con `BOTS`, `MAGIC_N1`, `MAGIC_N2`, `MT5_PORTABLE=true` y `SYMBOL`.
  - `clone_dir_for(login: str, clones_dir: str) -> str`.

- [ ] **Step 1:** Tests:
  - `test_render_env_incluye_bots_y_magics`.
  - `test_clone_dir_fuera_de_produccion`: el resultado empieza por `C:\MT5_instances_n1` y nunca por `C:\MT5_instances\`.
  - `test_alta_dry_run_no_escribe`: en `tmp_path`, con un cliente Supabase falso, no se crean archivos ni se hacen upserts.
- [ ] **Step 2:** `pytest tests/test_provision.py -v` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `pytest -v` → PASS.
- [ ] **Step 5:** Commit `feat(provision): alta manual de cuentas del bot nuevo en carpeta de clones propia`.

### Task 9: Postgres local: base, migraciones versionadas y punto de enchufe

**Files:**
- Create: `motor/localdb.py`, `localdb/migrations/001_base.sql`, `scripts/localdb_migrate.py`, `tests/test_localdb.py`
- Modify: `motor/config.py` (`LOCAL_PG_DSN`), `instances/example.env`

**Interfaces:**
- Produces:
  - `LOCAL_PG_DSN`: variable de entorno, por ejemplo `postgresql://sentinel_n1:<pass>@localhost:5432/sentinel_n1`. Vacía ⇒ base local desactivada; el bot sigue funcionando y registra un aviso al arrancar.
  - `001_base.sql`: tabla `schema_migrations(version text pk, applied_at timestamptz)` y tabla `engine_events(id bigserial pk, ts timestamptz not null default now(), user_id uuid, bot_id text, kind text, payload jsonb)`.
  - `scripts/localdb_migrate.py`: `apply_pending(conn, migrations_dir: str) -> list[str]` aplica en orden los `NNN_*.sql` que falten, cada uno en su transacción, y devuelve las versiones aplicadas. Se ejecuta con `python -m scripts.localdb_migrate`.
  - `motor/localdb.py`: `class LocalDB` con `connect() -> bool`, `record_event(kind: str, payload: dict) -> None` (best-effort, nunca lanza) y `close()`. `main.setup` la crea y la deja como `engine.localdb`.
  - La sincronización hacia Supabase **no** se hace en esta fase: el bot sigue escribiendo directo a Supabase, como permite el spec 7.5.

- [ ] **Step 1:** Oscar crea el rol y la base: `sentinel_n1` y `sentinel_n1_test`. El comando exacto va en `docs/como-funciona.md` y lo corre él o Daniel con la contraseña del superusuario de Postgres, que Claude no conoce. Después define `LOCAL_PG_DSN_TEST` en su entorno.
- [ ] **Step 2:** Tests en `tests/test_localdb.py`. Se saltan con `pytest.skip` si no existe `LOCAL_PG_DSN_TEST`:
  - `test_migraciones_idempotentes`: aplicar dos veces ⇒ la segunda devuelve `[]`.
  - `test_record_event_inserta`.
  - `test_localdb_sin_dsn_no_rompe`: `connect()` devuelve `False` y `record_event` no lanza.
- [ ] **Step 3:** `pytest tests/test_localdb.py -v` → FAIL.
- [ ] **Step 4:** Implementar.
- [ ] **Step 5:** `pytest -v` → PASS (los tests de base de datos corren, no se saltan).
- [ ] **Step 6:** Commit `feat(localdb): Postgres local con migraciones versionadas y registro de eventos`.

### Task 10: CLAUDE.md, memoria, skills y documentación sencilla

**Files:**
- Create:
  - `CLAUDE.md`
  - `docs/como-funciona.md`
  - `docs/convivencia-y-reemplazo.md`
  - `docs/memory/fork-fase1-decisiones.md`
  - `docs/memory/equipo-y-flujo-git.md`
  - `.claude/skills/lanzar-bot-nuevo/SKILL.md`
  - `.claude/skills/migracion-supabase-segura/SKILL.md`
  - `.claude/skills/flujo-git-equipo/SKILL.md`
  - `.claude/skills/actualizar-memoria/SKILL.md`
- Modify: `README.md`, `instances/example.env`, `docs/memory/MEMORY.md`, `docs/memory/logging-taxonomy-and-ticket.md`, `docs/dashboard-handoff-multibot.md`
- Delete (`docs/memory/`):
  - `bot-760-root-cause.md`
  - `bot-design-constraints.md`
  - `healer-*.md`
  - `ideas-experimento-2.md`
  - `m5-range-regime-v2.md`
  - `multi-bot-margin-governance.md`
  - `op1-red-closes-healer-vs-reset.md`
  - `pendientes-pre-experimento-2.md`
  - `unwind-orphans-hedge-count-state.md`
  - `workflow-implement-then-commit.md`
  - `newsguard-phase2-nri.md`

**Interfaces:**
- Consumes: los nombres reales de comandos, scripts, variables y tareas de las Tasks 1–9.

- [ ] **Step 1:** Contenido de `CLAUDE.md`:
  - qué es el proyecto y sus 5 fases;
  - las reglas de convivencia con producción (copiar las Global Constraints);
  - el flujo git (ramas `faseN/...`, `datos/...`, PR, revisión cruzada, tags `vN-AAAA-MM-DD`, identidad por commit);
  - la memoria en `docs/memory/` y las skills en `.claude/skills/`;
  - la regla: "al cerrar cada tarea relevante, actualizar memoria/CLAUDE.md/skills en el mismo PR";
  - la regla: "no usar conclusiones de exp-2".
- [ ] **Step 2:** Escribir cada skill con frontmatter `name`/`description` y pasos concretos que usen los comandos reales:
  - `lanzar-bot-nuevo`: `start_all.ps1`, `restart_motor.ps1 -DryRun` primero, vigilante, y cómo verificar que producción sigue viva (`Get-CimInstance` y que aparezcan los PIDs `bot.lite_e2`).
  - `migracion-supabase-segura`: inspeccionar el esquema real, solo cambios aditivos, prefijo `n1_`, revisión con Daniel, aplicar y anotar en la memoria.
  - `flujo-git-equipo`.
  - `actualizar-memoria`.
- [ ] **Step 3:** Escribir los documentos en lenguaje sencillo:
  - `README.md`: qué es y cómo se arranca en 5 pasos.
  - `como-funciona.md`: las piezas y cómo se hablan, con un diagrama en texto.
  - `convivencia-y-reemplazo.md`: aislamiento; paso a paso para pasar una cuenta al motor nuevo cambiando `BOTS=`; vuelta atrás; qué unificar en el PR al original (filtros por ruta, provisionador PENDING por motor, `news_exporter`).
- [ ] **Step 4:** Mover las decisiones de la memoria local de Claude a `docs/memory/` y actualizar `MEMORY.md`.
- [ ] **Step 5:** Verificar que todo comando citado en los documentos y skills existe: `grep` de cada `scripts\...` y `python -m ...` contra el árbol.
- [ ] **Step 6:** Commit `docs: CLAUDE.md, memoria compartida, skills iniciales y guías en lenguaje sencillo`.
- [ ] **Step 7:** Abrir PR `fase1/limpieza-multimotor` → `main` con un resumen para Daniel (qué cambia en el servidor y por qué) y pedirle revisión. Junto con el PR, explicarle cómo proteger `main` en GitHub (Settings → Branches → Require a pull request).

---

## Bloque B — Repo de la app web. Rama `feat/motores-dinamicos`

### Task 11: Motores dinámicos en la app web

**Files** (en `C:\Users\Administrator\Documents\sentinel_trading-webapp`):
- Modify:
  - `src/lib/bots.ts`
  - `src/lib/metrics.ts`
  - `src/components/dashboard/metric-cards.tsx`
  - `src/components/dashboard/profit-target-panel.tsx`
  - `src/components/dashboard/account-panel.tsx`
  - `src/components/dashboard/bot-cards.tsx`
  - `src/components/admin/admin-grid.tsx`
  - `src/app/dashboard/admin/page.tsx`
  - `src/app/dashboard/config/page.tsx`
  - `src/lib/supabase/types.ts`
- Create:
  - `src/components/config/engine-config-json.tsx`
  - `src/lib/actions/engine-config.ts`
  - `scripts/metrics.check.ts` (se corre con `npx tsx`)

**Interfaces:**
- Produces:
  - `type BotId = string`.
  - `BOT_META: Record<string, {tag, label, tone}>` con `m15`, `m5`, `e2` conocidos.
  - `botMeta(id: string)` con fallback `{tag: id.toUpperCase(), label: \`Motor ${id}\`, tone: "neutral"}`.
  - `isLegacyBot(id): id is "m15" | "m5"`.
  - `BASE_PRIORITY = ["m15", "e2", "m5"]`.
  - `accountBaseState(states): BotState | null`: el primer estado con `initial_balance`, siguiendo `BASE_PRIORITY` y después cualquier otro.
  - `closedStatsByBot(rows): Record<string, Stats>`, que reemplaza las claves fijas `m15`/`m5`.
  - Config: `bot_id` legacy ⇒ el formulario actual sin cambios. Cualquier otro ⇒ `EngineConfigJson`: un textarea con validación JSON que guarda `engine_config.params` mediante una server action con RLS.
  - Lista de motores del usuario: los `bot_id` presentes en sus `bot_instances`, ordenados primero los legacy.

- [ ] **Step 1:** `scripts/metrics.check.ts` (asserts con `node:assert`):
  - `accountBaseState` con `m15` (base 1000) y `m5` (base 900) da 1000.
  - Con solo `n1` (base 500) da 500.
  - Con `e2` y `m5` da `e2`.
  - `closedStatsByBot` agrupa `n1`/`n2` por separado.
  - `botMeta("n1").tag === "N1"`.
- [ ] **Step 2:** `npx tsx scripts/metrics.check.ts` → FAIL.
- [ ] **Step 3:** Implementar los cambios en los archivos listados, buscando cada `"m15"`/`"m5"` del grep del plan. Donde hoy se muestra `M15 … · M5 …`, se genera la lista con los motores del usuario. Para un usuario con `m15`+`m5` el texto debe quedar idéntico.
- [ ] **Step 4:** `npx tsx scripts/metrics.check.ts && npm run lint && npm run build` → todo OK.
- [ ] **Step 5:** Commit `feat(dashboard): motores dinámicos (cualquier bot_id) sin cambiar la vista m15/m5`, push de la rama y PR. Tomar el link del preview de Vercel.
- [ ] **Step 6:** Revisar el preview con dos usuarios: uno de producción (`m15`+`m5`), donde métricas y textos deben ser idénticos a producción; y el usuario demo `n1`+`n2` creado en Task 12, donde deben aparecer dos tarjetas, encendido/apagado y cierre forzado por motor, métricas de cuenta y la meta. Daniel aprueba el PR y se mergea.

> Nota para el PR: hoy `isBotId` solo acepta `m15`/`m5`, así que los usuarios `e2` probablemente no ven sus tarjetas de motor. Con este cambio las empezarán a ver. Se le avisa a Daniel en el PR; es un efecto secundario deseable, pero visible.

---

## Bloque C — Verificación de punta a punta

### Task 12: Puesta en marcha en demo y cierre de la Fase 1

**Files:** ninguno nuevo. Solo `docs/memory/` (registrar el resultado) y un tag.

- [ ] **Step 1:** Prerrequisitos de Oscar:
  - Una cuenta demo de Vantage nueva (login/servidor/contraseña).
  - Un usuario nuevo de Supabase creado desde el panel de Auth (uuid + email).
  - La migración `n1_001` ya aplicada (Task 5).
- [ ] **Step 2:** `python -m scripts.provision alta ... --bots n1,n2`, luego `scripts\start_all.ps1 -Lite`, luego `scripts\instalar_tareas.ps1`.
- [ ] **Step 3:** Verificar los criterios del spec §8:
  1. `n1` y `n2` aparecen `RUNNING` en `bot_instances` con heartbeat menor a 30 s.
  2. Desde el dashboard funcionan apagar, encender y cierre forzado de cada uno, y la meta se puede editar.
  3. Los PIDs de `bot.lite_e2` y de `C:\MT5_instances\*` siguen siendo los mismos de antes de empezar (anotarlos en el Step 1).
  4. Matar a mano el `terminal64` del clon `n1`: el vigilante lo relanza en 3 min o menos, y los procesos de producción siguen intactos.
  5. `engine_events` en Postgres local recibe eventos.
  6. `pytest -q` en verde.
- [ ] **Step 4:** Registrar el resultado en `docs/memory/fork-fase1-decisiones.md`. Después del merge, tag `fase1-base-2026-MM-DD` en `main` y push del tag.
