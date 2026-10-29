<#
.SYNOPSIS
    Reinicio selectivo: tumba SOLO los procesos de este repo y relanza start_all.ps1.

.DESCRIPTION
    Se lanza a mano cuando hace falta reiniciar los motores del bot nuevo
    (el vigilante relanza solo los caidos; esto reinicia todos).

    A diferencia del reinicio antiguo, NO mata "todo lo que parezca un bot":
    pregunta a "python -m scripts.procs list --json" (funcion select_ours) y mata
    unicamente lo que devuelve:
      - python con motor.run / scripts.vigilante que lleven la ruta de ESTE repo
        (run_motor.ps1 y la tarea SentinelN1Vigilante pasan siempre --root <repo>;
        asi un vigilante a medio relanzar se corta y no compite con este reinicio)
      - powershell con run_motor.ps1 de ESTE repo
      - terminal64.exe bajo MT5_CLONES_DIR (por defecto C:\MT5_instances_n1)
    Los procesos de produccion (bot.lite_e2, news_exporter y los terminales de
    C:\MT5_instances) quedan fuera por construccion.

    Pasos:
      1. Lista los procesos propios y los mata (salvo con -DryRun).
      2. Relanza scripts\start_all.ps1 -SkipTests (sin prompts).

    Todo queda en logs\restart_motor.log.

.PARAMETER DryRun
    Solo loguea que procesos mataria y que lanzaria, sin tocar nada.

.PARAMETER Lite
    Relanza en modo ahorro (start_all.ps1 -Lite): sin TUI ni log en consola.

.EXAMPLE
    powershell -File scripts\restart_motor.ps1 -DryRun
#>
param(
    [switch]$DryRun,
    [switch]$Lite
)

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$log = Join-Path $logDir "restart_motor.log"

function Write-Log($t) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $t"
    Write-Host $line
    Add-Content -Path $log -Value $line
}

$modo = if ($DryRun) { " (DRY RUN)" } else { "" }
Write-Log "=== Reinicio solicitado$modo ==="

# --- 1. Procesos propios (select_ours) ---
Set-Location $projectRoot
$json = python -m scripts.procs list --json
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: no se pudo listar los procesos propios. No se mata nada."
    exit 1
}
$propios = @()
if ($json) { $propios = @((ConvertFrom-Json $json) | Where-Object { $_ }) }

Write-Log "$($propios.Count) procesos propios a reiniciar"
foreach ($p in $propios) {
    $desc = if ($p.cmdline) { $p.cmdline } else { $p.exe }
    Write-Log "matar PID $($p.pid) [$($p.name)]: $($desc -replace '\s+', ' ')"
    if (-not $DryRun) { Stop-Process -Id $p.pid -Force -ErrorAction SilentlyContinue }
}

if ($DryRun) {
    Write-Log "=== DRY RUN: no se mato nada ni se lanzo start_all ==="
    exit 0
}

Start-Sleep -Seconds 5

# --- 2. Relanzar todo ---
$startArgs = @("-SkipTests")
if ($Lite) { $startArgs += "-Lite" }
Write-Log "lanzando start_all.ps1 $($startArgs -join ' ') ..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_all.ps1") @startArgs *>> $log
Write-Log "=== start_all terminado (exit $LASTEXITCODE) ==="
