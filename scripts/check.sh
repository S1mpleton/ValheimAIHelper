#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/bridge/src${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/python -m pytest bridge/tests -q
dotnet test mod/ValheimAIHelper.Tests/ValheimAIHelper.Tests.csproj -c Release -p:RestoreLockedMode=true --nologo
dotnet build mod/ValheimAIHelper/ValheimAIHelper.csproj -c Release -p:RestoreLockedMode=true --nologo
node --check bridge/src/valheim_ai/static/app.js
