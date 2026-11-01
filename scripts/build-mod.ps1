param([string]$GameDir = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw '.NET SDK 8 or later is required to build the mod.' }
if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 scripts/bootstrap_mod.py }
else { & python scripts/bootstrap_mod.py }
if ($LASTEXITCODE -ne 0) { throw 'BepInEx reference preparation failed.' }
$Project = Join-Path $Root 'mod\ValheimAIHelper\ValheimAIHelper.csproj'
if ($GameDir) {
    $Managed = Join-Path $GameDir 'valheim_Data\Managed'
    if (-not (Test-Path (Join-Path $Managed 'assembly_valheim.dll'))) { throw 'Game assemblies not found at -GameDir.' }
    & dotnet build $Project -c Release -p:RestoreLockedMode=true "-p:GameManagedDir=$Managed"
} else {
    & dotnet build $Project -c Release -p:RestoreLockedMode=true
}
if ($LASTEXITCODE -ne 0) { throw 'Mod build failed. Check compatibility with this Valheim version.' }
Write-Host 'Built: mod\ValheimAIHelper\bin\Release\net48\ValheimAIHelper.dll'
