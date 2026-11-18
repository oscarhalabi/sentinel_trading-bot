# Fase 2C — Datos de contexto (diseño)

Fecha: 2026-11-18 · Autores: Oscar (estrategia/análisis), con Claude (brainstorming con superpowers).

Forma parte de la Fase 2. Mapa: `docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md`.
Se apoya en 2A (`docs/superpowers/specs/2026-11-12-fase2a-nucleo-datos-design.md`), ya en `main`:
almacén Parquet con manifiestos, catálogo, consulta sin futuro con reserva, verificación.

## 1. Para qué

Guardar, junto a los precios de 2A y en la laptop de Oscar, el **contexto que mueve los mercados**:
calendario económico, macro, posicionamiento y eventos corporativos. La Fase 3 lo usará para tres
cosas, las tres pedidas por Oscar:

1. **Evitar** operar alrededor de noticias.
2. **Operar** noticias (la sorpresa: dato real frente a previsión).
3. **Contexto de régimen** (tipos, inflación, dólar, posicionamiento).

Con la misma regla de 2A: **sin ver el futuro**. Cada valor lleva el instante en que se conoció.

**Forma de trabajar (pedido de Oscar):** buscar algo **dinámico, inteligente y pragmático**, no
cuadrado. En la práctica: se guarda todo lo que pasó con su hora, y los supuestos (latencia, qué
previsión usar, retrasos de publicación) quedan como **parámetros de la consulta** con valores
prudentes por defecto, para que el laboratorio (Fase 4) los varíe y compruebe que una estrategia no
depende de un supuesto afortunado.

## 2. Decisiones (brainstorming del 2026-11-13 al 18)

1. Las noticias sirven para **evitar, operar y régimen**: hace falta hora exacta, dato real,
   previsión y anterior.
2. **Calendario: enfoque A con API premium si pasa una prueba** (decisión delegada en Claude y
   aprobada por Oscar). Fuente de calendario intercambiable + ALFRED + foto propia de Forex
   Factory. Antes de pagar, una prueba reproducible compara una premium (Trading Economics) con
   una barata (FMP o EODHD). Si la premium da ≥ 15 años con hora exacta y previsión de consenso a un
   precio razonable, se contrata; si no, la barata. Oscar aprueba el pago.
   Motivo: para *operar* noticias pesa el número de casos (6 años ≈ 70 NFP; 20 años ≈ 240); para
   *evitar* y *régimen*, una barata alcanza.
3. **Construcción: enfoque 1**, ampliar el almacén de 2A con tipos de contexto + guardar siempre el
   crudo (§5). Descartados: tablas en Postgres (rompe "la verdad son los archivos" y obliga a
   reimplementar reserva y sin-futuro) y solo crudo procesado al consultar (lento, frágil, no
   verificable).
4. **Foto de Forex Factory desde la laptop** en 2C, cada 4 h por defecto. Pasa al VPS en 2B solo
   tras comprobar con Daniel que no afecta a producción (ver §8).
5. Alcance de §3, aprobado por Oscar.

## 3. Qué se guarda y de dónde

| Tipo | Fuente | Alcance | Frecuencia |
|---|---|---|---|
| `calendario` | API elegida (§7) | Divisas G10 + CNY (USD, EUR, GBP, JPY, CHF, AUD, CAD, NZD, NOK, SEK, CNY); impacto medio y alto; toda la historia que dé | diaria |
| `calendario` (fuente `forexfactory`) | JSON público `ff_calendar_thisweek.json` y `..._nextweek.json` | lo que traigan | cada 4 h (`DATOS_FF_CADA_HORAS`) |
| `serie` (fuente `alfred`) | ALFRED / FRED con versiones (API gratis con clave) | Primera publicación y revisiones de: PAYEMS (NFP), UNRATE, CPIAUCSL, CPILFESL, PCEPI, PCEPILFE, GDP, RSAFS, ICSA, y el ISM manufacturero si FRED lo ofrece | diaria |
| `serie` (fuente `fred`) | FRED | De mercado, sin revisiones: DGS10, DGS2, DFII10, T10YIE, DFF, DTWEXBGS, VIXCLS (más las que pida la Fase 3) | diaria |
| `cot` | CFTC (archivos anuales, informes Legacy y Disaggregated / TFF) | Oro, plata, cobre, petróleo WTI, divisas G10, S&P 500, Nasdaq 100, Dow, bonos a 10 años | semanal |
| `etf` | SPDR (CSV histórico de GLD) | Toneladas y valor | diaria |
| `corporativo` | Una API barata (FMP o EODHD), **contratada aunque gane la premium** (decisión de Oscar, 2026-11-18) | Acciones y ETFs del registro (`clase` `accion`/`etf`) | semanal |

Las listas de series y mercados viven en un archivo de configuración versionado
(`datos/contexto.json`), no en el código: añadir una serie es añadir una línea.

Fuera de 2C: Vantage y swaps (2B), libro de órdenes (2E), noticias en texto o sentimiento (Fase 3).

## 4. Modelo de datos sin futuro

**Regla general:** cada fila lleva `ts_conocido` (UTC), el instante desde el que el valor existía.
La consulta devuelve solo filas con `ts_conocido + latencia ≤ t`.

### 4.1 `calendario`

Columnas: `ts_evento` (hora programada, UTC), `ts_anunciado` (desde cuándo se sabía que el evento
existía, a esa hora), `ts_conocido` (desde cuándo se conocía el dato real), `ts_version` (cuándo se
guardó esta versión de la fila), `fuente`, `id_fuente`, `pais`,
`divisa`, `evento` (nombre normalizado), `evento_fuente` (nombre original), `categoria` (empleo,
inflacion, tipos, pib, consumo, actividad, vivienda, balanza, otros), `impacto` (bajo/medio/alto),
`programado` (bool), `real`, `prevision`, `anterior`, `anterior_revisado`, `unidad`,
`prevision_es_pit` (bool), `ts_foto` (solo fuente `forexfactory`: hora de la foto).

- **Dato real:** `ts_conocido = ts_evento`. La latencia del bot **no** se guarda: es un parámetro de
  la consulta (§6), por defecto 5 s.
- **Previsión:** se guardan las dos. La de la API, con `prevision_es_pit = false` (puede ser la
  última que guardó el proveedor). La de cada foto de FF, como fila propia con
  `ts_conocido = ts_foto` y `prevision_es_pit = true`.
- **Existencia del evento (dos instantes por fila; decisión de Claude tras la revisión):** la consulta
  muestra el evento desde `ts_anunciado` con `real`/`sorpresa` vacíos, y el dato real solo desde
  `ts_conocido + latencia`. Así funciona "evitar" (saber qué viene) sin ver el futuro.
  - Hacia adelante, `ts_anunciado` sale de la primera foto de FF o descarga de la API que trae el
    evento con esa hora.
  - En el histórico, `ts_anunciado = ts_evento − horizonte`, con `horizonte` como parámetro (por
    defecto 7 días para `programado = true`; 0 para los no programados).
  - **Cambios de fecha u hora** (cierres del gobierno de EE. UU., reuniones extraordinarias) se
    guardan como versiones: la fila vieja queda con su `ts_evento` y la nueva entra con su
    `ts_anunciado`. La consulta en t usa la última versión con `ts_version ≤ t` (en el histórico,
    `ts_version = ts_anunciado`).
- **Re-descargas de la API (solo se añade, nunca se reescribe):** cada descarga diaria que cambia
  algo de un evento (previsión, real, hora) añade una versión con `ts_version` = hora de descarga.
  Las filas idénticas no se duplican. Desde que empezamos a descargar, la previsión de la API también
  queda guardada tal como estaba en cada momento (`prevision_es_pit = true`); en el histórico
  bajado de golpe, `false`.
- **Emparejar Forex Factory con la API** (para `prevision="mejor"` y el cruce de §9): mismo `evento`
  normalizado, misma `divisa` y `ts_evento` a ±90 min. Lo que no empareja se lista en el informe.
- **Normalización de nombres:** un diccionario versionado (`datos/eventos.json`) mapea nombres de
  cada fuente a un `evento` común (p. ej. "Non-Farm Employment Change" y "Nonfarm Payrolls" →
  `US_NFP`). Lo que no esté mapeado se guarda con su nombre original y se lista en el informe.

### 4.2 `serie`

Columnas: `serie`, `periodo` (fecha de referencia), `valor`, `ts_conocido`, `ts_hasta` (fin de
validez de esa versión; nulo si sigue vigente), `fuente`.

- **Macro (ALFRED):** una fila por periodo y versión. `ts_conocido` = fecha de publicación de
  ALFRED + hora de publicación. La hora sale, en este orden, del calendario (mismo día, evento
  mapeado) o de una tabla por serie en `datos/contexto.json` (p. ej. PAYEMS 08:30
  America/New_York).
- **De mercado (FRED):** sin versiones. `ts_conocido` = fecha + **retraso por serie** en
  `datos/contexto.json` (decisión de Claude tras la revisión: varias se publican al día hábil
  siguiente). Por defecto: DGS10, DGS2, DFII10, T10YIE (tabla H.15) y DFF → día hábil siguiente a las
  16:30 NY; VIXCLS → ese día a las 17:00 NY; DTWEXBGS (semanal, H.10) → lunes siguiente a las 16:30
  NY. Si una serie no tiene retraso configurado, se usa el día hábil siguiente (prudente).
- **Primeras versiones de ALFRED cargadas en bloque** (todos los periodos viejos con la misma fecha,
  años después): se marcan `publicacion_estimada = true` y su `ts_conocido` se estima como
  `periodo + retraso típico de la serie` (configurable; p. ej. PAYEMS: primer viernes del mes
  siguiente a las 8:30 NY). La consulta las incluye por defecto (son necesarias para el régimen
  largo) y el parámetro `excluir_estimadas=True` las quita, para medir si cambian algún resultado.

### 4.3 `cot`

Columnas: `mercado`, `informe` (legacy, disaggregated, tff), `fecha_informe` (martes),
`ts_conocido`, y las columnas de posiciones del informe (largos, cortos, por tipo de operador,
interés abierto). `ts_conocido` = viernes 15:30 America/New_York, o la fecha real de publicación
de la CFTC cuando un festivo la retrasa (tabla de excepciones en `datos/contexto.json`).
- **Histórico publicado tarde:** los informes Disaggregated y TFF se publicaron por primera vez en
  septiembre de 2009, con datos desde 2006. Esos informes anteriores llevan `ts_conocido` = fecha
  real de esa publicación y `publicacion_tardia = true` (Legacy existe desde mucho antes y no se toca).
- **Correcciones de la CFTC:** se añaden como versión nueva (`ts_version` = hora de descarga), igual
  que el calendario.

### 4.4 `etf`

Columnas: `etf`, `fecha`, `toneladas`, `valor_usd`, `ts_conocido` = ese día a las 18:00
America/New_York (parámetro `retraso_etf`).

### 4.5 `corporativo`

Columnas: `simbolo` (canónico del registro), `tipo` (split, dividendo), `fecha_ex`,
`fecha_anuncio` (nula si la fuente no la da), `razon` (splits), `importe` y `divisa` (dividendos),
`ts_conocido` = `fecha_anuncio` o, si falta, apertura del día ex.

### 4.6 Crudo

Cada respuesta descargada se guarda tal cual (JSON, CSV o ZIP), comprimida, con manifiesto y hora
de descarga. Los Parquet normalizados se pueden rehacer desde el crudo.

## 5. Almacén

Se amplía `datos/almacen.py` en lugar de crear otro:

- Los tipos se declaran en una tabla única: esquema, versión y **partición** de cada uno:
  - `calendario`: `fuente=F/simbolo=<divisa>/anio=AAAA.parquet`
  - `serie`: `fuente=F/simbolo=<serie>/completo.parquet` (un archivo por serie, se reescribe al cambiar)
  - `cot`: `fuente=cftc/simbolo=<mercado>/anio=AAAA.parquet`
  - `etf`: `fuente=spdr/simbolo=<etf>/completo.parquet`
  - `corporativo`: `fuente=F/simbolo=<simbolo>/completo.parquet`
  - `crudo`: `crudo/fuente=F/AAAA/MM/DD/<nombre>.<ext>.zst`
- Se reutiliza el hueco `simbolo=` como "clave" (divisa, serie, mercado). Así **no cambia nada**
  de lo ya bajado (744 M ticks de oro).
- `escribir`, `estado`, manifiestos, escritura segura y `catalogo reconstruir` funcionan para todos
  los tipos. Nuevo `escribir_crudo(raiz, bytes, fuente, nombre, momento)`.
- **Contrato del manifiesto ampliado (aditivo):** los manifiestos de 2A siguen leyéndose igual; los
  nuevos tipos añaden `ts_conocido_min/max`.

## 6. Consulta

Dos funciones nuevas en `datos/consulta.py` (pocas a propósito: la Fase 3 compondrá lo que
necesite):

- `contexto(tipo, desde, hasta, conocido_en=None, claves=None, latencia="5s",
  prevision="mejor", incluir_reservado=False) -> DataFrame`
  - Devuelve lo que se sabía en `conocido_en` (por defecto `hasta`): para cada fila, la última
    versión con `ts_version ≤ conocido_en`; en `calendario`, eventos con `ts_anunciado ≤ conocido_en`
    y el dato real solo si `ts_conocido + latencia ≤ conocido_en`; en el resto, filas con
    `ts_conocido + latencia ≤ conocido_en`.
  - En `serie` con versiones devuelve la versión vigente en ese instante (no la revisada después).
  - En `calendario` añade `sorpresa = real − prevision` y `fuente_prevision`.
    `prevision="ff"` usa solo fotos de FF (la última foto antes del evento), `"api"` solo la de la
    API, `"mejor"` la de FF si existe y si no la de la API.
  - Incluye `revision_anterior = anterior_revisado − anterior`.
- `unir_contexto(precios, tipos, latencia="5s", prevision="mejor", impacto_min="alto",
  divisas=None) -> DataFrame` (forma decidida por Claude tras la revisión, lo mínimo útil):
  - Para `serie`, `cot` y `etf`: una columna por (clave, campo) con el último valor conocido antes del
    `ts_cierre` de cada vela. En `cot`, los campos van en una lista por defecto en
    `datos/contexto.json` (largos y cortos netos por tipo de operador e interés abierto).
  - Para `calendario`: cuatro columnas por vela, que son las que piden "evitar" y "operar":
    `min_hasta_proximo` y `proximo_evento` (próximo evento anunciado de impacto ≥ `impacto_min` en
    `divisas`), y `min_desde_ultimo` y `sorpresa_ultimo` (último evento con dato real conocido).
  - Nunca pega algo conocido después del `ts_cierre`. Lo demás lo compone la Fase 3 con `contexto()`.
- **Reserva de 12 meses:** se aplica también al contexto, por `ts_conocido`, con el mismo registro
  de accesos de 2A.
- Parámetros de retraso (`retraso_etf`, latencia) con valores por defecto prudentes y variables
  desde la consulta, para el análisis de sensibilidad de la Fase 4.

## 7. Prueba de APIs antes de pagar

Orden reproducible `python -m datos contexto probar-calendario --fuentes te,fmp,eodhd`:

- Baja el mismo rango (enero de 2008 y enero de 2024, divisas USD, EUR, JPY) de cada candidata con
  su cuota de prueba o gratuita.
- Informe (`_informes/prueba-calendario-AAAA-MM-DD.html`) con, para cada una:
  - profundidad real (primer año con datos);
  - % de eventos con hora exacta (no solo fecha);
  - coincidencia de la hora con la de ALFRED en datos de EE. UU.;
  - coincidencia del dato real con ALFRED;
  - % de eventos con previsión;
  - divisas cubiertas;
  - límites y precio anotados a mano.
- Criterio: la premium se contrata si cumple ≥ 15 años con hora exacta y previsión en los eventos de
  impacto alto de USD y EUR, y el precio es razonable a juicio de Oscar. Si no, la barata.
- La elegida queda como fuente `calendario` por defecto. Las demás clases de fuente se quedan en
  el código (intercambiables).
- **Cuentas de prueba (Oscar, 2026-11-18):** Oscar crea las cuentas y guarda las claves en el `.env`
  de la laptop (`TE_API_KEY`, `FMP_API_KEY`, `EODHD_API_KEY`). Primero con los planes gratuitos.
  Trading Economics solo si su prueba es fácil de obtener (sin tarjeta, o con un precio que Oscar
  apruebe) y no bloquea por país; si no, la comparación se hace solo con FMP y EODHD y queda escrito en
  el informe. Si los planes gratuitos no llegan a 2008, Oscar decide si paga un mes de la mejor
  candidata para completar la prueba.
- **La barata se contrata en cualquier caso** para `corporativo` (splits y dividendos de las acciones
  y ETFs del registro).

## 8. Foto de Forex Factory

- `python -m datos contexto bajar ff` guarda el JSON crudo de esta semana y la siguiente, y las filas
  normalizadas con `ts_foto`. Se lanza cada `DATOS_FF_CADA_HORAS` (4 por defecto).
- **La tarea programada de la foto entra en 2C** (se adelanta este trozo de 2D, porque cada día sin
  foto se pierde): `scripts/instalar_tarea_ff.ps1` crea la tarea `SentinelDatosFF` en la laptop, con
  `-DryRun`. La registra Oscar a mano (regla del repo: nadie registra tareas sin que lo pida una persona).
- **Desde la laptop, no el VPS:** producción ya consulta FF cada 15 min desde el VPS, y si FF
  limitara esa IP le cortaríamos las noticias. Moverla al VPS es parte de 2B, con Daniel.
- **Sirve al laboratorio ya, y quizás al bot (Fase 5):** el JSON de producción no trae previsión. Se
  diseña para que la misma captura pueda alimentar al bot en vivo si la Fase 5 lo decide.
- Una foto perdida (laptop apagada) no se puede recuperar: `verificar` la anota y el laboratorio
  sabe dónde la previsión es menos exacta.
- Volumen: ~50 KB por foto; 6 al día ≈ 110 MB/año en crudo, ~10 MB comprimido.

## 9. Verificación

`python -m datos verificar` incluye el contexto:

- Fotos de FF que faltan (aviso, con fechas).
- Eventos que cambiaron de fecha u hora (informativo).
- Semanas de COT que faltan; huecos en series diarias de mercado.
- Versiones de ALFRED con fechas incoherentes (`ts_hasta ≤ ts_conocido`).
- **Cruce de fuentes:** dato real API frente a ALFRED (grave si difieren en la primera
  publicación); previsión API frente a FF (se mide la diferencia típica por evento y se muestra en el
  informe; con el tiempo dice si la previsión de años viejos vale).
- Eventos sin mapear en `datos/eventos.json`.
- Tamaño por tipo y crecimiento mensual; aviso si un tipo crece más de lo esperado.
- Umbrales finos: se ajustan con los primeros datos reales (como en 2A).

## 10. Tests

Sin red ni APIs reales. Ejemplos de respuesta de cada fuente en `tests/datos/fixtures/`
(incluida una real de cada API, recortada, tras la prueba de §7). Tests clave de "sin futuro":

- Un IPC consultado antes de su hora aparece como evento anunciado pero sin dato real; después,
  con dato. Con latencia de 5 s, el dato aparece 5 s más tarde.
- Un evento reprogramado se ve con su hora vieja antes del cambio y con la nueva después.
- Una serie de la tabla H.15 no se ve el mismo día, sino el día hábil siguiente.
- Un COT Disaggregated de 2007 no existe antes de septiembre de 2009.
- Una revisión de ALFRED no se ve antes de su fecha de publicación; antes se ve la versión anterior.
- El COT del martes no existe hasta el viernes a las 15:30 NY (y hasta la fecha real si hay festivo).
- `unir_contexto` nunca pega a una vela algo conocido después de su `ts_cierre`.
- `prevision="ff"` usa la última foto **anterior** al evento, nunca una posterior.
- Reprocesar desde el crudo da exactamente las mismas filas.
- La reserva bloquea el contexto por `ts_conocido` igual que los precios.

## 11. No-objetivos y anti-objetivos

- No se decide la fuente de calendario del bot en vivo (Fase 5).
- No se baja nada en el VPS ni se toca producción, Supabase ni `motor/`.
- No se analizan noticias en texto ni sentimiento.
- Fracaso aunque "funcione": que una consulta use un dato antes de publicarse; que una previsión
  posterior al dato se tome por previa; que la foto de FF afecte a producción; claves de API en el repo.

## 12. Cuándo está lista la 2C

1. Tests pasan en Linux y en Windows.
2. Prueba de APIs hecha, con informe, y **Oscar eligió y aprobó el pago**.
3. Calendario, ALFRED, FRED, COT, GLD y corporativos bajados con toda su historia; foto de FF
   corriendo sola al menos una semana.
4. "NFP de los últimos 5 años con su sorpresa y el oro 30 min antes y después" sale en segundos y es
   correcto (comprobado a mano en 3 casos).
5. Informe sin problemas graves sin explicar.
6. Guía (`docs/datos-de-mercado.md`), skill `datos-mercado` y memoria actualizadas.

## 13. Lo que queda al constructor

- Nombres internos de módulos y funciones de cada fuente (`datos/fuentes/<fuente>.py`).
- Cómo paginar y respetar los límites de cada API.
- Columnas exactas de posiciones del COT (según los archivos de la CFTC).
- Formato de compresión del crudo (zstd recomendado).
