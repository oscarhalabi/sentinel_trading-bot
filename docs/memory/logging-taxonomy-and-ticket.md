---
name: logging-taxonomy-and-ticket
description: Taxonomía de log_types de bot_logs, columna ticket, y dónde se loguea cada evento (aperturas en broker, cierres en el motor)
metadata:
  node_type: memory
  type: reference
---

Sistema de logging del bot (`motor/logger.py` multi-sink → consola + CSV + `bot_logs` DB + buffer TUI). Firma: `Logger.write(log_type, message, price, lots, balance, ticket=0)`; los sinks reciben `(log_type, message, ts, price, lots, balance, ticket)`.

> Actualizado en Fase 1 (2026-10-28): el `log_type` de una **apertura** ya no sale de una lista fija. Cada motor etiqueta la operación en el `comment` con el formato `ETIQUETA|texto`; `broker._open_log_type` toma la parte antes de `|`, la pasa a mayúsculas, deja solo `[A-Z0-9_]`, la corta a 20 caracteres y, si no hay etiqueta, usa `OPERACION`. Los motores nuevos (`n1`, `n2`) eligen sus propias etiquetas; la lista ENTRADA/PROTECCION/RECOVERY/RESCATE/GRINDER y los cierres HEALER/UNWIND son solo del bot viejo (exp-1). `bot_logs.log_type` y `bot_positions.op_type` son TEXT libre según los archivos SQL del repo (confirmado contra la base real el 2026-11-02, antes de aplicar `migrations/n1_001_engine_config.sql`).

**Columna `ticket`** (BIGINT, default 0) en `bot_logs` y en el CSV — commit del 2026-07-07. 0 = evento sin posición asociada. Aplicada a la DB viva con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

**Dónde se loguea cada evento:**
- **Aperturas** → centralizadas en `broker.market_order` (`_log_open`), no en el motor. El `log_type` es la etiqueta del `comment` (ver arriba). Órdenes rechazadas → `ERROR` con retcode (anti-spam: una línea por racha). El ticket = `res.order`.
- **Cierres** → los loguea el motor con su contexto y el ticket de la posición; puede añadir una traza por posición (`CIERRE`) y un resumen sin ticket.
- **Otros sin ticket:** `SYSTEM`, `SHADOW`.

**Caveat schema drift:** la tabla `bot_logs` VIVA tiene la columna de tiempo como `ts` (timestamptz, default now()), pero `schema.sql` la declara `created_at`. `db.insert_log` lista columnas explícitas y deja el timestamp por default, así que funciona con cualquiera de los dos nombres. Si algún día se re-despliega desde `schema.sql` habrá `created_at`; tenerlo presente al consultar. Un deploy fresco desde `schema.sql` ya incluye `ticket`.
