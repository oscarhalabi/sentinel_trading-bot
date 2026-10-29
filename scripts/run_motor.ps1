<#
.SYNOPSIS
    Arranca UN motor del bot nuevo, cargando su .env al entorno del proceso.

.DESCRIPTION
    Lee un archivo .env (KEY=VALUE por linea), fija BOT_ID y MAGIC_NUMBER y lanza
    "python -m motor.run". Las variables se setean SOLO en este proceso.

    Un .env = un USUARIO = una cuenta MT5. Los motores que corren sobre esa
    cuenta (los de BOTS) comparten credenciales y difieren solo en BOT_ID y
    MAGIC_NUMBER. El magic sale del registro de motores (motor\engines.json):
    "python -m motor.registry magic <bot_id>" lee MAGIC_<X> del entorno o usa el
    valor por defecto del motor.

    Si python termina con un codigo distinto de 0, el proceso de PowerShell se
    cierra con ese codigo ([Environment]::Exit), incluso con -NoExit: asi el
    vigilante ve el motor como caido y lo relanza.

    Siempre se pasa --root con la ruta absoluta del repo: asi la linea de comandos
    del proceso lleva la ruta y restart_motor.ps1 puede reconocerlo como propio
    (y nunca confundirlo con los procesos de produccion).

    Para levantar todos los motores de todos los usuarios con preflight y tests,
    usa scripts\start_all.ps1.

.PARAMETER EnvFile
    Ruta al .env del usuario.

.PARAMETER BotId
    Motor a arrancar (n1, n2...). Debe existir en motor\engines.json.

.PARAMETER Mode
    console (por defecto), lite (sin TUI ni log en consola) o tui.

.EXAMPLE
    scripts\run_motor.ps1 -EnvFile instances\daniel.env -BotId n1
    scripts\run_motor.ps1 -EnvFile instances\daniel.env -BotId n1 -Mode lite
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [Parameter(Mandatory = $true)]
    [string]$BotId,

    [ValidateSet("console", "lite", "tui")]
    [string]$Mode = "console"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $EnvFile)) {
    Write-Error "No existe el archivo de entorno: $EnvFile"
    exit 1
}

# Cargar KEY=VALUE (ignora comentarios y lineas vacias).
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#") -or ($line -notmatch "=")) { return }
    $idx = $line.IndexOf("=")
    $key = $line.Substring(0, $idx).Trim()
    $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path "Env:$key" -Value $val
}

# Correr desde la raiz del repo (padre de scripts/), con ruta absoluta.
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# --- Identidad del motor -------------------------------------------------
$BotId = $BotId.Trim().ToLower()
$env:BOT_ID = $BotId
# stderr de python no debe abortar el script (PS 5.1 + Stop): se captura aparte.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$magic = (& python -m motor.registry magic $BotId 2>&1 | ForEach-Object { "$_" })
$magicExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($magicExit -ne 0) {
    Write-Host "$magic" -ForegroundColor Red
    Write-Error "No se pudo obtener el magic de '$BotId' (ver motor\engines.json)."
    exit 1
}
$env:MAGIC_NUMBER = ("$magic" -split "\s+" | Where-Object { $_ } | Select-Object -Last 1)

Write-Host "Arrancando $BotId | USER_ID=$env:USER_ID SYMBOL=$env:SYMBOL BOT_ID=$env:BOT_ID MAGIC=$env:MAGIC_NUMBER (modo: $Mode)" -ForegroundColor Cyan

python -m motor.run --mode $Mode --root "$projectRoot"
$codigo = $LASTEXITCODE

# Si el motor se cae con error, se cierra tambien esta ventana de PowerShell,
# aunque se abriera con -NoExit (start_all.ps1): si quedara abierta con la misma
# linea de comandos, el vigilante creeria que el motor sigue vivo y no lo
# relanzaria. Una salida limpia (codigo 0: Ctrl+C, parada a mano o "ya hay un
# motor corriendo") deja la ventana abierta para poder leerla.
if ($codigo -ne 0) {
    Write-Host "El motor $BotId termino con error (codigo $codigo). Se cierra la ventana para que el vigilante lo relance." -ForegroundColor Red
    [Environment]::Exit($codigo)
}
