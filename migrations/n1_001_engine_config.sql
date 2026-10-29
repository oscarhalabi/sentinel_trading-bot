-- ==================================================================
-- n1_001_engine_config.sql  |  Tabla engine_config para los motores nuevos
-- ==================================================================
-- QUE AGREGA
--   Una tabla nueva, public.engine_config: una fila por usuario y motor
--   (n1, n2, ...) con los parametros del motor guardados como JSON
--   ("params"). El bot nuevo lee de aqui en vez de bot_config.
--
-- POR QUE
--   bot_config tiene una columna por cada parametro de m15/m5/e2 y esta
--   compartida con produccion. Cada motor nuevo trae parametros distintos;
--   con un JSON no hay que tocar bot_config ni agregar columnas cada vez.
--
-- ES ADITIVA
--   Solo crea una tabla (y su seguridad). No cambia, borra ni renombra nada
--   de lo que usan m15, m5 o e2. Se puede correr mas de una vez sin dano.
--
-- NO APLICAR todavia: esta migracion NO se aplica hasta que Daniel apruebe
--   el PR. Se aplica a mano en el SQL editor de Supabase y se anota en
--   docs/memory/.
--
-- RESULTADO DE LA REVISION DEL ESQUEMA (solo leyendo archivos, sin tocar
-- Supabase): en supabase_schema.sql, migrations/002-009 de este repo y
-- migrations/010-017 de produccion NO hay ningun CHECK/enum/FK sobre
--   - bot_id (es TEXT libre en bot_instances, bot_state, bot_config,
--     bot_positions, bot_logs),
--   - op_type (TEXT libre, el comentario lista valores pero no hay CHECK),
--   - log_type (TEXT NOT NULL libre),
--   - provision_requests.bots (TEXT libre, default 'm15,m5'),
-- y la tabla engine_config no existia. Por eso 'n1'/'n2' y los tags libres
-- ya caben y esta migracion NO necesita ampliar ninguna restriccion. Esto
-- hay que CONFIRMAR contra la base real (ver el checklist).
--
-- ANTES DE APLICAR (consultas de solo lectura, en el SQL editor de Supabase)
--   1) Que engine_config no exista ya con otra forma:
--        SELECT column_name, data_type, is_nullable
--          FROM information_schema.columns
--         WHERE table_schema = 'public' AND table_name = 'engine_config';
--      (esperado: 0 filas)
--   2) Que no haya CHECK/FK/UNIQUE que bloqueen bot_id, op_type, log_type:
--        SELECT conrelid::regclass AS tabla, conname, contype,
--               pg_get_constraintdef(oid) AS definicion
--          FROM pg_constraint
--         WHERE conrelid IN ('public.bot_instances'::regclass,
--                            'public.bot_state'::regclass,
--                            'public.bot_config'::regclass,
--                            'public.bot_positions'::regclass,
--                            'public.bot_logs'::regclass,
--                            'public.provision_requests'::regclass)
--           AND contype IN ('c', 'f', 'u');
--      (esperado: ningun 'c' que mencione bot_id, op_type, log_type o bots)
--   3) Que esas columnas sean TEXT sin restriccion:
--        SELECT table_name, column_name, data_type
--          FROM information_schema.columns
--         WHERE table_schema = 'public'
--           AND column_name IN ('bot_id', 'op_type', 'log_type', 'bots');
--   4) Politicas actuales (para comparar con la que crea esta migracion):
--        SELECT tablename, policyname, cmd, roles, qual, with_check
--          FROM pg_policies
--         WHERE schemaname = 'public'
--         ORDER BY tablename, policyname;
-- ==================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.engine_config (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    bot_id     TEXT        NOT NULL,                 -- 'n1', 'n2', ...
    params     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, bot_id)
);

-- Seguridad por filas: el usuario solo ve y edita sus filas.
-- El service-role (el bot) se salta RLS automaticamente.
ALTER TABLE public.engine_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS engine_config_owner ON public.engine_config;
CREATE POLICY engine_config_owner ON public.engine_config
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

COMMIT;
