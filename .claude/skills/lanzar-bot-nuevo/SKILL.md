---
name: lanzar-bot-nuevo
description: Usar cuando haya que arrancar, parar, reiniciar o comprobar los motores del bot nuevo (n1, n2...) en el VPS, o dar de alta una cuenta demo nueva, siempre comprobando que producción (e2) no se toca.
---

# Lanzar, reiniciar y vigilar el bot nuevo

Regla de oro: producción (`bot.lite_e2`, `scripts.news_exporter` de `...\sentinel_trading`
y los terminales de `C:\MT5_instances`) **no se toca nunca**. Esta skill solo maneja
procesos de ESTE repo y terminales de `C:\MT5_instances_n1`.

Todos los comandos se corren desde la raíz del repo.

## 0. Antes de nada: foto de producción

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'bot\.lite_e2|news_exporter' } |
  Select-Object ProcessId, Name, CommandLine
```

Anota los PIDs de `bot.lite_e2` y `news_exporter`. Al final tienen que ser los mismos.

## 1. Alta de una cuenta demo nueva (solo la primera vez por cuenta)

Necesita un usuario de Supabase NUEVO (nunca uno con filas e2/m15/m5) y una cuenta demo.

```powershell
python -m scripts.provision alta --user-id <uuid> --email <correo> --login <numero> `
    --server <servidor> --password <clave> --bots n1,n2 --dry-run
```

Si el plan se ve bien, repetir sin `--dry-run`. Clona MT5 en `C:\MT5_instances_n1\mt5_<login>`,
escribe `instances\<uuid>.env` y crea (solo si faltan) las filas `bot_instances` (inactivas),
`engine_config` y `account_settings`. No lanza nada: lo hace el vigilante o `start_all`.
Primera vuelta con `SHADOW_MODE=true` en el `.env`.

## 2. Arrancar

```powershell
scripts\start_all.ps1 -DryRun     # preflight + tests, muestra qué abriría
scripts\start_all.ps1 -Lite       # lanza un motor por ventana (lite = sin pantalla)
```

`start_all.ps1` valida cada `instances\*.env` (BOTS conocidos, magics distintos y sin chocar
con producción), corre `pytest -q` (se salta con `-SkipTests`) y abre un `run_motor.ps1` por
cada motor de `BOTS=`. Un solo motor a mano:
`scripts\run_motor.ps1 -EnvFile instances\<usuario>.env -BotId n1 -Mode lite`.

## 3. Reiniciar solo lo nuestro

```powershell
powershell -File scripts\restart_motor.ps1 -DryRun    # SIEMPRE primero
powershell -File scripts\restart_motor.ps1 -Lite      # real
```

El `-DryRun` lista lo que mataría (también en `logs\restart_motor.log`). Tiene que salir
**cero** procesos de producción: solo `python ... motor.run`, `run_motor.ps1`,
`scripts.vigilante` de este repo y `terminal64.exe` bajo `C:\MT5_instances_n1`. Si aparece algo
de `...\sentinel_trading` o `C:\MT5_instances`, NO ejecutar sin `-DryRun` y avisar a Daniel.
Ver también: `python -m scripts.procs list --json`.

## 4. Vigilante (tarea `SentinelN1Vigilante`, cada 3 min)

```powershell
scripts\instalar_tareas.ps1 -DryRun     # ver qué registraría
scripts\instalar_tareas.ps1             # registrar (solo cuando lo pida una persona)
scripts\instalar_tareas.ps1 -Desinstalar
python -m scripts.vigilante --dry-run   # qué relanzaría ahora
```

Relanza los motores de `BOTS=` que no estén vivos. Tope: 5 relanzamientos en 30 min por
motor (estado en `logs\vigilante_estado.json`, log en `logs\vigilante.log`).
Solo toca la tarea `SentinelN1Vigilante`, jamás las `Sentinel*` de producción.

## 5. Verificar al terminar

1. Repetir la foto del paso 0: mismos PIDs de `bot.lite_e2` y `news_exporter`.
2. `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'motor\.run' }`
   muestra nuestros motores.
3. En la app web / Supabase: `bot_instances.last_heartbeat` reciente para `n1`/`n2`.

## Si algo falla

- "Los bots X e Y comparten el magic": revisar `MAGIC_N1`/`MAGIC_N2` en el `.env`.
- El vigilante dice "desactivado por 5 reintentos": mirar el log del motor, arreglar, y esperar 30 min o borrar `logs\vigilante_estado.json`.
- Un reinicio de producción mató nuestros terminales: es normal, el vigilante los relanza en 3 min como máximo.
