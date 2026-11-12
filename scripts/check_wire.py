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
from valheim_ai.protocol import Action


async def main():
    project = ROOT / "mod/ValheimAIHelper.WireProbe/ValheimAIHelper.WireProbe.csproj"
    subprocess.run(["dotnet", "build", str(project), "-c", "Release", "--nologo", "--verbosity", "quiet"], check=True, timeout=60)
    program = project.parent / "bin/Release/net8.0/ValheimAIHelper.WireProbe.dll"
    plugin = ROOT / "mod/ValheimAIHelper/bin/Release/net48/ValheimAIHelper.dll"
    subprocess.run(["dotnet", str(program), "--check-game-access", str(plugin)], check=True, timeout=10)
    encoded = subprocess.check_output(["dotnet", str(program)], timeout=10)
    capabilities = json.loads(subprocess.check_output(["dotnet", str(program), "--capabilities"], timeout=10))
    assert set(capabilities) == {a.value for a in Action}, "Mod/bridge action catalog mismatch"
    original = json.loads(encoded)
    assert original["actor"]["name"] == "Бьёрн"
    token = "wire-probe-local-token-not-a-provider-key"
    def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "gpt-6-luna"}]})
        if b"api-check" in request.content:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "action": "wait", "target_id": "", "duration": 1, "say": "Связь работает"
            })}}]})
        if b"wire-extended" in request.content:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": [{
                "action":"drop", "target_id":"inventory:stones", "amount":5, "duration":1, "say":"Выбрасываю пять камней"
            }]})}}]})
        if b'"steps"' in request.content:
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": [{
                "action": "move", "target_id": "", "duration": 2, "say": "Иду к точке",
                "destination": {"x": -1080, "y": 32.125, "z": 4800}, "run": True,
            }]})}}]})
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
            # These are the actual DTOs used by the in-game panel, not hand-written JSON.
            settings = subprocess.check_output(["dotnet", str(program), "--ui-provider"], timeout=10)
            configured = await client.post("/api/provider", content=settings, headers={"Content-Type": "application/json"})
            assert configured.status_code == 200, configured.text
            result = subprocess.check_output(["dotnet", str(program), "--read-ui-ok"], input=configured.content, timeout=10)
            assert result.strip() == b"UI_OK"
            await client.post("/api/command", json={"mode": "ai", "objective": "Иди к точке"})
            planning = await client.post("/api/decision", json={**original, "sequence": 4, "background_planning": True})
            assert planning.status_code == 200
            task = app.state.bridge.planner.task
            if task is not None: await task
            planned = await client.post("/api/decision", json={**original, "sequence": 5, "background_planning": True})
            assert planned.status_code == 200 and planned.json()["action"] == "move", planned.text
            result = subprocess.check_output(["dotnet", str(program), "--check-plan-response", "5"], input=planned.content, timeout=10)
            assert result.strip() == b"PLAN_ACCEPTED:move"
            await app.state.bridge.planner.close()
            status = await client.get("/api/status")
            result = subprocess.check_output(["dotnet", str(program), "--read-ui-status"], input=status.content, timeout=10)
            assert result.strip() == b"UI_STATUS_OK"
            models = await client.post("/api/models/openai", json={})
            assert models.status_code == 200, models.text
            result = subprocess.check_output(["dotnet", str(program), "--read-ui-models"], input=models.content, timeout=10)
            assert result.strip() == b"UI_MODELS_OK"
            checked = await client.post("/api/check/openai", json={"model": "gpt-6-luna"})
            assert checked.status_code == 200, checked.text
            result = subprocess.check_output(["dotnet", str(program), "--read-ui-check"], input=checked.content, timeout=10)
            assert result.strip() == b"UI_CHECK_OK"
            stop = subprocess.check_output(["dotnet", str(program), "--ui-command", "stop"], timeout=10)
            stopped = await client.post("/api/command", content=stop, headers={"Content-Type": "application/json"})
            assert stopped.status_code == 200
            result = subprocess.check_output(["dotnet", str(program), "--read-ui-ok"], input=stopped.content, timeout=10)
            assert result.strip() == b"UI_OK"
            # A real 0.4 mod snapshot advertises the same actions and quantity fields.
            extended_bytes = subprocess.check_output(["dotnet", str(program), "--extended", "1"], timeout=10)
            extended = json.loads(extended_bytes)
            first_extended = await client.post("/api/decision", content=extended_bytes, headers={"Content-Type":"application/json"})
            assert first_extended.status_code == 200, first_extended.text
            await client.post("/api/command",json={"mode":"ai","objective":"Выбрось 5 камней"})
            planned_extended = await client.post("/api/decision",json={**extended,"sequence":2})
            task = app.state.bridge.planner.task
            if task is not None: await task
            planned_extended = await client.post("/api/decision",json={**extended,"sequence":3})
            result = subprocess.check_output(["dotnet",str(program),"--check-extended-response","3"],input=planned_extended.content,timeout=10)
            assert result.strip()==b"DROP_ACCEPTED:5",planned_extended.text
            chat_bytes = subprocess.check_output(["dotnet",str(program),"--ui-chat"],timeout=10)
            chatted = await client.post("/api/chat",content=chat_bytes,headers={"Content-Type":"application/json"})
            assert chatted.status_code==200 and chatted.json()["queued"],chatted.text
            await app.state.bridge.planner.close()
    print("PASS: actual C# wire serializer -> Python HTTP validation -> follow/mocked AI move -> C# decoder and policy")
    print("PASS: in-game panel DTOs -> provider settings/models/check/stop -> Python API -> C# UI decoders")
    print("PASS: background plan -> destination/run/plan ID -> C# policy")
    print("PASS: all action names match; actual C# extended snapshot -> quantity drop decision -> C# policy; ordinary chat DTO")


if __name__ == "__main__":
    asyncio.run(main())
