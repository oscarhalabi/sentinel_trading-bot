# Fase 2 — Datos históricos de mercado (diseño)

Fecha: 2026-11-12 · Autores: Oscar (estrategia/análisis), con Claude. Daniel revisa lo que toca el VPS.

## 1. Intención y por qué

Las Fases 3 (investigación) y 4 (laboratorio) necesitan **datos de mercado propios, completos y
confiables**, guardados de forma que se puedan consultar millones de filas en segundos. La Fase 2
construye esa base: qué se baja, de dónde, cómo se guarda, cómo se comprueba que está bien y cómo
se mantiene al día sola.

Se empieza por el oro (XAUUSD), pero **nada queda atado a un par**. Oscar quiere que la
investigación se haga en el mayor número de mercados posible para no depender de uno solo, así que
el universo inicial es amplio (sección 7.2) y añadir un símbolo es añadir una entrada en un registro.

La Fase 2 **no investiga ni decide estrategia**: no calcula indicadores, señales ni estadísticas
de trading. Solo deja los datos crudos, ordenados, verificados y con su procedencia.

## 2. Contexto (2026-11-12)

- Fase 1 cerrada (tag `fase1-base-2026-11-03`). El VPS corre producción (e2) y nuestros motores
  nulos `n1`/`n2`; reglas de convivencia en `CLAUDE.md` y `docs/convivencia-y-reemplazo.md`.
- Producción tiene su propia tarea `SentinelTickCollector`; **no se usa ni se toca**. El bot nuevo
  se arma sus datos desde cero.
- El `news_exporter` de producción lee el JSON público de Forex Factory, que **solo trae esta
  semana y la siguiente**, y escribe una versión recortada (sin dato real, previsión ni anterior).
- Oscar trabaja en un **PC con Windows con 1 TB o más de SSD**. Esa es la máquina de investigación.
- La cuenta actual es Vantage RAW 1:500. Las demos de Vantage caducan con el tiempo.

## 3. Decisiones de Oscar (brainstorming del 2026-11-12)

1. **Resolución:** se investigan todos los estilos, scalping incluido. Se guardan **ticks bid/ask**
   (con el spread real), y las velas M1 se calculan a partir de ellos.
2. **Fuentes:** **Vantage (MT5)** para lo reciente y todo lo que llegue de aquí en adelante (es la
   verdad de nuestro broker), y **Dukascopy** (gratis, ticks bid/ask) para la historia larga. Queda un
   **hueco preparado** para añadir más adelante futuros de pago (por ejemplo GC del CME, con volumen
   real) si la Fase 3 demuestra que hacen falta.
3. **Almacén:** archivos **Parquet** consultados con **DuckDB**; un **catálogo en Postgres**
   (qué hay, de dónde salió, huecos, corridas). Diseñado para muchos símbolos.
4. **Máquina:** la historia larga y el laboratorio viven en el **PC de Oscar (Windows)**. El VPS
   solo hace lo que exige MT5: capturar los datos de Vantage.
5. **Traspaso:** el VPS sube su captura a un **bucket en la nube (Cloudflare R2)**; el PC la baja.
   El bucket también es el respaldo de todo.
6. **Contexto, además del precio:** calendario económico histórico, macro diario (FRED),
   mercados relacionados y posicionamiento (COT de la CFTC y tenencias del ETF GLD).
7. **Universo:** lo más amplio posible (unos 30 símbolos, sección 7.2).
8. **Disco:** 1 TB o más de SSD en el PC.
9. **Calendario del pasado:** una **API de pago** (se elige en el plan tras probar 2 o 3 candidatas)
   y, desde ya, una **foto diaria del JSON de Forex Factory** para tener copia propia.
10. **Captura en el VPS:** un **clon MT5 propio solo para datos** con una **demo RAW nueva**, sin
    motores ni usuario de Supabase. Oscar quiere además saber qué tipo de cuenta conviene. Por eso
    se deja como opcional un segundo clon con una demo **Standard**, para medir los dos spreads reales
    (ver 7.10).

## 4. Objetivos

1. Tener en el PC, para cada símbolo del universo, **ticks bid/ask desde lo más antiguo disponible**
   (Dukascopy) y **ticks de Vantage** desde lo que dé su servidor en adelante, más velas M1 de las
   dos fuentes.
2. Tener los **datos de contexto** (calendario, macro, mercados relacionados, posicionamiento)
   alineados en UTC con los precios.
3. **Saber siempre qué hay y qué tan bueno es**: el catálogo dice qué días existen, de qué fuente,
   cuántas filas tienen, qué huecos hay y qué problemas de calidad se detectaron.
4. **Mantenerse al día solo**: el VPS captura Vantage todos los días; el PC completa todo lo demás
   con una tarea programada. Si algo se corta, la siguiente corrida rellena lo que falta.
5. **Consultas rápidas** para las Fases 3 y 4: vistas DuckDB listas (ticks, M1 y temporalidades
   mayores calculadas al vuelo) y una función Python sencilla para cargar un rango.
6. **Respaldo** fuera de las dos máquinas (R2), y poder reconstruir el PC desde cero.

## 5. No-objetivos (Fase 2)

- No se calculan indicadores, estructuras (BOS, order blocks...), regímenes ni señales: eso es Fase 3.
- No se hace backtesting ni simulación: eso es Fase 4.
- No se toca Supabase (ninguna migración). La Fase 2 no tiene nada que mostrar en la app web.
- No se compran datos de futuros todavía (solo queda el hueco preparado).
- No se cambia nada de producción ni de los motores `n1`/`n2`.

## 6. Anti-objetivos (sería un fracaso aunque "funcione")

- Que la captura del VPS frene, mate o confunda a producción o a nuestros motores (CPU, disco,
  red o terminales MT5).
- **Mezclar horas**: guardar la hora del servidor de Vantage como si fuera UTC, o equivocarse en el
  cambio de horario de verano. Eso corrompería en silencio cualquier estudio de sesiones o de noticias.
- Mezclar fuentes sin decirlo: que una vela no diga si viene de Vantage o de Dukascopy.
- Huecos silenciosos: días que faltan o que vienen a medias sin que el catálogo lo marque.
- Datos que solo se pueden leer con el código de quien los bajó (formato propio, rutas fijas).
- Claves de API o del bucket dentro del repo.

## 7. Diseño

### 7.1 Visión general

```
           VPS (Windows)                                  PC de Oscar (Windows)
  ┌──────────────────────────────┐                ┌──────────────────────────────────┐
  │ clon MT5 mt5_datos (demo RAW)│                │ Dukascopy → ticks históricos     │
  │   └─ python -m datos.capturar│   Cloudflare   │ FRED, CFTC, GLD, API calendario  │
  │      (tarea SentinelN1Datos) │──► R2 bucket ◄─┤ python -m datos.actualizar       │
  │ foto diaria Forex Factory    │   (respaldo)   │   (tarea SentinelDatosPC)        │
  │ Parquet local + subida       │                │ Parquet en DATA_DIR (SSD 1 TB)   │
  └──────────────────────────────┘                │ catálogo Postgres + DuckDB       │
                                                  │ → Fases 3 y 4                    │
                                                  └──────────────────────────────────┘
```

- Todo el código vive en un paquete nuevo **`datos/`** en este repo, separado de `motor/`. El motor
  no importa nada de `datos/` (y al revés), así un fallo en uno no afecta al otro.
- Dependencias propias en `requirements-datos.txt` (pyarrow, duckdb, requests, boto3), para no
  cargar el motor con librerías que no usa.
- Una sola CLI (`python -m datos <orden>`) con órdenes pequeñas: `bajar-dukascopy`, `capturar-mt5`,
  `contexto`, `velas`, `verificar`, `subir`, `traer`, `catalogo`, `actualizar` (corre todo lo de la
  máquina en orden).

### 7.2 Registro de símbolos (universo)

Un archivo **`datos/simbolos.json`** es la fuente única de qué se baja. Es la misma idea que
`motor/engines.json`. Cada entrada tiene:

- `simbolo`: nombre canónico propio (p. ej. `XAUUSD`, `US500`, `WTI`).
- `clase`: `metal`, `fx_mayor`, `fx_cruce`, `indice`, `energia`, `dolar`.
- `fuentes`: nombre en cada fuente (`dukascopy`: `XAUUSD`, `USA500IDXUSD`...; `vantage`:
  `XAUUSD+`...). Si una fuente no tiene el símbolo, no aparece.
- `ticks`: `true` o `false` (si se guardan ticks o solo M1). Por decisión de Oscar, `true` para todos.
- `desde`: fecha mínima a bajar (por defecto, lo más antiguo que tenga la fuente).
- `horario`: plantilla de horario de mercado (`fx`, `metal`, `indice_us`...) para detectar huecos.

Universo inicial (unos 30 símbolos; en el plan se verifica cuáles existen en cada fuente):

| Clase | Símbolos |
|---|---|
| Metales | XAUUSD, XAGUSD |
| FX mayores | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD |
| FX cruces | EURJPY, GBPJPY, EURGBP, EURCHF, AUDJPY, EURAUD, GBPCHF, CADJPY, AUDNZD, EURCAD, GBPAUD, NZDJPY |
| Índices | US500, US100, US30, DE40, UK100, JP225 |
| Energía | WTI, BRENT |
| Dólar | DXY (índice del dólar, si Dukascopy o Vantage lo tienen; si no, se reconstruye de los mayores) |

El VIX diario sale de FRED (`VIXCLS`), no de los brókeres.

### 7.3 Fuentes de precio

**Dukascopy (PC):** ticks bid/ask con volumen, por hora, en archivos públicos `.bi5` (LZMA). Gratis.
Historia desde ~2003 en FX y metales y menos en índices y energía (se verifica en el plan). Se baja
con un descargador propio (es un formato pequeño y conocido, sin depender de librerías de terceros
que se abandonan):

- Pide una hora a la vez, con **límite de velocidad** (p. ej. 4 peticiones en paralelo como máximo)
  y reintentos con espera creciente. Una hora vacía (fin de semana, feriado) es válida y se anota.
- Convierte los precios según los decimales de cada instrumento y guarda un archivo por día.
- Reanudable: lo que ya está en el catálogo como `ok` no se vuelve a bajar.

**Vantage MT5 (VPS):** `copy_ticks_range` (todos los ticks, bid/ask/last/flags) y `copy_rates_range`
(velas M1 del bróker, que llegan más atrás que los ticks).

- Primera corrida: retrocede día a día hasta que el servidor deja de dar datos. Así sabemos la
  profundidad real de Vantage.
- Después: cada corrida pide desde el último tick guardado, con 1 hora de solape, y quita duplicados.
- Se guarda también la **hora cruda del servidor**, además de la UTC (ver 7.6).

**Hueco para fuentes de pago (futuro):** cada fuente es una clase con la misma interfaz pequeña
(`listar_disponible`, `bajar_dia`, `esquema`). Añadir Databento, TickData u otra es una clase nueva
y una entrada en `fuentes` del registro, sin tocar el almacén ni el catálogo.

### 7.4 Fuentes de contexto (PC)

| Dato | Fuente | Frecuencia | Notas |
|---|---|---|---|
| Calendario económico histórico | API de pago (candidatas: Financial Modeling Prep, EODHD, Trading Economics) | diaria | Se elige en el plan con una prueba: profundidad, hora exacta, dato real/previsión/anterior, impacto, precio. Presupuesto orientativo: 20–30 USD/mes. |
| Calendario Forex Factory (foto) | JSON público `ff_calendar_thisweek.json` | diaria (VPS) | Una sola descarga al día, independiente del `news_exporter` de producción. Se guarda el JSON tal cual y una versión tabular. |
| Macro diario | FRED (API gratis con clave) | diaria | DGS10, DGS2, DFII10 (rendimiento real), T10YIE (inflación esperada), DFF/EFFR, DTWEXBGS (dólar amplio), VIXCLS, y las que pida la Fase 3. Se guardan también las **fechas de publicación** (vintages, ALFRED) para no usar en el laboratorio un dato antes de que existiera. |
| Mercados relacionados | Los mismos símbolos del universo (índices, energía, plata, dólar) | — | Ya están en 7.2. |
| COT | CFTC (archivos anuales gratis) | semanal | Informe "Disaggregated" y "Legacy" de oro, plata y los futuros de divisas. Se guarda la fecha del informe (martes) **y** la de publicación (viernes). |
| Tenencias ETF GLD | Web de SPDR (CSV histórico) | diaria | Toneladas y valor. Si la fuente cambia, se marca en el catálogo. |

### 7.5 Almacén: Parquet + DuckDB

Raíz configurable `DATA_DIR` (p. ej. `D:\sentinel_datos` en el PC y `C:\sentinel_datos_n1` en el
VPS). Estructura particionada estilo Hive, legible por cualquier herramienta (DuckDB, pandas,
Polars, Spark):

```
DATA_DIR/
  ticks/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-05-13.parquet
  ticks/fuente=vantage_raw/simbolo=XAUUSD/anio=2026/2026-11-11.parquet
  m1/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-05.parquet        (calculado de los ticks)
  m1/fuente=vantage_rates/simbolo=XAUUSD/anio=2019/2019-03.parquet    (velas del bróker)
  contexto/calendario/fuente=<api>/anio=2024.parquet
  contexto/calendario/fuente=forexfactory/crudo/2026-11-12.json
  contexto/fred/serie=DFII10.parquet
  contexto/cot/informe=disaggregated/anio=2024.parquet
  contexto/etf/gld.parquet
  _manifiestos/...    (un JSON por archivo: filas, rango, hash, versión de esquema, fuente)
```

**Esquema de ticks** (igual para todas las fuentes; las columnas que una fuente no trae van nulas):

| Columna | Tipo | Notas |
|---|---|---|
| `ts` | timestamp(µs, UTC) | Hora del tick en UTC. |
| `ts_servidor` | int64 (ms) | Solo Vantage: hora cruda del servidor MT5. |
| `bid`, `ask` | float64 | Precio. |
| `bid_vol`, `ask_vol` | float32 | Dukascopy: volumen ofrecido. |
| `last`, `vol` | float64 | Vantage: si el servidor los da. |
| `flags` | int32 | Vantage: banderas del tick MT5. |

**Esquema M1:** `ts` (apertura, UTC), `open/high/low/close` en bid, `ask_close`,
`spread_min/medio/max`, `ticks` (cantidad), `vol` si existe. Las temporalidades mayores (M5, M15,
H1, H4, D1) **no se guardan**: se calculan al vuelo desde M1 con DuckDB, que es muy rápido. Para
D1 hay dos cortes posibles: medianoche UTC y cierre de Nueva York, 17:00 hora de NY (el que usan
los brókeres).

- Compresión zstd. Un archivo de ticks por día y uno de M1 por mes: ni miles de archivos
  diminutos ni archivos gigantes.
- **Escritura atómica**: se escribe en un archivo temporal y se renombra. El manifiesto se escribe
  al final. Un archivo sin manifiesto se considera incompleto y se rehace.
- Los datos crudos **nunca se editan**. Si una fuente corrige algo, se baja de nuevo y el manifiesto
  guarda la versión anterior y la nueva.
- `datos/consulta.py`: abre una conexión DuckDB con vistas listas (`ticks`, `m1`, `velas(tf)`,
  `calendario`, `fred`...) y una función `cargar(simbolo, desde, hasta, tf, fuente)` que devuelve
  un DataFrame. Es la puerta de entrada para las Fases 3 y 4.

Tamaño estimado: unos 150 a 400 GB para unos 30 símbolos y unos 20 años de ticks. Cabe en el SSD del PC.

### 7.6 Tiempo: todo en UTC

- La regla es una sola: **todas las columnas `ts` están en UTC**.
- Dukascopy ya publica en UTC.
- El servidor de Vantage usa la hora del bróker (GMT+2 en invierno y GMT+3 en verano, que es el
  estilo de "cierre de Nueva York"). La conversión se hace con una regla explícita y probada, y la
  hora cruda se guarda en `ts_servidor`. **Se verifica con datos**: en el periodo que comparten,
  la correlación cruzada entre los precios de Vantage y los de Dukascopy debe dar desfase 0 en
  todas las semanas, también las semanas en que Europa y EE. UU. cambian de horario en fechas
  distintas. Si no da 0, la regla está mal y la corrida falla.
- El calendario, FRED y el COT se guardan con su hora y zona originales **y** en UTC.

### 7.7 Catálogo en Postgres

Base **`sentinel_datos`** (rol `sentinel_datos`) en el Postgres del PC. Se instala Postgres en el PC
si no está. Migraciones en `datos/migrations/NNN_*.sql`, aplicadas con
`python -m datos catalogo migrar`, con el mismo mecanismo de `scripts/localdb_migrate.py`. Tablas:

- `fuentes`: nombre, tipo (precio/contexto), notas, de pago o no.
- `simbolos`: espejo del registro (se sincroniza desde `simbolos.json`).
- `particiones`: una fila por archivo (fuente, símbolo, día o mes, ruta, filas, primer y último
  `ts`, hash, versión de esquema, estado `ok`/`vacio`/`incompleto`/`error`, fecha de descarga).
- `corridas`: cada ejecución (máquina, orden, inicio, fin, resultado, resumen).
- `problemas`: lo que encuentra la verificación (hueco, duplicados, spread negativo, salto de
  precio, desfase de hora...), con símbolo, rango y gravedad.

El catálogo **se puede reconstruir** leyendo los manifiestos (`python -m datos catalogo reconstruir`):
la verdad son los archivos y sus manifiestos, no la base. En el VPS no hace falta catálogo
Postgres: la captura usa los manifiestos para saber dónde quedó. Así no se toca el Postgres del bot.

### 7.8 Calidad: cómo sabemos que los datos están bien

`python -m datos verificar` corre después de cada actualización y escribe en `problemas`:

- **Huecos**: compara las horas con ticks con el horario esperado de cada plantilla (fin de semana,
  pausa diaria de metales e índices, feriados conocidos). Un hueco largo en horario abierto es un
  problema.
- **Coherencia**: `bid <= ask`, `ts` creciente, sin duplicados exactos, sin precios cero.
- **Saltos**: movimientos que se revierten enseguida y superan N veces la volatilidad del momento
  (ticks basura). Se marcan, no se borran.
- **Conteo**: ticks por día comparados con la mediana del símbolo. Un día con el 10 % de lo normal
  se marca como sospechoso.
- **Cruce entre fuentes** (Vantage contra Dukascopy, en el periodo común): desfase de hora (debe
  ser 0), diferencia del precio medio y spread de cada uno. Con esto se mide cuánto se parece
  Dukascopy a nuestro bróker y si sirve como sustituto en los años sin datos de Vantage.
- Un **informe corto** (texto o HTML) por corrida: qué se bajó, qué falta y qué problemas nuevos hay.

### 7.9 Captura en el VPS (sin tocar producción)

- **Clon MT5 propio solo para datos:** `C:\MT5_instances_n1\mt5_datos`, con una demo RAW nueva de
  Vantage. No tiene motores, ni usuario de Supabase, ni `.env` en `instances/`. Se clona con la
  misma rutina de `scripts/provision.py` (perfil IPC incluido), en un modo nuevo `clon-datos` que
  no escribe nada en Supabase.
- El proceso es `python -m datos capturar-mt5`, lanzado por una tarea programada propia
  **`SentinelN1Datos`** (cada hora). Pasos: conecta al clon con `mt5.initialize(path=...)`, baja lo
  nuevo, escribe Parquet y manifiestos, sube a R2 y se desconecta. **No se queda corriendo**, así
  que un reinicio de producción que mate los terminales no le afecta: la corrida siguiente vuelve a
  abrir el terminal.
- Guardas, reutilizando las de la Fase 1: `registry.validate_mt5_target` (nunca un terminal de
  `C:\MT5_instances` ni las cuentas 26329953/55/57) y la ruta del clon bajo `MT5_CLONES_DIR`. El
  vigilante y `restart_motor.ps1` lo reconocen como nuestro por la carpeta de clones.
- Cuidado con los recursos: prioridad baja de proceso, tope de días por corrida en el relleno
  inicial (para no tener el terminal ocupado horas) y aviso si el disco libre baja de un umbral.
- La foto diaria de Forex Factory se baja en la misma tarea, una vez al día.
- Todo esto requiere que **Daniel** cree la demo y que una persona pida registrar la tarea.

### 7.10 ¿Qué tipo de cuenta conviene? (RAW o Standard)

RAW no es un costo fijo de una vez: cobra una **comisión por lote** operado (en Vantage, unos
3 USD por lado y lote; se confirma en el plan) a cambio de un spread casi nulo. Standard no cobra
comisión, pero tiene un spread más ancho. Cuál conviene depende de la estrategia (frecuencia,
tamaño, horario), que aún no existe. Por eso no se decide ahora: **se mide**.

- El clon principal captura con RAW.
- **Opcional (recomendado):** un segundo clon `mt5_datos_std` con una demo Standard captura los
  mismos símbolos (fuente `vantage_std`). Con los dos spreads reales y las comisiones anotadas en
  `fuentes`, la Fase 4 calcula el costo por operación de cada tipo de cuenta para cada variante de
  estrategia.
- Lo mismo vale para otros brókeres en el futuro: es otra fuente más.

### 7.11 Bucket en la nube (Cloudflare R2)

- Un bucket privado (p. ej. `sentinel-datos`) con la misma estructura de carpetas que `DATA_DIR`.
- El VPS **sube** lo suyo (`vantage_*`, foto de Forex Factory) con una clave que solo puede
  escribir en esas carpetas. El PC **sube** lo suyo (Dukascopy, contexto) y **baja** lo del VPS.
- `datos subir` y `datos traer` comparan manifiestos (hash), así que solo se transfiere lo nuevo o
  lo cambiado.
- Costo: R2 cobra unos 0,015 USD por GB y mes, sin cargo por descarga. Con 400 GB, unos 6 USD/mes.
  Se respalda **todo**, también lo de Dukascopy: volver a bajarlo llevaría días.
- Las claves (R2, FRED, API de calendario) van en el `.env` de cada máquina, nunca en el repo.

### 7.12 Mantenimiento al día

| Máquina | Tarea | Cuándo | Qué hace |
|---|---|---|---|
| VPS | `SentinelN1Datos` | cada hora | Captura Vantage (ticks + M1), foto FF una vez al día, sube a R2. |
| PC | `SentinelDatosPC` | diaria (y al encender si se saltó) | Trae lo del VPS, Dukascopy hasta ayer, FRED, COT (los viernes), GLD, calendario, rehace M1, verifica e informa. |

Cada orden es **idempotente** (correrla dos veces da lo mismo) y **rellena huecos**: si el PC
estuvo apagado una semana, la siguiente corrida baja esa semana. El relleno inicial (20 años ×
30 símbolos en Dukascopy) se lanza a mano por tandas y puede tardar varios días; se puede parar
y seguir cuando se quiera.

### 7.13 Seguridad y convivencia

- Nada de la Fase 2 escribe en Supabase.
- En el VPS solo se añaden un clon en `C:\MT5_instances_n1`, una carpeta `DATA_DIR` y la tarea
  `SentinelN1Datos`. No se toca `C:\Users\Administrator\Documents\sentinel_trading`, ni sus
  tareas, ni `C:\MT5_instances`.
- Los tests no tocan red, MT5, R2 ni bases reales: usan archivos de ejemplo pequeños (un `.bi5`
  armado en el test, JSON de FRED o CFTC de muestra, un MT5 falso).

## 8. Cómo se sabe que la Fase 2 está lista

1. En el PC hay ticks de Dukascopy de todo el universo disponible, desde lo más antiguo, y M1
   calculado. El catálogo lo lista sin particiones en estado `incompleto` o `error`.
2. En el PC hay ticks y M1 de Vantage RAW hasta como mucho 1 o 2 horas atrás, traídos desde el VPS
   por R2, con la profundidad real del servidor medida y anotada.
3. El cruce Vantage contra Dukascopy da **desfase de hora 0** en todas las semanas comunes, y su
   informe dice cuánto se parecen precio y spread.
4. Calendario histórico (API elegida), foto diaria de FF, FRED con fechas de publicación, COT y GLD
   están guardados y consultables en UTC.
5. `datos.consulta.cargar("XAUUSD", ..., tf="H1")` y las vistas DuckDB funcionan, y una consulta de
   un año de M1 de oro tarda pocos segundos.
6. Las dos tareas corrieron solas al menos una semana sin intervención, y el informe de calidad no
   tiene problemas graves sin explicar.
7. Producción intacta: mismos PIDs antes y después de instalar la tarea del VPS, y
   `restart_motor.ps1 -DryRun` sigue listando cero procesos de producción.
8. Hay una restauración probada: el PC se puede reconstruir desde R2 y el catálogo desde los manifiestos.
9. Docs, memoria y skills actualizadas. Skill nueva **`datos-mercado`**: cómo añadir un símbolo o
   una fuente, cómo relanzar un relleno y cómo leer el informe de calidad.

## 9. Riesgos y preguntas abiertas

- **Profundidad de Vantage:** no sabemos cuántos meses de ticks da su servidor. Se mide en la
  primera corrida y se anota.
- **Demos que caducan:** si la demo de datos caduca, la captura se corta. El informe diario avisa
  si no hay ticks nuevos, y cambiar de demo es cambiar el login del clon. La fuente sigue siendo
  `vantage_raw`: el precio del bróker no depende de la cuenta.
- **Condiciones de uso:** Dukascopy permite descargar sus datos históricos para uso propio. Se
  respetan límites de velocidad y no se redistribuyen. Forex Factory: solo el JSON público, una
  vez al día.
- **API de calendario:** cuál y cuánto cuesta se decide en el plan (Task 9) con una prueba real.
  La compra la aprueba Oscar.
- **Futuros de pago (GC, SI, 6E...):** fuera de la Fase 2. La Fase 3 dirá si el volumen real hace falta.
- **Python en el PC:** misma versión que en el VPS (3.14), si hay wheels de pyarrow y duckdb. Si
  no, la última versión que las tenga. Se fija en el plan.

## 10. Lo que queda a criterio del constructor

- Nombres internos de módulos y funciones dentro de `datos/`.
- Número exacto de peticiones en paralelo, tamaño de las tandas y umbrales de calidad (se ajustan
  con los primeros datos y se anotan en la memoria).
- Herramienta de subida a R2 (boto3 o rclone), siempre que compare por hash y no suba claves.
