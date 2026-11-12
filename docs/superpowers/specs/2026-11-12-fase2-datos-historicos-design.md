# Fase 2 — Datos históricos de mercado (mapa de la fase)

Fecha: 2026-11-12 · Autores: Oscar (estrategia/análisis), con Claude. Daniel revisa lo que toca el VPS.

Este documento es el **mapa** de la Fase 2: qué subproyectos tiene, en qué orden y qué se acordó
para cada uno. Cada subproyecto tiene su propio brainstorming, spec, plan y PR. El primero ya está
diseñado: **2A** (`2026-11-12-fase2a-nucleo-datos-design.md`).

## 1. Para qué

Las Fases 3 (investigación) y 4 (laboratorio) necesitan datos de mercado propios, completos y
confiables, de **muchos mercados a la vez**, guardados para consultar millones de filas en
segundos y sin poder mirar el futuro. Es probable que el bot final opere varios mercados al mismo
tiempo, así que nada queda atado al oro.

## 2. Decisiones de Oscar (2026-11-12)

1. **Ticks bid/ask** (se investigan todos los estilos, scalping incluido).
2. **Universo "todo lo posible"**: divisas, metales, índices, energía, materias primas, acciones
   (NVDA...), ETFs y criptomonedas. **Ticks para todo**. La prioridad solo decide el orden de
   descarga. Si falta espacio, se mueve el almacenamiento (disco externo o NAS, con R2 de respaldo).
   Supabase no se usa para esto: es la base compartida con producción, es caro por GB y no está
   pensado para miles de millones de filas.
3. **Fuentes de precio:** Dukascopy (historia larga, gratis) y Vantage MT5 (nuestro bróker, de
   aquí en adelante). Queda un hueco para fuentes de pago.
4. **Almacén:** Parquet + DuckDB, con el catálogo en Postgres.
5. **Máquinas:** el bot sigue en el VPS. Los datos, la investigación y la simulación van en la
   **laptop de Oscar** (Windows, 1 TB o más de SSD), con su propio clon del repo
   (`C:\Users\oscar\code\sentinel_trading-bot`). El código pasa por GitHub; los datos, por R2.
6. **Traspaso y respaldo:** Cloudflare R2 en la cuenta de Oscar, con dos buckets
   (`sentinel-datos-vps`, donde escribe el VPS, y `sentinel-datos`, el respaldo completo), porque
   en R2 una clave se limita por bucket y no por carpeta.
7. **Contexto:** calendario histórico (API de pago, se elige tras probar) + foto diaria de Forex
   Factory, FRED con fechas de publicación, COT, GLD y splits/dividendos.
8. **Sin ver el futuro:** en el instante t, la simulación solo usa lo conocido hasta t. Las velas
   llevan su hora de cierre, y el contexto, su fecha de publicación.
9. **Reserva:** los últimos 12 meses no se miran (corte móvil) hasta la validación final de la
   Fase 4. Acceder a ellos exige pedirlo y queda registrado.
10. **Captura de Vantage:** demo 26415345 (creada por Oscar). Las demos caducan por inactividad, así
    que una **cuenta real mínima solo para datos** es la opción preferida (las reales no se cierran).

## 3. Subproyectos

| Sub | Qué | Dónde | Depende de | Estado |
|---|---|---|---|---|
| **2A** | Núcleo: registro de símbolos, almacén Parquet, catálogo, descargador Dukascopy, M1, verificación, consulta sin futuro y con reserva | Laptop | — | Diseñado |
| **2B** | Vantage + R2 | VPS → laptop | 2A | Por diseñar |
| **2C** | Contexto | Laptop | 2A | Por diseñar |
| **2D** | Mantenimiento automático e informe diario | Ambas | 2A–2C | Por diseñar |
| **2E** | Libro de órdenes (order book) | Por decidir | 2A | Por diseñar |

2B y 2C pueden ir en paralelo cuando termine 2A.

## 4. Notas acordadas para cada subproyecto pendiente

### 2B — Vantage + R2
- Clon MT5 propio solo para datos en `C:\MT5_instances_n1\mt5_datos`, sin motores ni usuario de
  Supabase. Pasa `registry.validate_mt5_target` y no toca producción.
- Tarea `SentinelN1Datos` (cada hora) que se conecta, baja ticks y M1, sube a R2 y se desconecta.
  No se queda corriendo, así que los reinicios de producción no le afectan.
- **Hora del servidor → UTC** con una regla explícita, guardando también la hora cruda. Se
  confirma con desfase 0 frente a Dukascopy, incluidas las semanas en que EE. UU. y Europa cambian
  de horario en fechas distintas.
- **Foto diaria de especificaciones y costos** de cada símbolo: swaps, tamaño de contrato,
  márgenes, lote mínimo y comisión. Sin esto, el laboratorio simula mal las operaciones de más de
  un día.
- **RAW o Standard:** RAW cobra comisión por lote con spread casi nulo; Standard no cobra comisión
  pero tiene más spread. Se mide en lugar de suponerlo (segunda captura opcional con Standard).
- Avisar si la cuenta deja de conectar o no llegan ticks; cambiar de cuenta no corta la serie.
- Probar si MT5 de Vantage da profundidad de mercado (`market_book`); probablemente venga vacía.
- Comparación Vantage contra Dukascopy: desfase de hora, diferencia de precio y spread.

### 2C — Contexto
- Calendario histórico con API de pago (probar 2–3 candidatas antes de pagar) + foto diaria del
  JSON de Forex Factory (una descarga al día, independiente del `news_exporter` de producción).
- FRED con fechas de publicación (ALFRED), COT (fecha del informe y de publicación) y GLD.
- Splits y dividendos de acciones, para ajustar donde la verificación de 2A marque "posible split".

### 2D — Mantenimiento
- Tareas programadas `SentinelN1Datos` (VPS) y `SentinelDatosPC` (laptop), órdenes idempotentes
  que rellenan huecos, candado contra corridas dobles e informe diario.

### 2E — Libro de órdenes
- El oro spot y las divisas en MT5 no tienen un libro central (son mercados entre bancos). Hay libro
  real en los futuros del CME (oro GC, divisas 6E, índices), en Nasdaq (acciones) y en Binance
  (cripto, incluido PAXG). Un proveedor que cubre CME y Nasdaq es Databento (de pago).
- Años de libro completo son varios TB. Evaluar fuentes y costo, y capturar desde ya hacia
  delante, que es lo barato. Si sirve, el bot lo usaría en vivo (Fase 5).

## 5. Notas para las Fases 3–5 (acordadas aquí para no perderlas)

- **Biblioteca científica** como primer bloque de la Fase 3: papers revisados por pares y libros
  de referencia (microestructura: Harris, Hasbrouck, Bouchaud; método: López de Prado; regímenes:
  Hamilton; SSRN, arXiv q-fin, Journal of Finance). Solo lo que se pueda obtener legalmente.
- **Librería única de indicadores y herramientas**, la misma en el laboratorio y en el bot, para
  que el backtest y la operación real calculen igual.
- **Análisis entre mercados** (correlaciones, regímenes compartidos, contagio) sobre la consulta
  multi-mercado de 2A.
- **Proyección como un trader humano:** que los indicadores y las herramientas gráficas estimen
  hacia dónde puede ir el precio (zonas, objetivos, escenarios con su probabilidad).
- **Sumar ventajas pequeñas:** no se descarta una estrategia por rendir poco. Si gana de forma
  consistente después de costos y no se mueve igual que las demás, entra al conjunto.
- **Rentabilidad:** la aspiración de Oscar es de alrededor del 200 % mensual, con días buenos de
  ~10 %. Se le advirtió que eso está muy por encima de lo sostenible. El laboratorio mide con
  costos reales, drawdown y probabilidad de ruina, y el objetivo se ajusta a lo que demuestren los
  datos, sin martingalas.
- **MiroFish:** no se fuerza. Si en la Fase 3 aparece una pieza que valga la pena, se evalúa como
  servicio aparte (licencia AGPL) y solo hacia delante. Ver `docs/memory/mirofish-evaluacion.md`.

## 6. Convivencia con producción

Igual que en la Fase 1: nada de la Fase 2 escribe en Supabase. En el VPS solo se añaden un clon
en `C:\MT5_instances_n1`, una carpeta de datos y la tarea `SentinelN1Datos` (2B/2D). No se toca
`C:\Users\Administrator\Documents\sentinel_trading`, ni sus tareas (`SentinelTickCollector`
incluida), ni `C:\MT5_instances`.
