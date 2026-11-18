-- Catálogo de datos de mercado: qué existe, qué se bajó y qué problemas hay.
-- Se puede reconstruir desde los manifiestos JSON (particiones e inicios).

create table if not exists fuentes (
    nombre   text primary key,
    tipo     text,
    de_pago  boolean not null default false,
    notas    text
);

create table if not exists simbolos (
    simbolo     text primary key,
    clase       text,
    prioridad   int,
    activo      boolean not null default true,
    datos       jsonb,
    actualizado timestamptz not null default now()
);

create table if not exists inicios (
    simbolo text not null,
    fuente  text not null,
    inicio  date not null,
    primary key (simbolo, fuente)
);

create table if not exists particiones (
    tipo         text not null,
    fuente       text not null,
    simbolo      text not null,
    periodo      text not null,
    ruta         text,
    estado       text not null,
    filas        bigint,
    ts_min       timestamptz,
    ts_max       timestamptz,
    sha256       text,
    intentos     int not null default 0,
    ultimo_error text,
    actualizado  timestamptz not null default now(),
    primary key (tipo, fuente, simbolo, periodo)
);

create table if not exists corridas (
    id        bigserial primary key,
    orden     text not null,
    inicio    timestamptz not null default now(),
    fin       timestamptz,
    resultado text,
    resumen   jsonb
);

create table if not exists problemas (
    id        bigserial primary key,
    tipo      text not null,
    simbolo   text not null,
    fuente    text not null,
    desde     timestamptz not null,
    hasta     timestamptz,
    gravedad  text,
    detalle   jsonb,
    revisado  boolean not null default false,
    creado    timestamptz not null default now(),
    unique (tipo, simbolo, fuente, desde)
);

create table if not exists accesos_reserva (
    id      bigserial primary key,
    momento timestamptz not null,
    quien   text not null,
    simbolo text not null,
    desde   timestamptz,
    hasta   timestamptz,
    unique (momento, quien, simbolo)
);
