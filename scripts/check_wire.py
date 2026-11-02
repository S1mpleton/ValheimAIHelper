"""Exercise the mod's actual JSON encoder/decoder across the Python HTTP API."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge/src"))
from valheim_ai.app import create_app


async def main():
    project = ROOT / "mod/ValheimAIHelper.WireProbe/ValheimAIHelper.WireProbe.csproj"
    subprocess.run(["dotnet", "build", str(project), "-c", "Release", "--nologo", "--verbosity", "quiet"], check=True, timeout=60)
    program = project.parent / "bin/Release/net8.0/ValheimAIHelper.WireProbe.dll"
    encoded = subprocess.check_output(["dotnet", str(program)], timeout=10)
    original = json.loads(encoded)
    assert original["actor"]["name"] == "Бьёрн"
    token = "wire-probe-local-token-not-a-provider-key"
    def provider(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "action": "move", "target_id": "", "duration": 2, "say": "Иду вперёд"
        })}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=token, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"Authorization": "Bearer " + token}) as client:
            first = await client.post("/api/decision", content=encoded, headers={"Content-Type": "application/json"})
            assert first.status_code == 200, first.text
            assert first.json()["action"] == "wait"
            assert (await client.post("/api/command", json={"mode": "follow"})).status_code == 200
            following = await client.post("/api/decision", json={**original, "sequence": 2})
            assert following.status_code == 200, following.text
            assert following.json()["action"] == "follow"
            result = subprocess.check_output(["dotnet", str(program), "--check-response", "2"], input=following.content, timeout=10)
            assert result.strip() == b"ACCEPTED:follow"
            await client.post("/api/provider", json={"provider": "openai", "model": "test-model", "api_key": "mock-only"})
            await client.post("/api/command", json={"mode": "ai", "objective": "Иди вперед"})
            moving = await client.post("/api/decision", json={**original, "sequence": 3})
            assert moving.status_code == 200 and moving.json()["action"] == "move", moving.text
            result = subprocess.check_output(["dotnet", str(program), "--check-response", "3"], input=moving.content, timeout=10)
            assert result.strip() == b"ACCEPTED:move"
    print("PASS: actual C# wire serializer -> Python HTTP validation -> follow/mocked AI move -> C# decoder and policy")


if __name__ == "__main__":
    asyncio.run(main())
