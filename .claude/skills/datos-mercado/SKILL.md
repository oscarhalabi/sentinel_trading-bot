---
name: datos-mercado
description: Usar cuando haya que bajar, relanzar o revisar datos históricos de mercado en la laptop (python -m datos), añadir un símbolo o una fuente, leer el informe de verificar, consultar velas para investigar o mover DATA_DIR.
---

# Datos de mercado (paquete `datos/`)

Referencia completa: `docs/datos-de-mercado.md`. Decisiones y medidas reales:
`docs/memory/fase2-datos-decisiones.md`. Todo esto corre en la **laptop de Oscar**, nunca
en el VPS de producción.

## 0. Antes de nada

- `.venv` en la raíz del repo; variables `DATA_DIR`, `DATOS_PG_DSN` (y `DATOS_PG_DSN_TEST` para
  los tests) como variables de usuario de Windows. Un proceso abierto antes de crearlas no las ve:
  en PowerShell, cargarlas con `[Environment]::GetEnvironmentVariable('DATA_DIR','User')`.
- **Nunca** imprimir ni commitear el DSN ni las contraseñas (`pgpass.conf` vive en `%APPDATA%`).
- Ritmo de Dukascopy: 3 en paralelo y 3 por segundo (es el valor por defecto; `DUKAS_MAX_PARALELO` y `DUKAS_MAX_POR_SEGUNDO` lo cambian). Más rápido provoca 429/503.

## 1. Bajar o relanzar una descarga

1. ¿Hay otra en marcha? Si existe `DATA_DIR\_registro\descarga.lock` con un PID vivo, no lanzar
   otra (el candado lo impide igual). Un candado con un PID muerto se reemplaza solo.
2. Descargas largas (horas o días): **no** lanzarlas desde la app de Claude, porque mueren
   cuando la app se reinicia. Pedir a Oscar que abra con doble clic un lanzador `.cmd` en
   `C:\sentinel_datos` (modelo: `bajar_XAUUSD.cmd`: fija el ritmo, `start /belownormal /wait`,
   anota inicio y fin en `_registro\*.out.txt`).
3. Descargas cortas (un día, unos minutos): `python -m datos bajar --simbolo X --desde D --hasta D`.
4. Ver el avance: `python -m datos catalogo estado` o la tabla `particiones` del catálogo
   (`estado`, `periodo`, `ultimo_error`).
5. Al terminar, relanzar hasta que no queden días con `error` (cada pasada solo pide esos).
   El código 1 al final significa "hubo días con error", no que algo se rompiera.
6. Después: `python -m datos velas --simbolo X` y `python -m datos verificar --simbolo X`.

## 2. Leer el informe de `verificar`

- Abrir `DATA_DIR\_informes\AAAA-MM-DD.html` o consultar la tabla `problemas`.
- `hueco` grave: mirar si es un festivo o cierre excepcional no previsto en `datos/horarios.py`
  (convertir a UTC y ver el día de la semana). Si es una regla general que falta, añadirla con
  test en `tests/datos/test_horarios.py`. Si es un día suelto, explicarlo en la memoria.
- `conteo`, `salto`: avisos; revisar solo si se amontonan en un periodo.
- `verificar` no repite problemas ya anotados. Para rehacer el informe de un símbolo tras cambiar
  las reglas: `delete from problemas where simbolo='X'` en el catálogo y volver a correrlo.

## 3. Añadir un símbolo o una fuente

- Símbolo de Dukascopy: `python -m datos simbolos descubrir` → revisar el resumen →
  `--aplicar`. Lo que se ajusta a mano va en `ajustado_a_mano` del registro para que no se pise.
- Un ticker que cambia de nombre (ej. `FB.US` → `META.US`) va en `_RENOMBRADOS` de
  `datos/simbolos.py`, con test.
- Fuente nueva: una clase en `datos/fuentes/` con `nombre`, `bajar_dia(sim, dia)` y
  `listar_instrumentos()`, como `Dukascopy`. Los tests nunca llaman a la red.

## 4. Consultar para investigar

Usar `datos.consulta` (`cargar`, `cargar_varios`, `ticks`, `conectar`) con fechas `date`. No leer
los Parquet a mano: la consulta aplica la reserva de 12 meses y la regla de no ver el futuro.
Pedir `incluir_reservado=True` solo para la prueba final, y decirlo.

## 5. Mover `DATA_DIR`

1. Parar cualquier descarga (que no exista el candado).
2. Copiar la carpeta entera (incluidas `_manifiestos` y `_registro`) al disco nuevo.
3. Cambiar la variable de usuario `DATA_DIR`.
4. `python -m datos catalogo reconstruir`: las rutas del catálogo son relativas, pero así se
   comprueba que todo está.
5. `python -m datos catalogo estado` y comparar los conteos con los de antes.
