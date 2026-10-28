# Fase 1 — Base del fork (diseño)

Fecha: 2026-10-28 · Autores: Oscar (dueño de estrategia/análisis), Daniel (app web/servidor), con Claude.

## 1. Intención y por qué

Este repo (`oscarhalabi/sentinel_trading-bot`, fork de `lozadaitm/sentinel_trading-bot`) va a alojar un **bot de trading completamente nuevo**, construido casi desde cero. La estrategia vieja (exp-1: Sentinel M15 + Grinder M5, martingala con cobertura, Healer) se descarta, junto con sus reglas rígidas ("sin SL", "sin stop por drawdown", "no salirse de lo diseñado") y también todas las conclusiones de exp-2/e2, que no son confiables porque estaban sesgadas por las instrucciones de ese repo.

Lo que **sí** se conserva es la forma de trabajar y la infraestructura: lanzar instancias leyendo un `.env`, un clon de MT5 por cuenta, el provisionador de cuentas, la app web con su Supabase (encendido/apagado, historial, metas, retiros).

La Fase 1 no construye la estrategia nueva. Deja **la base limpia** sobre la que las fases siguientes (datos → investigación → laboratorio → bot nuevo) van a trabajar, y la deja preparada para dos cosas:

1. **Convivir** en el mismo VPS, con la misma app web y el mismo Supabase, con el bot e2 que hoy está en producción, sin poder dañarlo.
2. **Reemplazarlo** algún día: es muy probable que el bot nuevo sustituya a exp-1/e2. Cuando eso pase (PR al repo original), el arranque de bots, el provisionador, las tareas programadas y la app web deben seguir funcionando igual; cambiar de motor debe ser un cambio de configuración, con vuelta atrás posible.

## 2. Contexto actual (verificado el 2026-10-28)

- **Producción** corre en este mismo VPS desde **otro clon**: `C:\Users\Administrator\Documents\sentinel_trading` (rama `experimento-2-rediseno`). Procesos vivos: 3 `terminal64.exe` en `C:\MT5_instances\mt5_263299{53,55,57}`, 6 `python -m bot.lite_e2` y el `scripts.news_exporter`.
- **Tareas programadas** existentes: `SentinelRestart`, `SentinelProvisioner`, `SentinelDailyReport`, `SentinelTickCollector` (pertenecen a producción).
- **Postgres 18** (`postgresql-x64-18`) está instalado y corriendo en el VPS. Hoy no lo usa nadie del bot (el Postgres del repo es legacy).
- El bot actual escribe **directo a Supabase** (control cada 3 s, heartbeat/estado/posiciones cada 15 s, logs en cola asíncrona).
- La **app web** vive en otro repo (`lozadaitm/sentinel_trading-webapp`, Next.js en Vercel). Controla el bot escribiendo tablas de Supabase (`bot_instances.is_active`, `force_close`, `account_settings`, `bot_config`). No hay tabla de comandos.
- El motor se enchufa a `bot/main.py::setup(engine_cls=...)` con una interfaz pequeña: constructor `(broker, logger)`, `init_history_cursor()`, `on_tick()`, `close_all(reason) -> bool`, y atributos `is_active`, `close_only`, `cfg`, `b`, `user_email`, `initial_balance`, `last_flow_ticket`, `baseline_applied_at`.
- **Peligro conocido**: `scripts/restart_all.ps1` mata *todos* los `python` con `bot.*`, todos los `run_instance.ps1` y *todos* los `terminal64.exe` del servidor. Usado desde este repo tumbaría producción.
- **Y al revés (verificado en el clon de producción)**: el `restart_all.ps1` de producción usa el mismo filtro (`bot\.(main|tui|lite)`, `run_instance.ps1`, todo `terminal64.exe`) y lo dispara `SentinelRestart` (bajo demanda, tras cada provisión). Es decir, **cada reinicio de producción mataría también al bot nuevo**, y no lo relanzaría.
- El provisionador de producción (`SentinelProvisioner`, cada 5 min) toma **toda** fila `provision_requests` en PENDING sin filtrar por motor.
- En el Supabase compartido, e2 usa `bot_id = 'e2'` (`BOTS=e2` en sus `.env`), y las migraciones **010–017 ya están usadas** por la rama de producción.

## 3. Objetivos

1. **Limpiar** el repo de toda la lógica de trading vieja, dejando un esqueleto que arranca, se conecta a MT5 y a Supabase, reporta heartbeat/estado/posiciones, obedece encendido/apagado/cierre forzado/meta, pero **no opera** (motor "nulo" / esqueleto) hasta que exista la estrategia nueva.
2. **Generalizar** la identidad de motor: dejar de tener fija la pareja M15/M5. Un motor se declara por nombre (`BOT_ID`) y su magic, y el lanzador/validador/provisionador lo resuelven desde un **registro de motores** en un solo lugar, no repartido en `config.py`, `run_instance.ps1` y `provision.py`.
3. **Aislar** el bot nuevo del de producción en el mismo VPS (procesos, clones MT5, tareas, logs, cuentas).
4. **Compartir** la app web y Supabase con el bot viejo, con cambios de base de datos **solo aditivos**.
5. **Postgres local** como almacén en tiempo real del bot nuevo, con sincronización a Supabase de lo que la app web necesita.
6. **Orden del proyecto para dos personas**: flujo git con ramas y PRs, `CLAUDE.md` nuevo, memoria compartida en el repo, skills compartidas, documentación en lenguaje sencillo.
7. **Camino de reemplazo** documentado: cómo pasar un usuario/cuenta del motor viejo al nuevo y cómo volver atrás.

## 4. No-objetivos (Fase 1)

- No se diseña ni implementa ninguna estrategia, indicador, señal ni gestión de riesgo de trading.
- No se descargan datos históricos (Fase 2), no se investiga (Fase 3), no se construye el laboratorio (Fase 4).
- No se modifica el clon de producción (`sentinel_trading`) ni sus tareas programadas.
- No se cambia lo que la app web muestra/hace para el bot viejo (sí se cambia su código para soportar al nuevo).
- No se manda nada al repo original todavía.

## 5. Anti-objetivos (sería un fracaso aunque "funcione")

- Que cualquier script de este repo pueda matar, reiniciar, o tocar las posiciones de las cuentas de producción.
- Que una migración de Supabase modifique, renombre o borre algo que el bot viejo o la app web usan hoy.
- Que el día del reemplazo haya que reescribir el lanzador, el provisionador o la app web.
- Reintroducir reglas rígidas de estrategia en la infraestructura (p. ej. validaciones que asuman hedge/healer).
- Documentación que solo entienda quien la escribió.

## 6. Decisiones ya tomadas (por Oscar)

- Python es el núcleo del bot.
- Se conserva el lanzamiento actual: `.env` por instancia, una instancia MT5 separada por cuenta.
- Se trabaja en **este** repo (el fork), limpiándolo; `main` = `main` del fork. Lo bueno se manda después como PR al original.
- **Misma app web y mismo Supabase** controlan ambos bots en paralelo hasta decidir cuál queda.
- Postgres local en el VPS para datos en tiempo real del bot nuevo; Supabase se sincroniza esporádicamente.
- Flujo git: `main` estable sin push directo; una rama por tema; PR revisado por la otra persona (o revisión automática de Claude primero); commits en español con formato `tipo(ámbito): ...`; tags en cada paso a producción.
- Roles: Oscar avanza en casi todo (estrategia/análisis y también infra cuando la domina) y consulta a Daniel por llamada; Daniel es el referente de app web y servidor. Por eso los PRs de servidor/app web llevan explicación clara de qué cambia y por qué.
- Skills compartidas dentro del repo, y Claude propone crear skills cuando haga falta.
- `CLAUDE.md`, memoria y skills se mantienen al día a medida que se avanza.
- Ignorar todas las simulaciones/investigaciones de exp-2.

## 7. Diseño

### 7.1 Qué se conserva, qué se adapta, qué se borra

*(Por 7.3, el paquete `bot/` se renombra —p. ej. a `motor/`— al limpiarlo; "se conserva" significa que el contenido se mantiene, no la ruta.)*

**Se conserva (tal cual o casi):**
`bot/broker.py` (conexión/órdenes MT5), `bot/db.py` (Supabase), `bot/logger.py`, `bot/notify.py` (correo Resend), `bot/news.py` (NewsGuard, genérico) + `scripts/news_exporter.py`, `bot/lite.py` (modo bajo consumo), `scripts/provision.py` (clonado MT5 portable + siembra de perfil IPC + `.env`), `bot/config.py` (constantes de infraestructura, `mt5_init_kwargs`, `SHADOW_MODE`), `bot/main.py` (bucle de control/heartbeat/flujos/meta), migraciones de Supabase existentes, `requirements.txt` (sin `psycopg2-binary` legacy si no se usa; ver 7.5).

**Se adapta:**
- `bot/config.py`: `EXPECTED_MAGIC`/`validate_identity` pasan a leer del registro de motores (7.2). La validación se mantiene (dos motores con el mismo magic en una cuenta sigue siendo un error grave), pero su justificación ya no menciona Healer/Hedge Lock.
- `scripts/run_instance.ps1`, `scripts/start_all.ps1`, `scripts/launch_all.ps1`, `scripts/restart_all.ps1`, `scripts/provision.py`: resuelven módulo y magic desde el registro; se quitan las auditorías de estrategia del preflight; aislamiento (7.3).
- `bot/broker.py::_open_log_type` y el uso de `op_type` en `bot_positions`: se generalizan para que el motor indique el tipo/etiqueta de cada operación, sin la taxonomía ENTRADA/PROTECCION/RECOVERY/RESCATE/GRINDER fija.
- `bot/tui.py`: `render_params(cfg)` deja de asumir los parámetros de exp-1 (muestra genéricamente lo que el motor exponga).
- `instances/example.env`: se reescribe para el modelo nuevo.
- `README.md`: se reescribe en lenguaje sencillo.

**Se borra** (queda en el historial de git; se puede recuperar):
`bot/strategy.py`, `bot/strategy_m5.py`, `bot/ledger.py`, `bot/budget.py`, `bot/indicators.py`, `bot/main_m5.py` y variantes `_m5`, `scripts/audit_margin.py`, `scripts/audit_m5_signals.py`, `tests/test_ledger.py`, `tests/test_m5_and_budget.py`, `bot_backtesting/` (el arnés es de la estrategia vieja; la Fase 4 diseña su propio laboratorio), `original_MQL5.txt`, `Grinder_Anterior.mq5`, `experimento_1_logs.csv`, `schema.sql`/`migrations.sql` (Postgres legacy), `docs/plan-m5-m15-coexistencia.md`, y las notas de `docs/memory/` específicas de la estrategia vieja (Healer, Hedge, OP1–OP4, M5 v2, margen multi-bot, diseño rígido, workflow de push directo, ideas/pendientes de exp-2).
Se **conservan** las notas de memoria de infraestructura: modelo multi-instancia Supabase, clon MT5/perfil IPC, exportador de noticias, NewsGuard fase 1 (como referencia de infraestructura), signup privado + provisioner, objetivo de ganancia + cierre forzado, retiros + comisión, taxonomía de logs (se actualiza al generalizar `log_type`).
*(Llamada de Claude: borrar en vez de archivar en una carpeta, porque Oscar pidió empezar casi de cero y no quiere que el contenido viejo sesgue el trabajo nuevo; git conserva todo.)*

### 7.2 Registro de motores

Un único archivo (p. ej. `bot/engines.py` o un `engines.json` que también lea PowerShell) declara cada motor disponible: `bot_id`, módulo de Python a lanzar (normal/TUI/lite), variable del `.env` de la que sale su magic, y descripción. Inicialmente contiene **solo el motor nuevo** (en esqueleto). `config.validate_identity`, `run_instance.ps1`, `start_all.ps1` y `provision.py` leen de aquí. Añadir un motor = una entrada en el registro.

El `bot_id` del motor nuevo debe ser **distinto** de `m15`, `m5` y del que use e2 en Supabase, para que sus filas nunca se mezclen. *(Llamada de Claude: nombre provisional `n1`; Oscar puede renombrarlo antes de la primera migración.)*

**Varios motores nuevos, no uno solo (pedido de Oscar).** Es probable que terminen existiendo varios bots separados (scalping, intradía, swing, uno por par/símbolo, uno por régimen, uno de cobertura, etc.). La infraestructura de Fase 1 no debe asumir un único motor nuevo:
- El registro admite N motores; cada uno con su `bot_id`, su magic, su módulo y su fila de `engine_config`. Agregar uno no requiere tocar lanzador, provisionador, vigilante ni app web.
- Una misma cuenta puede correr varios motores a la vez (como hoy M15+M5), y cada `.env` declara cuáles en `BOTS=`.
- El símbolo deja de ser uno fijo por instancia: cada motor declara el/los símbolos que opera (el `SYMBOL=` del `.env` queda como valor por defecto).
- La app web lista y controla cualquier cantidad de motores por usuario (encendido/apagado y cierre forzado por motor; métricas de cuenta agregadas).
- Cuando varios motores comparten cuenta, comparten margen y riesgo. En Fase 1 solo se deja el **punto de enchufe** para un guardián de riesgo a nivel de cuenta (que pueda vetar aperturas de cualquier motor); sus reglas se diseñan en Fases 3–5.
- El esqueleto de Fase 1 se prueba con **dos** motores nulos en la misma cuenta demo, para demostrar que conviven sin mezclarse.
- Los nombres concretos de cada motor se deciden cuando la investigación diga cuáles valen la pena; `n1` es solo el primero.

**Compatibilidad para el reemplazo:** el formato del `.env` se mantiene (mismas claves de Supabase, identidad, MT5, Resend, `SHADOW_MODE`, `BOTS=`); el motor nuevo añade su propia clave de magic (p. ej. `MAGIC_N1`). Cuando llegue el PR al original, el registro de allá podrá contener los motores viejos y el nuevo a la vez, y pasar un usuario de uno a otro es cambiar `BOTS=` en su `.env` (y la fila correspondiente en `bot_instances`). Volver atrás es lo mismo al revés.

### 7.3 Aislamiento del bot de producción en el mismo VPS

- **Carpeta de clones MT5 propia** (p. ej. `C:\MT5_instances_n1\...`), configurable por variable, distinta de `C:\MT5_instances`.
- **Cuentas propias**: el bot nuevo nunca usa una cuenta MT5 que esté usando e2. Durante Fases 1–5 se usan cuentas **demo** propias. *(Llamada de Claude.)*
- **Reinicio y parada selectivos**: `restart_all.ps1` (y cualquier script de parada) solo mata procesos cuyo directorio de trabajo o ruta pertenezca a **este** repo y terminales cuya ruta esté bajo **su** carpeta de clones. Nunca por nombre genérico. Debe tener un `-DryRun` que muestre qué mataría, y la Fase 1 se da por buena solo si un `-DryRun` con producción viva lista cero procesos de producción.
- **Tareas programadas propias** con prefijo distinto (p. ej. `SentinelN1Restart`, `SentinelN1Provisioner`); no se tocan las `Sentinel*` existentes.
- **Sobrevivir a los reinicios de producción** (no se modifica producción): *(llamadas de Claude)*
  - El código del bot nuevo vive en un paquete Python con otro nombre (p. ej. `motor/` en vez de `bot/`) y su lanzador en un script con otro nombre (p. ej. `run_motor.ps1`), para que el filtro de producción (`bot\.(main|tui|lite)`, `run_instance\.ps1`) no lo reconozca.
  - Los terminales MT5 sí morirán (producción mata todo `terminal64.exe` por nombre). Por eso el bot nuevo tiene un **vigilante** propio (tarea programada cada pocos minutos) que relanza las instancias del bot nuevo que no estén vivas, y el bot reconecta MT5 si pierde el terminal.
  - Cuando llegue el reemplazo (PR al original), se unifican los filtros de reinicio en uno basado en la ruta del repo y la carpeta de clones, no en nombres.
- **Exportador de noticias**: producción ya corre uno y escribe el JSON en la carpeta Common de MetaQuotes. El bot nuevo **lee ese mismo JSON** en lugar de lanzar un segundo exportador, mientras convivan. *(Llamada de Claude: evita dos procesos escribiendo el mismo archivo.)*
- **Provisionador**: como el de producción toma toda fila PENDING, durante la convivencia **no se usa `provision_requests`** para el bot nuevo. Las cuentas demo del bot nuevo se dan de alta a mano con un comando del `provision.py` de este repo que recibe los datos por argumentos (clona MT5 en la carpeta propia, escribe el `.env`, siembra filas `n1` en Supabase). El alta automática por la app web para el bot nuevo queda para el reemplazo. *(Llamada de Claude.)*
- **Identidad en Supabase**: cada cuenta demo del bot nuevo es un **usuario de Supabase nuevo y propio**, creado a mano, nunca uno que ya tenga filas `e2`/`m15`/`m5`. Así `account_settings` (metas, base de cálculo), que es por usuario y no por motor, nunca se cruza entre bots. *(Llamada de Claude.)*
- **Logs** en la carpeta `logs/` de este repo.

### 7.4 Supabase compartido (solo cambios aditivos)

- Tablas genéricas que el bot nuevo usa tal cual, filtrando por `user_id` + `bot_id`: `bot_instances`, `account_settings`, `bot_state`, `bot_positions`, `bot_logs`, `bot_candles`, `invites`, `provision_requests`, `billing_settings`, `withdrawals`.
- `bot_config` (≈60 parámetros de exp-1) **no se toca**. El motor nuevo tendrá su propia configuración en una tabla nueva y flexible (p. ej. `engine_config` con `user_id`, `bot_id`, y un campo JSON de parámetros), cuyo contenido definirá la Fase 5. En Fase 1 basta con que exista y el bot la lea.
- `bot_positions.op_type` y `bot_logs.log_type`: el bot nuevo escribe sus propias etiquetas; si hay restricciones (enum/check) que lo impidan, se amplían de forma aditiva.
- Si `provision_requests` necesita saber a qué motor va la solicitud, se usa la columna `bots` que ya existe (o se añade una columna nueva, nunca se cambia el significado de una existente).
- El `bot_id` nuevo debe ser distinto de `m15`, `m5` y `e2`.
- Antes de escribir cualquier migración, se revisa el esquema **real** de Supabase (no solo los archivos de este repo), porque producción ya aplicó 010–017 y pudo agregar restricciones. Las migraciones del bot nuevo usan un prefijo propio para no chocar en numeración (p. ej. `migrations/n1_001_...sql`) *(llamada de Claude)*, y cada una va con un comentario en lenguaje sencillo de qué agrega y por qué, y se revisa en PR con Daniel antes de aplicarse.

### 7.5 Postgres local en el VPS

- El bot nuevo escribe en el Postgres local (ya instalado) todo lo de alta frecuencia que necesite (ticks/velas propios, decisiones, señales, estado interno). En Fase 1 se crea la base, el usuario y un esquema mínimo, con un mecanismo de migraciones locales versionadas (mismo estilo numerado).
- Un **sincronizador** separado del bucle de trading copia a Supabase solo lo que la app web muestra (estado, posiciones, logs relevantes, velas), con frecuencia configurable. El control desde la app web (encendido/apagado, cierre forzado, metas) se sigue **leyendo de Supabase** con la misma latencia que hoy (~3 s), porque es lo que da seguridad.
- Si Supabase o el sincronizador fallan, el bot sigue operando con su base local y el sincronizador se pone al día después. Si el Postgres local falla, el bot no abre operaciones nuevas.
- *(Lo deja al constructor: si en Fase 1 el bot escribe directamente a Supabase como hoy y el sincronizador se completa en Fase 5, siempre que la base local, sus migraciones y el punto de enchufe existan. Lo que no es negociable es que el diseño final sea local-primero.)*

### 7.6 App web

- Sin cambios para el bot viejo.
- Para el bot nuevo: que aparezca como un motor más (la app ya filtra por `bot_id`, ver `docs/dashboard-handoff-multibot.md`) con encendido/apagado, cierre forzado, historial y metas funcionando.
- Ojo: según el handoff, la app tiene partes fijas a `m15`/`m5` (las métricas de cuenta prefieren la fila `m15` y caen a `m5`; los conteos por motor están fijos; el signup siembra `m15`+`m5`). Un usuario que solo tenga `n1` probablemente se vea vacío sin un cambio en la app web.
- **Decisión de Oscar: los cambios de la app web que haga falta los hacemos nosotros, y entran en la Fase 1.** La Fase 1 solo termina cuando el bot nuevo se ve y se controla desde el dashboard.
- El repo de la app web (`lozadaitm/sentinel_trading-webapp`, clon local en `C:\Users\Administrator\Documents\sentinel_trading-webapp`) es **el mismo que usa producción** y se despliega a Vercel. Por eso:
  - Se trabaja en una rama y PR en ese repo, revisado por Daniel; nunca push directo a su `main`.
  - Se prueba en el **preview de Vercel** del PR antes de mergear.
  - Cambios compatibles: quitar lo fijo a `m15`/`m5` (métricas de cuenta, conteos por motor) por lógica que tome los motores que tenga cada usuario, sin cambiar lo que ven hoy los usuarios de `e2`/`m15`/`m5`.
  - El signup sigue sembrando lo de producción; dar de alta usuarios del bot nuevo desde la app web queda para el reemplazo.
- Formulario de configuración del motor nuevo: en Fase 1 basta con mostrar/editar el JSON de `engine_config` de forma simple; el formulario real llega con la estrategia (Fase 5).
- La documentación del contrato bot ↔ app web (`docs/dashboard-handoff*.md`) se actualiza con el motor nuevo.

### 7.7 Orden del proyecto

- **`CLAUDE.md` nuevo** en la raíz: qué es el proyecto, fases, reglas de convivencia con producción, flujo git (ramas `fase1/...`, `datos/...`, PR, revisión, tags), dónde está la memoria y las skills, y la regla de mantenerlos al día. Reemplaza al borrado.
- **Memoria compartida** en `docs/memory/` (índice `MEMORY.md`), commiteada. Se mueve allí lo acordado en esta conversación (decisiones de fork, roles, flujo git).
- **Skills compartidas** en `.claude/skills/` del repo *(llamada de Claude: es la carpeta que Claude Code lee para skills de proyecto; Oscar la mencionó como `/claude/.skills`)*. Skills iniciales propuestas para Fase 1:
  - `lanzar-bot-nuevo`: arrancar/parar/reiniciar solo las instancias del bot nuevo, con chequeo de que producción no se toca.
  - `migracion-supabase-segura`: cómo escribir, revisar y aplicar una migración aditiva compartida.
  - `flujo-git-equipo`: crear rama, commit, PR, revisión, merge, tag.
  - `actualizar-memoria`: cuándo y cómo registrar decisiones en `docs/memory/` y `CLAUDE.md`.
  Más skills se proponen en cada fase cuando aparezca un trabajo repetido.
- **Documentación** en lenguaje sencillo: `README.md` (qué es, cómo se arranca), `docs/como-funciona.md` (piezas y cómo se hablan), `docs/convivencia-y-reemplazo.md` (cómo conviven con producción y cómo se hace el cambio y la vuelta atrás).
- **Protección de `main`** en GitHub (sin push directo, PR obligatorio). Configurarla es de Oscar/Daniel en GitHub; el documento de flujo lo explica.

### 7.8 Esqueleto del motor nuevo

Una clase que cumple la interfaz de `bot/main.py` (sección 2) y **no envía órdenes**: en cada `on_tick` solo registra que está viva; `close_all` cierra lo que tenga su magic (debería ser nada). Sirve para comprobar de punta a punta que lanzador → MT5 → Supabase/Postgres → app web funcionan antes de que exista estrategia.

## 8. Cómo se sabe que la Fase 1 está lista

1. El repo no contiene código de la estrategia vieja; arranca con dos motores esqueleto en la misma cuenta demo propia, en modo normal y `-Lite`, sin que se mezclen sus posiciones, logs ni estado.
2. Desde la app web, el bot nuevo aparece como un motor más: encender/apagar, cierre forzado y meta funcionan; heartbeat y estado se ven.
3. Mientras tanto, producción (e2) sigue corriendo sin interrupción: los 3 terminales y 6 procesos no se reinician, y `restart_all.ps1 -DryRun` de este repo lista cero procesos de producción.
4. Las migraciones nuevas son solo aditivas y están revisadas por Daniel.
5. Postgres local tiene base, esquema mínimo y migraciones versionadas.
6. Existen `CLAUDE.md`, memoria en `docs/memory/`, las cuatro skills iniciales y los tres documentos en lenguaje sencillo.
7. Todo entró a `main` por PR (este repo y el de la app web), y los usuarios actuales de producción ven el dashboard igual que antes.

## 9. Lo que queda a criterio del constructor

- Formato exacto del registro de motores (Python vs JSON), mientras PowerShell y Python lean la misma fuente.
- Estructura interna de carpetas del código nuevo y nombres de archivos.
- Esquema exacto de la base local mínima y herramienta de migraciones locales.
- Cómo se divide la Fase 1 en ramas/PRs (sugerencia: limpieza → registro/aislamiento → Supabase/Postgres → docs/skills).
- Si usar pytest para los tests nuevos de infraestructura (recomendado).
