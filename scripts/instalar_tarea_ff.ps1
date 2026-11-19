<#
.SYNOPSIS
    Registra la tarea programada SentinelDatosFF (foto de Forex Factory) en la laptop.

.DESCRIPTION
    SentinelDatosFF ejecuta "python -m datos contexto bajar ff" cada N horas (por defecto 4),
    desde ahora y sin fecha de fin, con el usuario actual y en la carpeta del repo.
    La salida se agrega a <DATA_DIR>\_registro\ff.log (DATA_DIR sale del .env de la raiz del repo).
    Si la laptop estaba apagada a la hora de una ejecucion, corre en cuanto pueda.

    - Es idempotente: ejecutarlo otra vez reemplaza solo esa tarea.
    - Solo toca la tarea llamada SentinelDatosFF. Nunca modifica ni borra otra
      tarea Sentinel* (las de produccion siguen intactas).

    Como usarlo (en la laptop de Oscar, a mano):
      1. Primero con -DryRun: solo muestra lo que registraria.
      2. Despues sin -DryRun para registrarla.

    Como quitarla:
      Unregister-ScheduledTask -TaskName SentinelDatosFF -Confirm:$false

.PARAMETER Horas
    Cada cuantas horas corre la foto. Por defecto 4. Debe ser mayor que 0.

.PARAMETER DryRun
    No registra nada: solo muestra lo que haria.

.EXAMPLE
    scripts\instalar_tarea_ff.ps1 -DryRun
    scripts\instalar_tarea_ff.ps1
    scripts\instalar_tarea_ff.ps1 -Horas 6
#>
param(
    [double]$Horas = 4,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$NombreTarea = "SentinelDatosFF"   # unico nombre que este script toca
$projectRoot = Split-Path -Parent $PSScriptRoot
$usuario = "$env:USERDOMAIN\$env:USERNAME"

if ($Horas -le 0) { Write-Host "  [FALLO] -Horas debe ser mayor que 0."; exit 1 }

# DATA_DIR sale del .env de la raiz del repo (KEY=VALUE por linea).
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Host "  [FALLO] no existe $envFile (hace falta para leer DATA_DIR)."; exit 1
}
$dataDir = $null
Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#") -or ($line -notmatch "=")) { return }
    $idx = $line.IndexOf("=")
    if ($line.Substring(0, $idx).Trim() -eq "DATA_DIR") {
        $dataDir = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    }
}
if ([string]::IsNullOrWhiteSpace($dataDir)) {
    Write-Host "  [FALLO] falta DATA_DIR en $envFile (carpeta donde se guardan los datos)."; exit 1
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $py) { Write-Host "  [FALLO] python no esta en el PATH."; exit 1 }

$registro = Join-Path $dataDir "_registro"
$log = Join-Path $registro "ff.log"
# cmd /c permite agregar la salida (y los errores) al log con >>.
$comando = "/c `"`"$($py.Source)`" -m datos contexto bajar ff >> `"$log`" 2>&1`""
$accion = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $comando -WorkingDirectory $projectRoot
$disparador = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours $Horas)
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

if ($DryRun) {
    Write-Host "  [DryRun] se registraria $NombreTarea (reemplaza solo esa tarea si ya existe):"
    Write-Host "           ejecuta : python -m datos contexto bajar ff"
    Write-Host "           carpeta : $projectRoot"
    Write-Host "           log     : $log"
    Write-Host "           cada    : $Horas horas, desde ahora, usuario $usuario"
    exit 0
}

New-Item -ItemType Directory -Force -Path $registro | Out-Null
Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparador `
    -Principal $principal -Settings $ajustes -Force `
    -Description "Datos: foto de Forex Factory (python -m datos contexto bajar ff)." | Out-Null
Write-Host "  [OK] tarea $NombreTarea registrada (cada $Horas h, usuario $usuario)."
