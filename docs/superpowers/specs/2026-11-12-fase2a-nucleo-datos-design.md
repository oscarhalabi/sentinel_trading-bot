# Fase 2A — Núcleo de datos de mercado (diseño)

Fecha: 2026-11-12 · Autores: Oscar (estrategia/análisis), con Claude (brainstorming con superpowers).

Forma parte de la Fase 2. Mapa completo de la fase (2A–2E y notas para las Fases 3–5):
`docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md`.

## 1. Para qué

Dejar en la **laptop de investigación de Oscar** (Windows, SSD de 1 TB o más, Python y Postgres
ya instalados, repo clonado en `C:\Users\oscar\code\sentinel_trading-bot`) una base de datos de
mercado propia: **ticks bid/ask de todo lo que ofrece Dukascopy**, velas M1 calculadas a partir de
ellos, un catálogo que dice qué hay y qué falta, una verificación de calidad y una consulta
rápida que las Fases 3 y 4 usan **sin poder mirar el futuro**.

2A es útil por sí sola: con ella ya se puede empezar a investigar. Vantage (2B), el contexto (2C),
el mantenimiento automático (2D) y el libro de órdenes (2E) se apoyan en ella.

## 2. Lo que dijo Oscar (brainstorming del 2026-11-12)

- Se investigan todos los estilos, scalping incluido: hay que guardar **ticks**.
- Universo "**todo lo posible**": divisas, metales, índices, energía, materias primas, **acciones**
  (NVDA y demás), ETFs y criptomonedas. **Ticks para todo**, también para lo que hoy no ofrece
  Vantage, por si lo incluye más adelante o se cambia de bróker. Si falta espacio, se mueve el
  almacenamiento más adelante.
- Almacén: Parquet + DuckDB, con el catálogo en Postgres.
- La simulación **no puede ver el futuro**: en el instante t solo usa t y lo anterior.
- Reserva de los **últimos 12 meses con corte móvil** (opción A).
- Descargador **propio** (enfoque A), frente a una librería de terceros o comprar un histórico.

## 3. No-objetivos de 2A

- Nada de Vantage, R2 ni el VPS (eso es 2B). Nada de calendario, FRED, COT, GLD ni splits (2C).
  Nada de tareas programadas (2D) ni libro de órdenes (2E).
- No se calculan indicadores ni señales (Fase 3). No se simula (Fase 4).
- No se toca Supabase, ni producción, ni `motor/`.

## 4. Estructura

Paquete nuevo `datos/` en el repo. `motor/` nunca importa `datos/`, así un fallo en los datos no
puede tumbar al bot. `datos/` solo reutiliza dos funciones puras ya existentes:
`motor.registry.read_env_file` (lector de `.env`) y `scripts.localdb_migrate.apply_pending` (migraciones). Cada pieza hace una sola cosa:

| Pieza | Qué hace | Depende de |
|---|---|---|
| `datos/config.py` | Lee `DATA_DIR`, `DATOS_PG_DSN`, `DATOS_DISCO_MIN_GB`, `DATOS_CORTE_RESERVA` del entorno o del `.env` de la raíz (con `registry.read_env_file`) | — |
| `datos/simbolos.json` + `datos/simbolos.py` | Registro del universo; carga y valida | — |
| `datos/fuentes/base.py` | Interfaz `Fuente`: `nombre`, `listar_instrumentos()`, `bajar_dia(simbolo, fecha) -> ResultadoDia` | — |
| `datos/fuentes/dukascopy.py` | Implementación para Dukascopy. Solo sabe bajar y convertir; no sabe dónde se guarda | red |
| `datos/almacen.py` | **Única pieza que escribe archivos de datos**: Parquet + manifiesto, escritura segura | — |
| `datos/catalogo.py` | Catálogo Postgres; reconstruible desde los manifiestos | `almacen` |
| `datos/descarga.py` | Orquesta: días pendientes → fuente → almacén → catálogo | las anteriores |
| `datos/velas.py` | M1 a partir de los ticks guardados | `almacen` |
| `datos/calidad.py` + `datos/horarios.py` | Verificación e informe; plantillas de horario de mercado | `almacen`, `catalogo` |
| `datos/consulta.py` | Lectura con DuckDB directamente sobre los Parquet: temporalidades, varios mercados, reserva, `ts_cierre` | solo los archivos |
| `datos/__main__.py` | CLI `python -m datos <orden>` | todo |

Dependencias nuevas en `requirements-datos.txt` (pyarrow, duckdb, requests, pandas). El bot no
las necesita.

**Flujo:**

```
python -m datos simbolos descubrir → registro (simbolos.json)
python -m datos bajar              → descarga ─► fuente ─► almacen ─► catalogo
python -m datos velas              → ticks ─► M1
python -m datos verificar          → problemas + informe
Fases 3/4: datos.consulta          → lee los Parquet (no necesita Postgres)
```

## 5. Registro de símbolos

`python -m datos simbolos descubrir` lee la lista oficial de instrumentos de Dukascopy (la que
usa su web de datos históricos; la URL exacta se fija en el plan) y crea o actualiza
`datos/simbolos.json`. Antes de escribir, muestra los símbolos nuevos y los que desaparecieron.
Un campo ajustado a mano (por ejemplo `prioridad`) no se pisa al volver a descubrir. Un símbolo
que desaparece de la fuente queda con `activo: false`; nunca se borra.

Campos de cada símbolo:

| Campo | Ejemplo | Para qué |
|---|---|---|
| `simbolo` | `XAUUSD`, `NVDA.US`, `US500`, `BTCUSD` | Nombre propio y neutro; las acciones llevan el país |
| `clase` | `metal`, `fx_mayor`, `fx_cruce`, `fx_exotico`, `indice`, `energia`, `materia_prima`, `accion`, `etf`, `cripto` | Agrupar en los análisis entre mercados |
| `prioridad` | `1` / `2` | Orden de descarga (1 = lo que se puede operar en Vantage) |
| `fuentes` | `{"dukascopy": "NVDAUSUSD"}` | Nombre en cada fuente |
| `decimales` | `3` | Convertir los precios enteros de Dukascopy |
| `horario` | `fx`, `metal`, `indice_us`, `indice_eu`, `accion_us`, `accion_eu`, `energia`, `cripto_24x7` | Distinguir huecos normales de problemas |
| `divisa` | `USD` | Comparar mercados |
| `activo` | `true` | `false` deja de bajarlo sin borrar lo guardado |
| `ajustado_a_mano` | `["prioridad"]` | Campos que `descubrir` no debe pisar |

**Prioridad 1 inicial** (se confirma en 2B con lo que Vantage ofrece de verdad): metales,
divisas mayores y cruces, índices principales, WTI y Brent, BTC y ETH, y unas 10 acciones muy
líquidas (NVDA, AAPL, MSFT, AMZN, TSLA, META, GOOGL...). Todo lo demás, prioridad 2, también con
ticks.

`simbolos.py::cargar()` valida: nombres únicos, `clase` y `horario` conocidos, `decimales`
entre 0 y 8, fuente registrada. Un registro inválido aborta antes de bajar nada.

## 6. Almacén

**Raíz:** `DATA_DIR` (p. ej. `C:\sentinel_datos`). Todas las rutas internas, también las de los
manifiestos, son relativas a la raíz: mover los datos a otro disco es mover la carpeta y cambiar
una variable.

```
DATA_DIR/
  ticks/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-05-13.parquet
  m1/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-05.parquet
  _manifiestos/<misma ruta>.json
  _informes/AAAA-MM-DD.html
  _registro/reserva.jsonl
```

Particiones estilo Hive: las lee cualquier herramienta (DuckDB, pandas, Polars) sin nuestro código.

**Ticks** (un archivo por día UTC):

| Columna | Tipo | Nota |
|---|---|---|
| `ts` | timestamp µs, UTC | Un `ts` sin zona se rechaza al escribir |
| `bid`, `ask` | float64 | |
| `bid_vol`, `ask_vol` | float32 | Volumen ofrecido según Dukascopy (no es volumen real de mercado) |

Cada fuente puede añadir columnas propias (Vantage las tendrá en 2B). Las columnas de arriba son
las comunes y las que la consulta siempre ofrece.

**M1** (un archivo por mes), a partir de los ticks:

| Columna | Nota |
|---|---|
| `ts` | Apertura del minuto, UTC |
| `open`, `high`, `low`, `close` | Sobre bid |
| `ask_close` | Para simular compras con el precio real |
| `spread_min`, `spread_medio`, `spread_max` | `ask - bid` dentro del minuto |
| `ticks`, `bid_vol`, `ask_vol` | Actividad |

Un minuto sin ticks **no existe** (no se inventan velas). `ts_cierre` no se guarda: la consulta
lo añade.

**Manifiesto** (un JSON por archivo): ruta relativa, filas, `ts` mínimo y máximo, sha256, versión
de esquema, fuente, fecha de descarga, commit del código y, si el archivo se rehízo, los hashes
anteriores. Un día sin mercado tiene manifiesto `vacio` y ningún Parquet.

**Escritura segura:** se escribe `*.tmp`, se renombra y se escribe el manifiesto al final. Un
Parquet sin manifiesto, o con un hash distinto, es `incompleto` y se rehace. Los datos crudos no
se editan nunca. Compresión zstd.

**Disco:** antes de cada día descargado se comprueba el espacio libre. Por debajo de
`DATOS_DISCO_MIN_GB` (50 por defecto) se para, se anota y el informe lo avisa.

**Estimación** (se mide en la primera tanda y se anota): prioridad 1, ~150–250 GB; todo,
~300–600 GB.

## 7. Descargador de Dukascopy

- **Origen:** `https://datafeed.dukascopy.com/datafeed/<INSTRUMENTO>/<AAAA>/<MM>/<DD>/<HH>h_ticks.bi5`,
  donde **el mes va de 00 a 11**. Las horas están en UTC.
- **Formato `.bi5`:** LZMA. Registros de 20 bytes big-endian: `uint32` ms desde el inicio de la
  hora, `uint32` ask, `uint32` bid, `float32` ask_vol, `float32` bid_vol. Precio = entero / 10^`decimales`.
  Un archivo vacío es una hora sin ticks: es válido.
- **Un día = 24 peticiones** (una por hora de datos, hechas seguidas). Como máximo 4 en paralelo
  (`DUKAS_MAX_PARALELO`) y con un tope de peticiones por segundo. Reintentos con espera
  creciente, más larga ante 429/503. Cliente HTTP inyectable (en los tests, uno falso). User-Agent
  genérico.
- **Un día queda `ok` solo si respondieron sus 24 horas** (con datos o vacías). Si una hora
  falla tras los reintentos, el día queda `error` y se reintenta en la siguiente corrida; nunca
  se guarda un día con horas faltantes como completo.
- **Orden:** prioridad 1 y después 2. Dentro de cada símbolo, **de hoy hacia atrás**. Hacia atrás
  se para tras **8 semanas seguidas sin datos** y anota el inicio real del símbolo en el catálogo.
- **Comprobación de escala:** en el primer día de cada símbolo se revisa que bid ≤ ask y que el
  precio tenga un orden de magnitud razonable para su clase. Si no, se para ese símbolo con un
  error claro (casi siempre son `decimales` mal puestos).
- **Uso:**
  ```
  python -m datos bajar                                   # todo lo pendiente, por prioridad
  python -m datos bajar --simbolo XAUUSD --desde 2024-01-01 --hasta 2024-01-31
  python -m datos bajar --prioridad 1 --max-horas 6       # tandas acotadas
  ```
  Ctrl+C se puede usar en cualquier momento: lo guardado queda bien y la siguiente corrida sigue
  donde quedó.
- **Tiempos esperados** (se miden y se anotan): unos 10–20 archivos/segundo, es decir, 2–3 h por
  símbolo con 20 años; 4–7 días para la prioridad 1 y unas semanas para todo.

## 8. Catálogo (Postgres `sentinel_datos`, en la laptop)

Migraciones en `datos/migrations/NNN_*.sql`, aplicadas con `python -m datos catalogo migrar`
(mismo mecanismo que `scripts/localdb_migrate.py`, con otro directorio y otra variable de DSN).

| Tabla | Contenido |
|---|---|
| `fuentes` | Nombre, tipo, de pago o no, notas |
| `simbolos` | Espejo de `simbolos.json` + inicio real detectado por fuente |
| `particiones` | Una fila por archivo: tipo, fuente, símbolo, fecha, ruta, filas, `ts` mínimo y máximo, hash, estado (`ok`/`vacio`/`incompleto`/`error`), intentos, último error |
| `corridas` | Cada ejecución: orden, inicio, fin, resultado, resumen |
| `problemas` | Lo que encuentra la verificación: tipo, símbolo, rango, gravedad, si está revisado |
| `accesos_reserva` | Importado de `_registro/reserva.jsonl` |

`python -m datos catalogo reconstruir` rehace `particiones` y `simbolos` (inicio real) leyendo los
manifiestos. Las `corridas` antiguas se pierden en una reconstrucción, y eso es aceptable.

## 9. Velas M1

`python -m datos velas [--simbolo] [--desde]` calcula M1 por mes con DuckDB a partir de los
ticks. Solo rehace los meses cuyos ticks cambiaron (compara los hashes de los manifiestos de
origen, que quedan anotados en el manifiesto de la M1).

## 10. Verificación

`python -m datos verificar [--desde] [--simbolo]` anota en `problemas` (sin duplicar) y genera
`_informes/AAAA-MM-DD.html`:

| Chequeo | Qué marca |
|---|---|
| Coherencia | bid > ask, `ts` desordenado, duplicados exactos, precios ≤ 0 |
| Huecos | Horas sin ticks dentro del horario abierto de su plantilla (los fines de semana y los feriados fijos conocidos no cuentan) |
| Conteo | Días con menos del 20 % de la mediana de ticks del símbolo |
| Saltos | Picos que se revierten en pocos ticks y superan 8 veces la volatilidad del momento (umbral ajustable). Se marcan, **no se borran** |
| Posible split | Salto nocturno cercano a una razón 2, 3, 4, 5, 10 (o su inversa) en acciones y ETFs |

El informe dice qué se bajó, qué falta, cuánto ocupa por clase y qué problemas nuevos hay.
Los umbrales se ajustan con los primeros datos y se anotan en la memoria.

## 11. Consulta (puerta de entrada de las Fases 3 y 4)

`datos/consulta.py` funciona **solo con los archivos** (si Postgres no está, la investigación
sigue):

- `conectar() -> duckdb.Connection` con vistas `ticks` y `m1` sobre `DATA_DIR`.
- `cargar(simbolo, desde, hasta, tf="m1", fuente="dukascopy", incluir_reservado=False) -> DataFrame`
  con `ts`, `ts_cierre` y las columnas de M1. `tf` en `m1, m5, m15, m30, h1, h4, d1`, calculadas al
  vuelo desde M1. D1 corta a las **17:00 de Nueva York** por defecto (`corte_diario="utc"` como
  opción).
- `ticks(simbolo, desde, hasta, fuente="dukascopy", incluir_reservado=False) -> DataFrame`.
- `cargar_varios(simbolos, desde, hasta, tf, rellenar=None)`: varios mercados alineados por
  `ts_cierre`. Por defecto no rellena. `rellenar="ultimo_conocido"` usa el último valor cuyo
  `ts_cierre` sea ≤ t: nunca un dato posterior.
- **Sin ver el futuro:** toda vela lleva `ts_cierre`, y las uniones entre mercados se hacen
  sobre `ts_cierre`. Una vela de H1 de las 10:00 solo está disponible a las 11:00.
- **Reserva:** el corte es `hoy − 12 meses`, o la fecha de `DATOS_CORTE_RESERVA` si está puesta
  (se pone cuando empiece la Fase 4). Sin `incluir_reservado=True` no se devuelve nada posterior
  al corte. Cada acceso con `True` se anota en `_registro/reserva.jsonl` (fecha, quién llamó,
  símbolo y rango) y el catálogo lo importa.

## 12. Errores

- Un símbolo o un día que falla no para a los demás; queda en `particiones` (estado `error`) y
  en `corridas`. Al final, la orden sale con código distinto de 0 si algo falló.
- Mensajes claros en español; DSN y claves nunca se escriben en logs ni en informes.
- Un registro de símbolos inválido, un `DATA_DIR` inexistente o un disco por debajo del mínimo
  abortan antes de descargar nada.

## 13. Tests

`python -m pytest -q`. Sin red, sin Dukascopy real y sin Postgres real; los tests de base real se
saltan si falta `DATOS_PG_DSN_TEST`. El `.bi5` de prueba lo arma el propio test con `lzma` y
`struct`, y la fuente usa un cliente HTTP falso. Cada test usa una carpeta temporal como
`DATA_DIR`.

Tests clave:
- `test_parsea_bi5`, `test_url_mes_de_00_a_11`, `test_hora_vacia_es_valida`
- `test_dia_con_hora_fallida_queda_error`, `test_reanuda_sin_duplicar`
- `test_escala_absurda_para_el_simbolo`
- `test_escritura_interrumpida_se_rehace`, `test_ts_sin_zona_se_rechaza`
- `test_descubrir_no_pisa_ajustes_manuales`, `test_registro_invalido_aborta`
- `test_m1_ohlc_y_spread`, `test_minuto_sin_ticks_no_existe`
- `test_d1_corte_nueva_york_en_cambio_de_horario`
- `test_reserva_bloquea_por_defecto`, `test_acceso_a_reserva_queda_registrado`
- `test_cargar_varios_nunca_usa_dato_posterior`
- `test_catalogo_se_reconstruye_desde_manifiestos` (con base real)
- `test_motor_no_importa_datos`

## 14. Cuándo está lista 2A

1. Los tests pasan en Linux (aquí) y en Windows (laptop).
2. Un día real de XAUUSD bajado en la laptop coincide con un gráfico de referencia.
3. La prioridad 1 completa está bajada, con su inicio real por símbolo anotado.
4. `cargar("XAUUSD", <un año>, tf="h1")` tarda pocos segundos.
5. El informe de verificación no tiene problemas graves sin explicar.
6. `catalogo reconstruir` en una base vacía da los mismos conteos.
7. Memoria y docs actualizadas (`docs/datos-de-mercado.md` con la guía sencilla) y skill nueva
   `datos-mercado` (añadir un símbolo, relanzar una descarga, leer el informe).

La prioridad 2 puede seguir bajando después de cerrar 2A: no la bloquea.
