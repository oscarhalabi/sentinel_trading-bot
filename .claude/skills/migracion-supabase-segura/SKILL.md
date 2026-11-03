---
name: migracion-supabase-segura
description: Usar cuando haya que cambiar el esquema de Supabase (crear tabla, columna, política) para el bot nuevo; el Supabase es compartido con producción, así que solo se permiten cambios aditivos revisados por Daniel.
---

# Migración segura de Supabase (compartido con producción)

El Supabase lo usan también el bot de producción y la app web. Una migración mala puede
romperlos. Reglas:

1. **Solo aditivo**: crear tablas/columnas/índices nuevos. Nunca borrar, renombrar ni cambiar
   el significado de algo existente. Nunca tocar `bot_config`.
2. **Inspeccionar el esquema REAL** (y solo con la aprobación de Daniel) antes de escribir: producción ya aplicó migraciones 010-017
   que no están en este repo. Correr (solo lectura, SQL editor de Supabase) las consultas de la
   sección "ANTES DE APLICAR" de `migrations/n1_001_engine_config.sql` (columnas existentes,
   `pg_constraint`, tipos de `bot_id`/`op_type`/`log_type`/`bots`, `pg_policies`). Si hay un
   CHECK o enum que impida usar `n1`/`n2` o las etiquetas nuevas, ampliarlo de forma aditiva.
3. **Nombre**: `migrations/n1_NNN_<nombre>.sql` (prefijo `n1_`, numeración propia: la siguiente es
   `n1_002_...`). Cabecera en lenguaje sencillo: QUÉ agrega, POR QUÉ, que es aditiva, y el
   checklist "ANTES DE APLICAR". Usar `IF NOT EXISTS` / `DROP POLICY IF EXISTS` para poder
   repetirla sin daño, y `BEGIN; ... COMMIT;`.
4. **Seguridad por filas**: tabla nueva con `ENABLE ROW LEVEL SECURITY` y política por
   `user_id = auth.uid()` (el bot usa service-role y se salta RLS).
5. **PR y revisión con Daniel** antes de aplicar. En el PR: qué cambia, por qué, y el resultado
   de las consultas de inspección.
6. **Aplicar** solo tras su aprobación: pegar el archivo en el SQL editor de Supabase.
7. **Anotar en la memoria**: nota en `docs/memory/` (qué se aplicó, fecha, quién) y línea en
   `docs/memory/MEMORY.md`; ver skill `actualizar-memoria`. Marcar la cabecera del archivo como aplicada.

Estado actual: `migrations/n1_001_engine_config.sql` (tabla `engine_config`) está **aplicada**
(2026-11-02, por Daniel). La próxima migración del bot nuevo es `n1_002`.

Nota: la base local de Postgres (`localdb/migrations/NNN_*.sql`, se aplican con
`python -m scripts.localdb_migrate`) es otra cosa: no es compartida y no pasa por esta skill.
