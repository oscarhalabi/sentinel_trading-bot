<#
.SYNOPSIS
    Arranque completo: preflight -> tests -> lanza los motores del bot nuevo.

.DESCRIPTION
    Encadena en el orden correcto lo que hay que hacer para levantar el sistema,
    y ABORTA antes de operar si algo no cuadra.

    Fases:
      1. Preflight   - python, dependencias y cada .env de instances\ validado con
                       "python -m motor.registry check" (BOTS conocidos, magics
                       distintos y sin chocar con los de produccion).
      2. Tests       - "pytest -q" (no toca MT5 ni Supabase). Se salta con -SkipTests.
      3. Lanzamiento - una ventana de PowerShell por cada motor de BOTS, via
                       scripts\run_motor.ps1 (ruta absoluta, para que
                       restart_motor.ps1 lo reconozca como propio).
      4. Resumen.

    Un .env = un USUARIO = una cuenta MT5. Los motores que corren sobre esa
    cuenta se declaran en BOTS (p.ej. BOTS=n1,n2); no hay un .env por motor.
    BOT_ID y MAGIC_NUMBER los inyecta run_motor.ps1 desde el registro de motores.

    Este script NO lanza auditorias ni news_exporter: el bot nuevo lee el JSON
    de noticias de NEWS_CALENDAR_PATH, que ya escribe produccion.

.PARAMETER InstancesDir
    Carpeta con los .env. Por defecto instances\ del repo.

.PARAMETER SkipTests
    Salta la fase 2. No recomendado tras un git pull.

.PARAMETER Lite
    Modo ahorro para VPS: lanza cada motor con --mode lite (sin TUI ni velas).
    Sin -Lite se lanza la TUI.

.PARAMETER DryRun
    Hace preflight y tests, pero no lanza nada: solo muestra que abriria.

.EXAMPLE
    scripts\start_all.ps1
    scripts\start_all.ps1 -Lite -SkipTests
    scripts\start_all.ps1 -DryRun
#>
param(
    [string]$InstancesDir = "",
    [switch]$SkipTests,
    [switch]$Lite,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if ($InstancesDir -eq "") { $InstancesDir = Join-Path $projectRoot "instances" }
$runner = Join-Path $PSScriptRoot "run_motor.ps1"
$modo = if ($Lite) { "lite" } else { "tui" }

function Write-Phase($n, $text) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor DarkCyan
    Write-Host ("  FASE $n - $text") -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor DarkCyan
}
function Write-Ok($t)   { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Warn($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fail($t) { Write-Host "  [FALLO] $t" -ForegroundColor Red }

function Read-EnvFile($path) {
    $map = @{}
    Get-Content $path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#") -or ($line -notmatch "=")) { return }
        $i = $line.IndexOf("=")
        $map[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim().Trim('"').Trim("'")
    }
    return $map
}

Set-Location $projectRoot
Write-Host ""
Write-Host "  SENTINEL (bot nuevo) - arranque completo" -ForegroundColor White
Write-Host "  repo: $projectRoot"

# ==================================================================
# FASE 1 - PREFLIGHT
# ==================================================================
Write-Phase 1 "PREFLIGHT"

$py = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $py) { Write-Fail "python no esta en el PATH."; exit 1 }
Write-Ok "python: $((python --version) 2>$null)"

# find_spec en vez de import: no ejecuta los modulos y no escupe traceback.
# En modo -Lite no se exigen rich/plotext: el lite no renderiza nada.
$deps = "('MetaTrader5','pandas','numpy','supabase','rich','plotext')"
if ($Lite) { $deps = "('MetaTrader5','pandas','numpy','supabase')" }
$faltan = python -c "import importlib.util as u; print(','.join(m for m in $deps if u.find_spec(m) is None))"
if ($LASTEXITCODE -ne 0) { Write-Fail "no se pudo comprobar las dependencias."; exit 1 }
if ($faltan -ne "") {
    Write-Fail "faltan dependencias: $faltan"
    Write-Host "         python -m pip install -r requirements.txt" -ForegroundColor DarkGray
    exit 1
}
Write-Ok "dependencias presentes"

if (-not (Test-Path $InstancesDir)) { Write-Fail "no existe $InstancesDir"; exit 1 }

$envFiles = Get-ChildItem -Path $InstancesDir -Filter "*.env" |
    Where-Object { $_.Name -notlike "example*" -and $_.Name -notlike "template*" -and $_.Name -notlike "_*" }
if (-not $envFiles) {
    Write-Fail "no hay instancias en $InstancesDir (solo plantillas)."
    Write-Host "         Copia instances\example.env a instances\<usuario>.env y rellenalo." -ForegroundColor DarkGray
    exit 1
}

$aLanzar = @()
$hardFail = $false
foreach ($f in $envFiles) {
    # Valida BOTS (conocidos), magics distintos y sin chocar con produccion.
    $salida = python -m motor.registry check $f.FullName 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "$($f.Name): $salida"
        $hardFail = $true
        continue
    }
    $own = Read-EnvFile $f.FullName
    $bots = @($own["BOTS"].Split(",") | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ -ne "" })
    $shadow = "no"
    if ($own.ContainsKey("SHADOW_MODE") -and $own["SHADOW_MODE"] -match "^(?i:true|1)$") { $shadow = "SI" }
    Write-Ok "$($f.Name): motores=[$($bots -join ', ')] sombra=$shadow"
    foreach ($b in $bots) {
        $aLanzar += [PSCustomObject]@{ Name = $f.BaseName; Path = $f.FullName; BotId = $b }
    }
}
if ($hardFail) { Write-Host ""; Write-Fail "preflight fallido. No se arranca nada."; exit 1 }

# ==================================================================
# FASE 2 - TESTS
# ==================================================================
if ($SkipTests) {
    Write-Phase 2 "TESTS (saltados por -SkipTests)"
} else {
    Write-Phase 2 "TESTS"
    python -m pytest -q | Select-Object -Last 5
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "los tests han fallado. Revisa la salida completa con: python -m pytest -q"
        exit 1
    }
    Write-Ok "tests en verde"
}

# ==================================================================
# FASE 3 - LANZAMIENTO
# ==================================================================
Write-Phase 3 "LANZAMIENTO"

if ($DryRun) {
    Write-Warn "-DryRun: no se lanza nada. Se habrian abierto:"
    foreach ($i in $aLanzar) { Write-Host "         $($i.Name)  ->  motor $($i.BotId) (modo $modo)" }
} else {
    foreach ($i in $aLanzar) {
        Write-Host "  Lanzando motor $($i.BotId) ($($i.Name)), modo $modo" -ForegroundColor Green
        # Ruta ABSOLUTA de run_motor.ps1: restart_motor.ps1 reconoce asi lo que es de este repo.
        Start-Process powershell -ArgumentList @(
            "-NoExit", "-ExecutionPolicy", "Bypass",
            "-File", "`"$runner`"",
            "-EnvFile", "`"$($i.Path)`"", "-BotId", $i.BotId, "-Mode", $modo
        )
        Start-Sleep -Milliseconds 1500
    }
}

# ==================================================================
# FASE 4 - RESUMEN
# ==================================================================
Write-Phase 4 "RESUMEN"
if ($DryRun) {
    Write-Ok "preflight y tests completados. $($aLanzar.Count) motor(es) por lanzar (dry run)."
} else {
    Write-Ok "$($aLanzar.Count) motor(es) lanzado(s). Cada uno en su ventana."
    Write-Host "         Cerrar una ventana (Ctrl+C) detiene ese motor limpiamente." -ForegroundColor DarkGray
}
Write-Host ""
