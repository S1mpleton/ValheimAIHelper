param([ValidateRange(1024, 65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-windows.ps1') -BridgeOnly -Port $Port
