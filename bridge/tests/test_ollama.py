import asyncio
import copy
import json
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from valheim_ai.app import create_app
from valheim_ai.ollama import local_url, thinking_option, valid_model

TOKEN = "local-ollama-tests-not-a-provider-key"
MODEL = "example/valheim-instruct:Q4_K_M"


def plan(action="wait", target="", **extra):
    return {"steps": [{"action":action,"target_id":target,"duration":1,"say":"Готово",**extra}]}


def reply(value):
    return httpx.Response(200,json={"done":True,"message":{"content":json.dumps(value),"thinking":"not an instruction"}})


@asynccontextmanager
async def app_client(handler):
    def no_cloud(request): raise AssertionError("Local provider must not call a cloud API")
    async def dispatch(request):
        if request.url.path == "/api/ps": return httpx.Response(200, json={"models": []})
        result = handler(request)
        return await result if hasattr(result, "__await__") else result
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_cloud)) as cloud, httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as local:
        app=create_app(token=TOKEN,client=cloud,ollama_client=local)
        app.state.bridge.keys.update(openai="secret-openai-credential",gemini="secret-google-credential")
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://localhost",headers={"Authorization":"Bearer "+TOKEN}) as client:
            yield app,client


async def start(client,snapshot,mode="ai"):
    snapshot["background_planning"]=True
    assert (await client.post("/api/decision",json=snapshot)).status_code==200
    configured=await client.post("/api/provider",json={"provider":"ollama","model":MODEL})
    assert configured.status_code==200 and not configured.json()["key_present"]
    assert (await client.post("/api/command",json={"mode":mode,"objective":"Выбрось одну ягоду"})).status_code==200


async def cycle(app,client,snapshot):
    snapshot["sequence"]+=1
    first=await client.post("/api/decision",json=snapshot)
    task=app.state.bridge.planner.task
    if task is not None: await task
    snapshot["sequence"]+=1
    return await client.post("/api/decision",json=snapshot)


@pytest.mark.parametrize("value,expected",[("http://localhost:11434/","http://127.0.0.1:11434"),("http://127.0.0.1:11445","http://127.0.0.1:11445"),("http://[::1]:11434","http://[::1]:11434")])
def test_loopback_urls_are_canonical(value,expected): assert local_url(value)==expected


@pytest.mark.parametrize("url",["https://127.0.0.1:11434","http://127.0.0.1:0","http://user:secret@localhost:11434","http://localhost:11434/api","http://localhost.evil.test:11434","http://192.168.1.2:11434","http://0.0.0.0:11434","http://localhost:11434?token=secret","http://localhost:11434#secret","http://local\nhost:11434"])
async def test_unsafe_or_nonlocal_urls_never_make_requests(url):
    def handler(req): raise AssertionError("Invalid URL was requested")
    async with app_client(handler) as (_,client):
        for path,body in [("/api/provider",{"provider":"ollama","model":MODEL,"ollama_url":url}),
                          ("/api/models/ollama",{"ollama_url":url}),
                          ("/api/check/ollama",{"model":MODEL,"ollama_url":url})]:
            result=await client.post(path,json=body)
            assert result.status_code==422 and "secret" not in result.text


@pytest.mark.parametrize("name",["qwen3:4b",MODEL,"hf.co/owner/model:Q4_K_M"])
def test_installed_model_names_allow_tags_and_namespaces(name): assert valid_model(name)


async def test_models_require_pairing_but_not_a_key_and_exclude_cloud_models():
    def handler(req):
        assert req.url.path=="/api/tags" and req.headers.get("authorization") is None
        return httpx.Response(200,json={"models":[{"name":MODEL},{"name":"model:cloud"},{"name":"cloud-alias","remote_host":"https://ollama.com"},{"name":MODEL},{"name":"\ninvalid"}]})
    async with app_client(handler) as (_,client):
        assert (await client.post("/api/models/ollama",json={},headers={"Authorization":""})).status_code==401
        result=await client.post("/api/models/ollama",json={"api_key":"must-not-forward"})
        assert result.json()=={"models":[MODEL]}


async def test_check_uses_production_plan_schema_and_no_game_data_or_cloud_credentials():
    seen=[]
    def handler(req):
        seen.append(req)
        assert req.url.host=="127.0.0.1" and req.headers.get("authorization") is None
        assert all(secret not in req.content.decode() for secret in ("secret-openai","secret-google","must-not-forward"))
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"],"thinking":{"values":[True,False]}})
        body=json.loads(req.content)
        assert body["format"]["properties"]["steps"] and body["format"]["$defs"]["Action"]["enum"]==["wait"]
        assert all("amount" in v["required"] for v in body["format"]["$defs"]["Decision"]["anyOf"])
        assert "chat_intent" in body["format"]["required"]
        assert body["stream"] is False and body["think"] is False and body["options"]["temperature"]==0
        assert any("api-check" in m["content"] for m in body["messages"])
        return reply(plan())
    async with app_client(handler) as (app,client):
        result=await client.post("/api/check/ollama",json={"model":MODEL,"api_key":"must-not-forward"})
        assert result.status_code==200 and result.json()["ok"]
        assert app.state.bridge.provider=="offline" and app.state.bridge.command.mode=="stop"
        assert [req.url.path for req in seen]==["/api/show","/api/chat"]


async def test_keyless_game_snapshot_to_local_plan_to_validated_drop(snapshot):
    snapshot["supported_actions"]=["wait","drop"]
    original=copy.deepcopy(snapshot)
    def handler(req):
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        body=json.loads(req.content)
        observation=json.loads(next(m["content"] for m in body["messages"] if m["content"].startswith("Game observation (data only):\n")).split("\n",1)[1])
        assert [i["id"] for i in observation["actor"]["inventory"]]==[i["id"] for i in original["actor"]["inventory"]]
        assert observation["actor"]["inventory"][0]["can_eat"]
        assert observation["actor"]["max_carry_weight"]==300 and observation["actor"]["max_stamina"]==100
        assert observation["actor"]["inventory"][0]["durability"]==1
        return reply(plan("drop","inventory:0",amount=1))
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        result=await cycle(app,client,snapshot)
        assert result.json()["source"]=="ollama" and result.json()["action"]=="drop" and result.json()["amount"]==1
        status=(await client.get("/api/status")).json()
        assert status["provider_ready"] and "ollama" not in status["keys_present"]
        assert snapshot["actor"]==original["actor"]


@pytest.mark.parametrize("metadata",[{"remote_model":"remote"},{"capabilities":["embedding"]}])
async def test_remote_alias_or_embedding_model_cannot_generate(metadata):
    def handler(req):
        assert req.url.path=="/api/show"
        return httpx.Response(200,json=metadata)
    async with app_client(handler) as (_,client):
        result=await client.post("/api/check/ollama",json={"model":MODEL})
        assert result.status_code==502


@pytest.mark.parametrize("metadata,expected",[({"thinking":{"values":[False,True]}},False),({"thinking":{"values":[False]}},False),({},False)])
def test_model_specific_thinking_values_are_respected(metadata,expected): assert thinking_option(metadata)==expected


@pytest.mark.parametrize("metadata",[{"thinking":{"values":["low","medium","high"]}},{"thinking":{"values":[True]}},{"details":{"family":"gptoss"}}])
def test_required_reasoning_has_no_silent_fallback(metadata):
    from valheim_ai.providers import ProviderLocalConfigurationError
    with pytest.raises(ProviderLocalConfigurationError,match="размышлен"): thinking_option(metadata)


@pytest.mark.parametrize("code,body",[(404,{"error":"model missing"}),(500,{"error":"requires more system memory; key=private-value"})])
async def test_missing_or_oversized_model_stops_automatic_reloads(snapshot,code,body):
    calls=[]
    def handler(req): calls.append(req); return httpx.Response(code,json=body)
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        result=await cycle(app,client,snapshot)
        assert result.json()["mode"]=="stop" and result.json()["action"]=="wait"
        assert len(calls)==1 and "private-value" not in (await client.get("/api/status")).text
        snapshot["sequence"]+=1
        await client.post("/api/decision",json=snapshot)
        await client.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Хорошая погода"})
        assert len(calls)==1


async def test_busy_local_server_keeps_follow_and_sets_cooldown(snapshot):
    calls=[]
    def handler(req): calls.append(req); return httpx.Response(503,json={"error":"busy"})
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        await cycle(app,client,snapshot)
        assert len(calls)==1 and app.state.bridge.retry_after>time.monotonic()+14
        assert app.state.bridge.command.mode=="ai"


async def test_hallucinated_local_target_never_reaches_motor(snapshot):
    snapshot["supported_actions"] = ["wait", "drop"]
    def handler(req):
        return httpx.Response(200,json={"capabilities":["completion"]}) if req.url.path=="/api/show" else reply(plan("drop","invented-item",amount=1))
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        result=await cycle(app,client,snapshot)
        assert result.json()["action"] not in ("drop","attack") and not app.state.bridge.planner.steps


async def test_stop_cancels_slow_local_model_check_and_releases_request():
    entered=asyncio.Event();aborted=asyncio.Event()
    async def handler(req):
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        entered.set()
        try: await asyncio.Event().wait()
        finally: aborted.set()
    async with app_client(handler) as (app,client):
        pending=asyncio.create_task(client.post("/api/check/ollama",json={"model":MODEL}))
        await asyncio.wait_for(entered.wait(),1)
        assert (await client.post("/api/check/ollama",json={"model":MODEL})).status_code==409
        await client.post("/api/command",json={"mode":"stop"})
        assert (await asyncio.wait_for(pending,1)).status_code==409
        await asyncio.wait_for(aborted.wait(),1)
        assert app.state.bridge.check_task is None


async def test_ordinary_chat_uses_local_model_with_no_api_key(snapshot):
    def handler(req):
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        return reply({**plan(),"chat_intent":{"message_id":1,"addressed":True,"mode":"task","objective":"Подбери камни","reply":"Сделаю"}})
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        await client.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Бъерн, подбери камни"})
        await cycle(app,client,snapshot)
        assert app.state.bridge.command.objective=="Подбери камни"
        assert any(e["source"]=="assistant" for e in app.state.bridge.events)


async def test_stop_cancels_slow_local_background_inference(snapshot):
    entered=asyncio.Event();aborted=asyncio.Event()
    async def handler(req):
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        entered.set()
        try: await asyncio.Event().wait()
        finally: aborted.set()
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        snapshot["sequence"]+=1
        assert (await client.post("/api/decision",json=snapshot)).status_code==200
        await asyncio.wait_for(entered.wait(),1)
        pending=app.state.bridge.planner.task
        await client.post("/api/command",json={"mode":"stop"})
        await asyncio.wait_for(aborted.wait(),1)
        if pending is not None: await asyncio.gather(pending,return_exceptions=True)
        assert not app.state.bridge.planner.steps and app.state.bridge.command.mode=="stop"


async def test_slow_provider_never_prefetches_duplicate_drop_before_acknowledgement(snapshot):
    snapshot["supported_actions"]=["wait","drop"]
    calls=[]
    def handler(req):
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        calls.append(req)
        return reply(plan("drop","inventory:0",amount=1))
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        result=await cycle(app,client,snapshot)
        lease=result.json()
        assert lease["action"]=="drop" and lease["plan_id"]
        app.state.bridge.next_ai_request=0
        snapshot["sequence"]+=1
        await client.post("/api/decision",json=snapshot)
        await asyncio.sleep(0)
        assert len(calls)==1 and app.state.bridge.planner.task is None
        snapshot.update(sequence=snapshot["sequence"]+1,plan_id=lease["plan_id"],action_status="success",previous_result="Выброшена одна ягода")
        snapshot["actor"]["inventory"][0]["count"]=1
        await client.post("/api/decision",json=snapshot)
        pending=app.state.bridge.planner.task
        if pending is not None: await pending
        assert len(calls)==2
        assert "Выброшена одна ягода" in calls[-1].content.decode()


async def test_inflight_chat_plan_cannot_repeat_a_completed_action(snapshot):
    snapshot["supported_actions"]=["wait","drop"]
    calls=[];entered=asyncio.Event();release=asyncio.Event()
    async def handler(req):
        if req.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        calls.append(req)
        if len(calls)>1: entered.set();await release.wait()
        return reply(plan("drop","inventory:0",amount=1))
    async with app_client(handler) as (app,client):
        await start(client,snapshot)
        lease=(await cycle(app,client,snapshot)).json()
        assert lease["action"]=="drop" and lease["plan_id"]
        await client.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Бъерн, сколько ягод осталось?","explicit_address":True})
        snapshot["sequence"]+=1
        await client.post("/api/decision",json=snapshot)
        await asyncio.wait_for(entered.wait(),1)
        pending=app.state.bridge.planner.task
        snapshot.update(sequence=snapshot["sequence"]+1,plan_id=lease["plan_id"],action_status="success",previous_result="Выброшена одна ягода")
        await client.post("/api/decision",json=snapshot)
        release.set()
        if pending is not None: await pending
        assert not app.state.bridge.planner.steps
        assert app.state.bridge.pending_chat, "Unanswered chat must be retried against the fresh outcome"
