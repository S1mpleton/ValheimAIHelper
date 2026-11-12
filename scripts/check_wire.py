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
from valheim_ai.providers import OLLAMA_DECISION_TIMEOUT


async def main():
    project = ROOT / "mod/ValheimAIHelper.WireProbe/ValheimAIHelper.WireProbe.csproj"
    subprocess.run(["dotnet", "build", str(project), "-c", "Release", "--nologo", "--verbosity", "quiet"], check=True, timeout=60)
    program = project.parent / "bin/Release/net8.0/ValheimAIHelper.WireProbe.dll"
    plugin = ROOT / "mod/ValheimAIHelper/bin/Release/net48/ValheimAIHelper.dll"
    subprocess.run(["dotnet", str(program), "--check-game-access", str(plugin)], check=True, timeout=10)
    encoded = subprocess.check_output(["dotnet", str(program)], timeout=10)
    capabilities = json.loads(subprocess.check_output(["dotnet", str(program), "--capabilities"], timeout=10))
    assert int(subprocess.check_output(["dotnet",str(program),"--ollama-ui-timeout"],timeout=10))>OLLAMA_DECISION_TIMEOUT
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
    local_requests=[]
    def ollama_provider(request):
        if request.url.path=="/api/ps": return httpx.Response(200,json={"models":[]})
        assert request.url.host=="127.0.0.1" and request.headers.get("authorization") is None
        local_requests.append(request.url.path)
        if request.url.path=="/api/tags": return httpx.Response(200,json={"models":[{"name":"wire-agent:Q4"}]})
        if request.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"],"thinking":{"values":[False]}})
        body=json.loads(request.content)
        assert body["think"] is False and body["stream"] is False
        probe=body["format"]["$defs"]["Action"]["enum"]==["wait"]
        step={"action":"wait","target_id":"","duration":1,"say":"Проверка"} if probe else {"action":"drop","target_id":"inventory:stones","amount":5,"duration":1,"say":"Пять камней"}
        text=json.dumps(body["messages"],ensure_ascii=False)
        if not probe and "Дай лидеру" in text: step={"action":"give","target_id":"character:42","item_id":"inventory:stones","amount":5,"duration":1,"say":"Передам камни рядом с лидером"}
        if not probe and "Подбери камень вдали" in text: step={"action":"pickup","target_id":"drop:far","amount":1,"duration":1,"say":"Подберу стопку"}
        return httpx.Response(200,json={"message":{"content":json.dumps({"steps":[step]})},"done":True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream, httpx.AsyncClient(transport=httpx.MockTransport(ollama_provider)) as local:
        app = create_app(token=token, client=upstream,ollama_client=local)
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
            # Switch using exactly the native menu's DTO; no fake local API key.
            settings=subprocess.check_output(["dotnet",str(program),"--ui-ollama"],timeout=10)
            configured=await client.post("/api/provider",content=settings,headers={"Content-Type":"application/json"})
            assert configured.status_code==200 and not configured.json()["key_present"],configured.text
            assert (await client.post("/api/models/ollama",json={})).json()["models"]==["wire-agent:Q4"]
            assert (await client.post("/api/check/ollama",json={"model":"wire-agent:Q4"})).status_code==200
            status=await client.get("/api/status")
            assert subprocess.check_output(["dotnet",str(program),"--read-ollama-status"],input=status.content,timeout=10).strip()==b"OLLAMA_STATUS_OK"
            await client.post("/api/command",json={"mode":"ai","objective":"Выбрось 5 камней"})
            await client.post("/api/decision",json={**extended,"sequence":4})
            task=app.state.bridge.planner.task
            if task is not None: await task
            local_plan=await client.post("/api/decision",json={**extended,"sequence":5})
            assert local_plan.json()["source"]=="ollama",local_plan.text
            assert subprocess.check_output(["dotnet",str(program),"--check-extended-response","5"],input=local_plan.content,timeout=10).strip()==b"DROP_ACCEPTED:5"
            await app.state.bridge.planner.close()
            for seq,goal,decoder,expected in [(6,"Дай лидеру 5 камней","--check-give-extended-response",b"GIVE_ACCEPTED:5"),
                                               (8,"Подбери камень вдали","--check-pickup-extended-response",b"PICKUP_ACCEPTED:45m")]:
                await client.post("/api/command",json={"mode":"ai","objective":goal})
                await client.post("/api/decision",json={**extended,"sequence":seq})
                task=app.state.bridge.planner.task
                if task is not None: await task
                decision=await client.post("/api/decision",json={**extended,"sequence":seq+1})
                assert subprocess.check_output(["dotnet",str(program),decoder,str(seq+1)],input=decision.content,timeout=10).strip()==expected,decision.text
                await app.state.bridge.planner.close()
            dialogue=await client.get("/api/dialogue")
            assert subprocess.check_output(["dotnet",str(program),"--read-dialogue-index"],input=dialogue.content,timeout=10).strip()==b"DIALOGUE_INDEX_OK"
            id=dialogue.json()["entries"][0]["id"]
            page=await client.get(f"/api/dialogue/{id}?part=response")
            assert subprocess.check_output(["dotnet",str(program),"--read-dialogue-page"],input=page.content,timeout=10).strip()==b"DIALOGUE_PAGE_OK"
            exported=await client.get(f"/api/dialogue/{id}/export")
            assert subprocess.check_output(["dotnet",str(program),"--read-dialogue-export"],input=exported.content,timeout=10).strip()==b"DIALOGUE_EXPORT_OK"
    print("PASS: actual C# wire serializer -> Python HTTP validation -> follow/mocked AI move -> C# decoder and policy")
    print("PASS: in-game panel DTOs -> provider settings/models/check/stop -> Python API -> C# UI decoders")
    print("PASS: background plan -> destination/run/plan ID -> C# policy")
    print("PASS: all action names match; actual C# extended snapshot -> quantity drop decision -> C# policy; ordinary chat DTO")
    print("PASS: native Ollama settings/status DTOs -> local model list/check/JSON plan -> C# quantity policy; thinking disabled")
    print("PASS: C# loaded ground stack (45m/count=7) -> local pickup plan; give exactly 5 from observed inventory to player -> C# validation")
    print("PASS: actual model request/raw reply/validation dialogue -> paged Python endpoints -> C# index/page/export DTOs")


if __name__ == "__main__":
    asyncio.run(main())
