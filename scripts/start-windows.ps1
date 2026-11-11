param(
    [string]$GameDir = "${env:ProgramFiles(x86)}\Steam\steamapps\common\Valheim",
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [switch]$BridgeOnly
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
if (-not $BridgeOnly) {
if (Get-Process -Name valheim -ErrorAction SilentlyContinue) {
    throw 'Close Valheim before installing/pairing the plugin, then run this script again.'
}

if (-not (Test-Path (Join-Path $GameDir 'BepInEx\core\BepInEx.dll'))) {
    throw 'BepInEx not found. Install BepInExPack_Valheim first, or pass -GameDir with the correct modded game folder.'
}
if (-not (Test-Path (Join-Path $GameDir 'valheim.exe'))) {
    throw 'This is not a Valheim Windows installation. Check -GameDir.'
}
# Copy the prebuilt plugin, or use an already built one in a source checkout.
$Plugin = Join-Path $Root 'plugin\ValheimAIHelper.dll'
if (-not (Test-Path $Plugin)) {
    $Plugin = Join-Path $Root 'BepInEx\plugins\ValheimAIHelper\ValheimAIHelper.dll'
}
if (-not (Test-Path $Plugin)) {
    $Plugin = Join-Path $Root 'mod\ValheimAIHelper\bin\Release\net48\ValheimAIHelper.dll'
}
if (-not (Test-Path $Plugin)) {
    throw 'Plugin DLL missing. Use the prepared Windows archive or run scripts\build-mod.ps1 first.'
}
$PluginDir = Join-Path $GameDir 'BepInEx\plugins\ValheimAIHelper'
New-Item -ItemType Directory -Force $PluginDir | Out-Null
$Destination = Join-Path $PluginDir 'ValheimAIHelper.dll'
if (Test-Path $Destination) {
    if ((Get-FileHash $Destination -Algorithm SHA256).Hash -ne (Get-FileHash $Plugin -Algorithm SHA256).Hash) {
        Copy-Item $Destination "$Destination.previous" -Force
    }
}
Copy-Item $Plugin $Destination -Force
$BundledRuntime = Join-Path (Split-Path $Plugin -Parent) 'runtime'
if (Test-Path (Join-Path $BundledRuntime 'ValheimAIBridge.exe')) {
    Copy-Item $BundledRuntime $PluginDir -Recurse -Force
    Write-Host 'Plugin and bundled service installed. Start Valheim; Esc -> Character control (F8). No PowerShell service is needed.'
    exit 0
}

}

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 from python.org, including the Python Launcher.' }
        & py -3.12 -m venv (Join-Path $Root '.venv')
    } else {
        & python -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or 3.12 from python.org.' }
        & python -m venv (Join-Path $Root '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}
& $Python -m pip install --require-hashes -r (Join-Path $Root 'bridge\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check Internet access and the Python version.' }
$env:PYTHONPATH = (Join-Path $Root 'bridge\src') + [IO.Path]::PathSeparator + $env:PYTHONPATH
Write-Host 'Starting AI service. Keep this window open. In Valheim: Esc -> AI Helper (or F8).'
Write-Host 'Controls are inside the game. Diagnostic web dashboard port:' $Port
$BridgeArgs = @('--data-dir', (Join-Path $Root '.local'), '--port', $Port)
if (-not $BridgeOnly) { $BridgeArgs += @('--game-dir', $GameDir) }
& $Python -m valheim_ai @BridgeArgs
if ($LASTEXITCODE -ne 0) { throw 'Bridge exited with an error. Read the message above.' }
