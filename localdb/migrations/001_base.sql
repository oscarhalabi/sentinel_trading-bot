-- Base del Postgres local: control de migraciones y registro de eventos del motor.
create table if not exists schema_migrations (
    version    text primary key,
    applied_at timestamptz not null default now()
);

create table if not exists engine_events (
    id      bigserial primary key,
    ts      timestamptz not null default now(),
    user_id uuid,
    bot_id  text,
    kind    text,
    payload jsonb
);

create index if not exists engine_events_ts_idx on engine_events (ts);
