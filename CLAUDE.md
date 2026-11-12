# CLAUDE.md

Guía para Claude Code en este repositorio. Idioma: español sencillo, sin jerga
innecesaria. Las personas que trabajan aquí son **Oscar** (estrategia y análisis) y
**Daniel** (app web y servidor/VPS).

## Qué es este proyecto

Este repo es el fork `oscarhalabi/sentinel_trading-bot` (original:
`lozadaitm/sentinel_trading-bot`). Aquí se construye un **bot de trading nuevo**,
casi desde cero, que opera oro (XAU) en MetaTrader 5. De lo viejo (exp-1, exp-2, e2)
solo se conserva la infraestructura: lanzar instancias con un `.env`, un terminal MT5
por cuenta, el alta de cuentas y la app web con su Supabase.

La lógica de trading vieja ya no está en el repo (queda en el historial de git).
**No se usan las conclusiones, simulaciones ni investigaciones de exp-2**: estaban
sesgadas por las instrucciones de aquel repo. Todo lo de estrategia se investiga de cero.

### Las 5 fases

1. **Base del fork** (Fase 1): limpiar el repo, convivir con producción en el mismo
   VPS, dejar un esqueleto de motor que no opera, Postgres local, docs y skills.
2. **Datos históricos**: recolectar y guardar datos de mercado.
3. **Investigación**: SMC, order blocks, BOS, soportes/resistencias, velas, regímenes,
   riesgo, fundamentales, con agentes de IA.
4. **Laboratorio**: econometría, simulación y prueba masiva de variantes.
5. **Bot nuevo** que se adapta al régimen de mercado.

Cada fase se diseña (brainstorming) por separado. Fase 2: mapa en
`docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md` (subproyectos 2A–2E; memoria
`fase2-datos-decisiones`). Los datos y la simulación van en la laptop de Oscar, no en el VPS. Los diseños y planes están en
`docs/superpowers/specs/` y `docs/superpowers/plans/`.

## Cómo está armado (resumen)

- `motor/` : el bot. `motor/engines.json` es el **registro de motores** (cada motor tiene
  `bot_id`, clase, variable de su magic, magic por defecto). Hoy: `n1` y `n2`, ambos
  motores nulos (no operan). `python -m motor.run` arranca el motor de `BOT_ID`.
- `scripts/` : lanzar, reiniciar, vigilar, dar de alta cuentas (ver `docs/como-funciona.md`).
- `instances/<usuario>.env` : un archivo por usuario (no se commitea). Plantilla: `instances/example.env`.
- `migrations/` : SQL de Supabase (compartido con producción). Las nuestras: `n1_NNN_*.sql`.
- `localdb/migrations/` : SQL del Postgres local de este bot.
- `docs/` : documentación en lenguaje sencillo (`como-funciona.md`, `convivencia-y-reemplazo.md`).

## REGLAS DE CONVIVENCIA CON PRODUCCIÓN (no negociables)

Producción (el bot e2) corre en este mismo VPS desde otra carpeta. Hay que protegerla.

- **Nunca** modificar `C:\Users\Administrator\Documents\sentinel_trading` (producción),
  sus tareas `Sentinel*` ni las cuentas MT5 26329953/55/57.
- Procesos de producción vivos que nada de este repo puede matar: `bot.lite_e2` y
  `scripts.news_exporter` lanzados desde `...\sentinel_trading`, y los terminales bajo
  `C:\MT5_instances\`.
- Carpeta de clones del bot nuevo: `MT5_CLONES_DIR`, por defecto `C:\MT5_instances_n1`.
- `bot_id` nuevos distintos de `m15`, `m5`, `e2`. Motores iniciales: `n1` y `n2`
  (nulos). Magics por defecto: `MAGIC_N1=300100` y `MAGIC_N2=300200`.
- **Supabase compartido: solo cambios aditivos.** Migraciones del bot nuevo en
  `migrations/n1_NNN_<nombre>.sql`. Antes de escribir una se inspecciona el esquema
  real. Cada una se revisa con Daniel en el PR **antes** de aplicarla (usar la skill
  `migracion-supabase-segura`).
- Tareas programadas propias con prefijo `SentinelN1` (hoy: `SentinelN1Vigilante`).
- El bot nuevo **no lanza `news_exporter`**: lee el JSON de `NEWS_CALENDAR_PATH`, que
  ya escribe producción.
- Cada cuenta nueva es una cuenta **demo propia** y un usuario de Supabase **nuevo**
  (nunca uno que ya tenga filas `e2`/`m15`/`m5`). Se da de alta a mano con
  `python -m scripts.provision alta ...`, no con `provision_requests` (el provisionador
  de producción se llevaría la solicitud).
- Un reinicio de producción mata todos los `terminal64.exe` del servidor, también los
  nuestros. Por eso existe el vigilante (`SentinelN1Vigilante`) que los relanza.
- Documentación y comentarios en español sencillo.

Detalle y motivos en `docs/convivencia-y-reemplazo.md`.

## Flujo git del equipo

(Skill: `flujo-git-equipo`.)

- `main` es estable: **nunca push directo**. Un PR por tema.
- Ramas: `faseN/<tema>` (ej. `fase1/limpieza-multimotor`), `datos/<tema>` para trabajo de
  datos, `fix/<tema>` y `docs/<tema>` si hace falta.
- PR revisado por la otra persona (o primero revisión de Claude con `/code-review`).
  Lo de servidor/app web lleva una explicación clara de qué cambia en el servidor y por qué.
- Commits en español, formato `tipo(ámbito): mensaje` (`feat`, `fix`, `docs`, `refactor`,
  `test`, `chore`) y la línea `Co-Authored-By` que indique el entorno.
- **Identidad por commit.** El VPS no tiene identidad global de git. Cada commit lleva la
  de quien lo pide: `git -c user.name="Oscar" -c user.email="oscarhalabi@proton.me" commit ...`.
  Para Daniel, su nombre y correo.
- Tags en cada paso a producción: `vN-AAAA-MM-DD` (ej. `v1-2026-11-15`).
- Webapp (`lozadaitm/sentinel_trading-webapp`, clon en `C:\Users\Administrator\Documents\sentinel_trading-webapp`):
  rama + PR revisado por Daniel, probado en el preview de Vercel. Lo que ven hoy los
  usuarios `m15`/`m5` no debe cambiar.
- `main` del fork está protegido en GitHub (desde 2026-11-03): PR obligatorio, sin force-push ni
  borrado. Sin aprobación obligatoria mientras Daniel no sea colaborador del fork.
- No hacer push ni registrar tareas programadas sin que lo pida una persona.

## Memoria y skills

- **Memoria** en `docs/memory/` (fuente canónica, versionada). Índice: `docs/memory/MEMORY.md`
  (una línea por nota). Cada nota tiene frontmatter `name`, `description`, `metadata.type`.
  Léelo al empezar. El almacén local de Claude (`~/.claude/projects/.../memory/`) es solo
  un espejo por máquina; ante divergencia gana `docs/memory/`.
- **Skills** en `.claude/skills/<nombre>/SKILL.md`:
  `lanzar-bot-nuevo`, `migracion-supabase-segura`, `flujo-git-equipo`, `actualizar-memoria`.
  Si aparece un trabajo repetido, proponer crear una skill nueva.

## Reglas de mantenimiento

- **Al cerrar cada tarea relevante, actualizar memoria, CLAUDE.md y skills en el mismo PR.**
  (Skill `actualizar-memoria`.) Si un comando, script o nombre cambia, corregirlo en
  README, docs y skills en ese mismo PR.
- **No usar conclusiones de exp-2** (ni citarlas, ni construir sobre ellas).
- Las notas de `docs/memory/` marcadas como de producción (exp-1/e2) describen el bot
  viejo: sirven de referencia de infraestructura, no de estrategia.
- Los tests no tocan MT5 ni Supabase reales: `python -m pytest -q`. Nunca llamar a
  `mt5.initialize`/`order_send` reales en tests.
