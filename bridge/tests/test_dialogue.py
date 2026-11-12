import asyncio
import json

import httpx
import pytest

from test_ollama import app_client, cycle, plan, reply, start, MODEL, TOKEN
from valheim_ai.dialogue import Dialogue, PART_LIMIT, EXPORT_CHUNK
from valheim_ai.app import create_app


async def export(c, id):
    offset = 0; chunks = []
    for _ in range(64):
        response = await c.get(f"/api/dialogue/{id}/export?offset={offset}")
        assert response.status_code == 200 and len(response.content) < 131072
        value = response.json(); assert value["offset"] == offset
        chunks.append(value["text"]); offset = value["next_offset"]
        if value["complete"]: return "".join(chunks)
    raise AssertionError("Export did not finish")


async def test_actual_request_and_raw_reply_are_visible_before_normalization_without_extra_model_calls(snapshot):
    captured = []; raw = json.dumps(plan("pickup", "drop", say="Я уже всё подобрал"), ensure_ascii=False)
    def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        captured.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": raw, "thinking": "DO-NOT-RECORD-THINKING"}})
    async with app_client(handler) as (app, c):
        await start(c, snapshot); result = await cycle(app, c, snapshot)
        assert result.json()["say"].startswith("Подхожу к выпавшей стопке")
        index = (await c.get("/api/dialogue")).json(); entry = index["entries"][0]
        record = app.state.bridge.dialogue.records[entry["id"]]
        assert json.loads(record.request) == captured[0]
        assert record.response == raw and "Я уже всё подобрал" in record.response
        assert "Подхожу к выпавшей стопке" in record.result and "Проверка плана: OK" in record.result
        text = await export(c, entry["id"])
        assert "DO-NOT-RECORD-THINKING" not in text and "Authorization" not in record.request
        assert '"think": false' in text and len(captured) == 1
        assert app.state.bridge.command.mode == "ai"


async def test_invalid_json_keeps_original_reply_and_separate_validation_reason(snapshot):
    raw = '{"steps": [ invalid JSON <script>alert(1)</script>'
    def handler(req):
        return httpx.Response(200, json={}) if req.url.path == "/api/show" else httpx.Response(200, json={"message": {"content": raw}})
    async with app_client(handler) as (app, c):
        await start(c, snapshot); await cycle(app, c, snapshot)
        entry = (await c.get("/api/dialogue")).json()["entries"][0]
        assert entry["status"] == "error"
        text = await export(c, entry["id"])
        assert raw in text and "json_invalid" in text


async def test_prompt_visible_while_model_waits_and_stop_marks_exchange_cancelled(snapshot):
    entered = asyncio.Event()
    async def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        entered.set(); await asyncio.Event().wait()
    async with app_client(handler) as (app, c):
        await start(c, snapshot); snapshot["sequence"] += 1
        await c.post("/api/decision", json=snapshot)
        await asyncio.wait_for(entered.wait(), 1)
        entry = (await c.get("/api/dialogue")).json()["entries"][0]
        assert entry["status"] == "running" and app.state.bridge.command.mode == "ai"
        request = (await c.get(f"/api/dialogue/{entry['id']}?part=request")).json()
        assert '"model"' in request["text"]
        assert (await c.get(f"/api/dialogue/{entry['id']}/export")).status_code == 409
        task = app.state.bridge.planner.task
        await c.post("/api/command", json={"mode": "stop"})
        if task is not None: await asyncio.gather(task, return_exceptions=True)
        entry = (await c.get("/api/dialogue")).json()["entries"][0]
        assert entry["status"] == "cancelled"
        assert "Запрос отменён" in await export(c, entry["id"])


async def test_credentials_are_redacted_from_request_response_and_export(snapshot):
    secrets = [TOKEN, "secret-openai-credential", "secret-google-credential", "sk-proj-" + "FAKEVALUE" * 8]
    def handler(req):
        return httpx.Response(200, json={}) if req.url.path == "/api/show" else reply(plan(say=";".join(secrets)))
    async with app_client(handler) as (app, c):
        await start(c, snapshot)
        app.state.bridge.command = app.state.bridge.command.model_copy(update={"objective": "Проверка: " + ";".join(secrets)})
        await cycle(app, c, snapshot)
        entry = (await c.get("/api/dialogue")).json()["entries"][0]
        text = await export(c, entry["id"])
        assert "[REDACTED]" in text
        assert all(secret not in text for secret in secrets)


async def test_dialogue_endpoints_require_pairing_and_parts_cannot_read_private_fields():
    async with app_client(lambda req: reply(plan())) as (_, c):
        for path in ("/api/dialogue", "/api/dialogue/1", "/api/dialogue/1/export"):
            assert (await c.get(path, headers={"Authorization": ""})).status_code == 401
        assert (await c.post("/api/dialogue/clear", json={}, headers={"Authorization": ""})).status_code == 401
        response = await c.get("/api/dialogue/1?part=credential-PRIVATE-SECRET")
        assert response.status_code == 422 and "PRIVATE-SECRET" not in response.text
        assert (await c.get("/api/dialogue/1?page=-1")).status_code == 422
        assert (await c.get("/api/dialogue/1/export?offset=-1")).status_code == 422


async def test_clearing_history_does_not_stop_agent_or_resurrect_inflight_exchange(snapshot):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(req):
        if req.url.path == "/api/show": return httpx.Response(200, json={})
        entered.set(); await release.wait(); return reply(plan())
    async with app_client(handler) as (app, c):
        await start(c, snapshot); snapshot["sequence"] += 1
        await c.post("/api/decision", json=snapshot); await asyncio.wait_for(entered.wait(), 1)
        task = app.state.bridge.planner.task
        assert (await c.post("/api/dialogue/clear", json={})).json()["ok"]
        release.set(); await task
        assert (await c.get("/api/dialogue")).json()["entries"] == []
        assert app.state.bridge.command.mode == "ai"


def test_retention_is_bounded_and_export_preserves_original_line_breaks():
    store = Dialogue(lambda: [])
    for _ in range(12): store.begin("ollama", MODEL, "plan").finish("ok", "accepted")
    assert len(store.records) == 8 and [e["id"] for e in store.index()["entries"]] == list(range(12, 4, -1))
    record = store.records[12]
    raw = 'Первая строка\n  "Камень" ×5\n' + "🙂" * 18000
    record.set("response", raw)
    combined = ""; offset = 0
    while True:
        part = store.export(record, offset); combined += part["text"]; offset = part["next_offset"]
        assert len(part["text"]) <= EXPORT_CHUNK
        if part["complete"]: break
    assert raw in combined
    page = store.page(record, "response", 9999)
    assert page["page"] == page["pages"] - 1 and len(page["text"]) < 2000


def test_redaction_precedes_truncation_and_live_key_is_not_retained_after_finish():
    key = "custom-key-with-no-provider-prefix"
    store = Dialogue(lambda: [])
    record = store.begin("openai", "gpt-test", "check", key)
    record.set("response", "x" * (PART_LIMIT - 10) + key + " tail" * 20)
    record.finish("error", "Bad response for " + key)
    assert key not in record.response and key not in record.result and record.credential == ""
    assert record.truncated and "ОБРЕЗАНО" in record.response


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_cloud_request_capture_matches_sent_body_without_headers_or_thought_fields(snapshot, provider):
    captured = []; raw = json.dumps(plan())
    def handler(req):
        captured.append(json.loads(req.content))
        if provider == "openai": return httpx.Response(200, json={"choices": [{"message": {"content": raw, "reasoning_content": "PRIVATE-THOUGHT"}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"thought": True, "text": "PRIVATE-THOUGHT"}, {"text": raw}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"Authorization": "Bearer " + TOKEN}) as c:
            snapshot["background_planning"] = True
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": provider, "model": "test-model", "api_key": "opaque-provider-credential"})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1; await c.post("/api/decision", json=snapshot)
            task = app.state.bridge.planner.task
            if task is not None: await task
            entry = (await c.get("/api/dialogue")).json()["entries"][0]
            record = app.state.bridge.dialogue.records[entry["id"]]
            assert json.loads(record.request) == captured[0] and record.response == raw
            text = await export(c, entry["id"])
            assert "opaque-provider-credential" not in text and "PRIVATE-THOUGHT" not in text and "Authorization" not in record.request


async def test_api_error_detail_is_visible_without_echoed_key(snapshot):
    key = "opaque-provider-key-for-diagnostic-test"
    def handler(req): return httpx.Response(401, json={"error": {"message": "Invalid key: " + key}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"Authorization": "Bearer " + TOKEN}) as c:
            await c.post("/api/decision", json=snapshot)
            await c.post("/api/provider", json={"provider": "openai", "model": "test-model", "api_key": key})
            await c.post("/api/command", json={"mode": "ai"})
            snapshot["sequence"] += 1; await c.post("/api/decision", json=snapshot)
            entry = (await c.get("/api/dialogue")).json()["entries"][0]
            text = await export(c, entry["id"])
            assert key not in text and "401" in text and "Invalid key" in text
