<#
.SYNOPSIS
    Registra (o quita) la tarea programada propia del bot nuevo: SentinelN1Vigilante.

.DESCRIPTION
    SentinelN1Vigilante ejecuta "python -m scripts.vigilante --root <repo>" cada 3 minutos, en la
    sesion interactiva del operador (los motores que relance abren su ventana ahi).

    - Es idempotente: ejecutarlo otra vez reemplaza solo esa tarea.
    - Solo toca la tarea llamada SentinelN1Vigilante. Nunca modifica ni borra otra
      tarea Sentinel* (las de produccion siguen intactas).

.PARAMETER Desinstalar
    Quita SentinelN1Vigilante (y nada mas).

.PARAMETER DryRun
    No registra ni borra nada: solo muestra lo que haria.

.EXAMPLE
    scripts\instalar_tareas.ps1 -DryRun
    scripts\instalar_tareas.ps1
    scripts\instalar_tareas.ps1 -Desinstalar
#>
param(
    [switch]$Desinstalar,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$NombreTarea = "SentinelN1Vigilante"   # unico nombre que este script toca
$projectRoot = Split-Path -Parent $PSScriptRoot
$usuario = "$env:USERDOMAIN\$env:USERNAME"

if ($Desinstalar) {
    $existe = Get-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
    if ($null -eq $existe) { Write-Host "  [OK] $NombreTarea no existe: nada que quitar."; exit 0 }
    if ($DryRun) { Write-Host "  [DryRun] se quitaria la tarea $NombreTarea."; exit 0 }
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false
    Write-Host "  [OK] tarea $NombreTarea quitada."
    exit 0
}

# pythonw no abre ventana de consola cada 3 minutos; si no esta, python normal.
$py = Get-Command pythonw -ErrorAction SilentlyContinue
if ($null -eq $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if ($null -eq $py) { Write-Host "  [FALLO] python no esta en el PATH."; exit 1 }

# --root lleva la ruta del repo en la linea de comandos: asi restart_motor.ps1
# (select_ours) reconoce al vigilante como propio.
$argumentos = "-m scripts.vigilante --root `"$projectRoot`""
$accion = New-ScheduledTaskAction -Execute $py.Source -Argument $argumentos -WorkingDirectory $projectRoot
$inicio = (Get-Date).AddMinutes(1)
$disparador = New-ScheduledTaskTrigger -Once -At $inicio -RepetitionInterval (New-TimeSpan -Minutes 3)
# Interactive: corre en la sesion abierta del operador (necesita estar conectado).
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

if ($DryRun) {
    Write-Host "  [DryRun] se registraria $NombreTarea (reemplaza solo esa tarea si ya existe):"
    Write-Host "           ejecuta : $($py.Source) $argumentos"
    Write-Host "           carpeta : $projectRoot"
    Write-Host "           cada    : 3 minutos, sesion interactiva de $usuario"
    exit 0
}

Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparador `
    -Principal $principal -Settings $ajustes -Force `
    -Description "Bot nuevo: relanza los motores caidos (scripts.vigilante)." | Out-Null
Write-Host "  [OK] tarea $NombreTarea registrada (cada 3 min, usuario $usuario)."
