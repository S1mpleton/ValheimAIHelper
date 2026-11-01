#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_CLI_HOME=/workspace/.valheim-cache/dotnet-home
export NUGET_PACKAGES=/workspace/.valheim-cache/nuget
python3 scripts/install_dotnet.py
export DOTNET_ROOT=/workspace/.valheim-tools/dotnet
export PATH="$DOTNET_ROOT:$PATH"
if [[ ! -x .venv/bin/python ]]; then uv venv .venv; fi
uv pip install --python .venv/bin/python --require-hashes -r bridge/requirements-dev.txt
python3 scripts/bootstrap_mod.py
dotnet restore mod/ValheimAIHelper/ValheimAIHelper.csproj --locked-mode --nologo
dotnet restore mod/ValheimAIHelper.Tests/ValheimAIHelper.Tests.csproj --locked-mode --nologo
dotnet build mod/ValheimAIHelper/ValheimAIHelper.csproj -c Release --no-restore --nologo
printf '%s\n' 'Setup complete. Set PYTHONPATH=bridge/src before starting the bridge or Python tests.'
