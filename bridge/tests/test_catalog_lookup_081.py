"""On-demand item-type lookup never becomes evidence of available stock."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from valheim_ai.dialogue import Dialogue
from valheim_ai.ollama import ModelSession
from valheim_ai.performance import Performance
from valheim_ai.protocol import Snapshot
from valheim_ai.providers import ProviderInvalidAction, ProviderUnavailable
from valheim_ai.task_compiler import compile_tasks
from valheim_ai.task_pool import TaskPool, TaskSpec


PROVIDERS = ("ollama", "openai", "gemini")


def world(snapshot):
    snapshot["item_catalog"] = [
        dict(item_prefab="AxeStone", name="Каменный топор", token="$item_axe_stone", category="Weapon"),
        dict(item_prefab="Raspberry", name="Малина", token="$item_raspberry", category="Consumable"),
        dict(item_prefab="Stone", name="Камень", token="$item_stone", category="Material"),
    ]
    snapshot["supported_actions"] = ["wait", "follow", "pickup", "speak"]
    return Snapshot.model_validate(snapshot)


def state_for(client, provider):
    pool = TaskPool()
    pool.apply_batch([TaskSpec(kind="follow", priority=9)])
    return SimpleNamespace(
        provider=provider,
        model={"ollama":"lookup-agent:4b", "openai":"gpt-6-luna", "gemini":"gemini-2.5-flash"}[provider],
        ollama_url="http://127.0.0.1:11434", keys={"openai":"test-openai", "gemini":"test-gemini"},
        inference_tasks=set(), performance=Performance(), dialogue=Dialogue(lambda: ["test-openai", "test-gemini"]),
        ollama_session=ModelSession(lambda *_: None), client_for=lambda _:client,
        decision_timeout=10, task_pool=pool,
    )


def facts(request, provider):
    body = json.loads(request.content)
    text = body["contents"][0]["parts"][0]["text"] if provider == "gemini" else body["messages"][-1]["content"]
    return json.loads(text)


def response(provider, value):
    raw = json.dumps(value, ensure_ascii=False)
    if provider == "ollama": return httpx.Response(200, json={"done":True, "message":{"content":raw}})
    if provider == "openai": return httpx.Response(200, json={"choices":[{"message":{"content":raw}}]})
    return httpx.Response(200, json={"candidates":[{"content":{"parts":[{"text":raw}]}}]})


def metadata(request):
    if request.url.path == "/api/ps": return httpx.Response(200, json={"models":[]})
    if request.url.path == "/api/show": return httpx.Response(200, json={"capabilities":["completion"]})
    return None


def saved_pool(state):
    return [node.model_dump(mode="json") for node in state.task_pool.tasks]


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_lookup_exposes_unobserved_type_then_preserves_model_priority_without_inventing_stock(snapshot, provider):
    observed, calls = world(snapshot), []
    def handler(request):
        extra = metadata(request)
        if extra is not None: return extra
        data = facts(request, provider)
        calls.append(data)
        names = {item["item_prefab"] for item in data["item_type_catalog"]}
        if len(calls) == 1:
            assert "Raspberry" in names and "AxeStone" not in names
            return response(provider, {"tasks":[], "edits":[], "catalog_queries":["каменный топор"]})
        assert "AxeStone" in names
        assert data["catalog_lookup_results"][0]["matches"][0]["item_prefab"] == "AxeStone"
        assert not any(entity.get("item_prefab") == "AxeStone" for entity in data["observed_entities"])
        return response(provider, {"tasks":[{"kind":"collect", "priority":13, "item_prefabs":["AxeStone"], "amount":1}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        before = saved_pool(state)
        batch = await compile_tasks(state, "Найди каменный топор", observed)
        assert len(calls) == 2 and batch.tasks[0].priority == 13
        assert saved_pool(state) == before and not state.inference_tasks
        traces = list(state.dialogue.records.values())
        assert [entry.purpose for entry in traces] == ["tasks", "catalog-lookup", "tasks"]
        assert all(entry.status == "ok" for entry in traces)
        executor_pool = TaskPool()
        executor_pool.apply_batch(batch, snapshot=observed)
        decision, _, _ = executor_pool.next_decision(observed)
        assert decision.action.value == "wait" and not decision.target_id
        assert executor_pool.tasks[0].status == "blocked"


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_ordinary_question_uses_one_generation_and_no_catalog_search(snapshot, provider):
    calls = []
    def handler(request):
        extra = metadata(request)
        if extra is not None: return extra
        calls.append(facts(request, provider))
        return response(provider, {"tasks":[{"kind":"action", "priority":41, "action":{
            "action":"speak", "target_id":"", "duration":1, "say":"Отвечаю", "text":"У меня 25 здоровья."}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        batch = await compile_tasks(state, "Сколько у тебя здоровья?", world(snapshot))
        assert len(calls) == 1 and batch.tasks[0].priority == 41
        assert all(entry.purpose != "catalog-lookup" for entry in state.dialogue.records.values())
        assert not state.inference_tasks


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_lookup_cannot_be_mixed_with_new_work(snapshot, provider):
    calls = []
    def handler(request):
        extra = metadata(request)
        if extra is not None: return extra
        calls.append(request)
        return response(provider, {"tasks":[{"kind":"follow", "priority":80}], "catalog_queries":["топор"]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        before = saved_pool(state)
        with pytest.raises(ProviderInvalidAction, match="нельзя смешивать"):
            await compile_tasks(state, "Найди топор", world(snapshot))
        assert len(calls) == 2 and saved_pool(state) == before and not state.inference_tasks


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_lookup_loop_is_bounded_to_two_search_passes(snapshot, provider):
    calls = []
    def handler(request):
        extra = metadata(request)
        if extra is not None: return extra
        calls.append(request)
        return response(provider, {"tasks":[], "edits":[], "catalog_queries":["топор"]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        before = saved_pool(state)
        with pytest.raises(ProviderInvalidAction, match="два обращения"):
            await compile_tasks(state, "Найди топор", world(snapshot))
        assert len(calls) == 3 and saved_pool(state) == before and not state.inference_tasks
        assert sum(entry.purpose == "catalog-lookup" for entry in state.dialogue.records.values()) == 2


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_unknown_dictionary_query_is_reported_without_applying_work(snapshot, provider):
    calls = []
    def handler(request):
        extra = metadata(request)
        if extra is not None: return extra
        calls.append(request)
        return response(provider, {"tasks":[], "edits":[], "catalog_queries":["абракадабра"]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, provider)
        before = saved_pool(state)
        with pytest.raises(ProviderInvalidAction, match="нет совпадения"):
            await compile_tasks(state, "Найди абракадабру", world(snapshot))
        assert len(calls) == 1 and saved_pool(state) == before and not state.inference_tasks


async def test_generation_after_lookup_shares_the_original_deadline(snapshot, monkeypatch):
    # A fake compiler clock consumes 9.99 seconds of its ten-second budget at
    # lookup, without a wall-clock sleep or changing asyncio's own clock.
    import valheim_ai.task_compiler as compiler
    clock = iter((100., 100., 109.99))
    monkeypatch.setattr(compiler, "time", SimpleNamespace(monotonic=lambda:next(clock)))
    calls = []
    async def handler(request):
        extra = metadata(request)
        if extra is not None: return extra
        calls.append(request)
        if len(calls) == 1:
            return response("ollama", {"tasks":[], "edits":[], "catalog_queries":["топор"]})
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state = state_for(client, "ollama")
        before = saved_pool(state)
        with pytest.raises(ProviderUnavailable, match="Время разбора"):
            await compile_tasks(state, "Найди топор", world(snapshot))
        assert len(calls) == 2 and saved_pool(state) == before and not state.inference_tasks
