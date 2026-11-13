import asyncio
import json

import httpx
import pytest

from test_chat040 import client, setup, finish_plan
from valheim_ai.local_schema import action_schema
from valheim_ai.protocol import Action, Decision, Snapshot, validate_decision


def bag(snapshot):
    original = snapshot["actor"]["inventory"][0]
    snapshot["actor"]["inventory"] = [
        dict(original, id="stone1", name="Камень", item_key="Stone:1:0", count=5),
        dict(original, id="stone2", name="Камень", item_key="Stone:1:0", count=3),
        dict(original, id="club", name="Дубина", item_key="Club:1:0", count=1, equipped=True),
        dict(original, id="quest", name="Квест", quest=True, count=1),
    ]
    snapshot["supported_actions"] = ["wait", "follow", "give", "drop", "pickup", "speak"]
    return Snapshot.model_validate(snapshot)


def test_exact_count_spans_matching_stacks_and_rejects_quest_and_forged_ids(snapshot):
    world = bag(snapshot)
    give = Decision(action="give", target_id="leader", item_id="stone1", amount=8, duration=1, say="Передам восемь")
    assert validate_decision(give, world) == give
    for changes in ({"amount": 9}, {"item_id": "quest"}, {"item_id": "unknown"}, {"target_id": "drop"}):
        with pytest.raises(ValueError): validate_decision(give.model_copy(update=changes), world)
    assert validate_decision(give.model_copy(update={"selection": "all_matching", "amount": 1}), world)


def test_all_inventory_is_one_macro_with_no_invented_item_id_and_preserves_quest_items(snapshot):
    world = bag(snapshot)
    give = Decision(action="give", target_id="leader", item_id="", selection="all_inventory", amount=1, duration=1, say="Передам всё")
    assert validate_decision(give, world) == give
    drop = give.model_copy(update={"action": Action.DROP, "target_id": ""})
    assert validate_decision(drop, world) == drop
    with pytest.raises(ValueError): validate_decision(drop.model_copy(update={"selection": "exact"}), world)
    with pytest.raises(ValueError): validate_decision(give.model_copy(update={"action": Action.SPEAK, "target_id": "", "text": "Привет"}), world)
    world.actor.inventory = [world.actor.inventory[-1]]
    with pytest.raises(ValueError): validate_decision(give, world)


def test_local_schema_has_separate_exact_and_bulk_variants(snapshot):
    world = bag(snapshot)
    variants = action_schema(world, True)["$defs"]["Decision"]["anyOf"]
    give = [v["properties"] for v in variants if v["properties"]["action"]["const"] == "give"]
    assert len(give) == 2
    assert give[0]["selection"]["enum"] == ["exact", "all_matching"]
    assert "" not in give[0]["item_id"]["enum"]
    assert give[1]["selection"]["const"] == "all_inventory"
    assert give[1]["item_id"]["const"] == "" and give[1]["target_id"]["enum"] == ["leader"]


async def test_paraphrase_waits_for_model_then_runs_semantically_selected_follow(snapshot):
    entered, release = asyncio.Event(), asyncio.Event()
    async def provider(req):
        entered.set(); await release.wait()
        assert "Гоша, пошли" in json.loads(req.content)["messages"][-1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"steps": [
            {"action": "follow", "target_id": "leader", "duration": 1, "say": "Иду за вами"}
        ]})}}]})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await c.post("/api/chat",json={"actor_name":"Гоша","speaker":"Лидер","text":"Гоша, пошли","explicit_address":True})
        snapshot["sequence"] += 1
        pending = (await c.post("/api/decision",json=snapshot)).json()
        assert pending["action"] == "wait" and pending["duration"] == 3
        await asyncio.wait_for(entered.wait(),1)
        task=app.state.bridge.planner.task
        release.set(); await task
        snapshot["sequence"] += 1
        result=(await c.post("/api/decision",json=snapshot)).json()
        assert result["action"] == "follow" and result["source"] == "openai"


async def test_new_address_replaces_stale_pending_commands_and_duplicate_does_not_restart_model(snapshot):
    entered=asyncio.Event()
    async def provider(req): entered.set(); await asyncio.Event().wait()
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        a={"actor_name":"Гоша","speaker":"Лидер","text":"Гоша, пошли","explicit_address":True}
        await c.post("/api/chat",json=a)
        snapshot["sequence"] += 1
        await c.post("/api/decision",json=snapshot)
        await asyncio.wait_for(entered.wait(),1)
        task=app.state.bridge.planner.task; revision=app.state.bridge.revision
        await c.post("/api/chat",json=a)
        assert app.state.bridge.planner.task is task and app.state.bridge.revision == revision
        await c.post("/api/chat",json=dict(a,text="Гоша, отдай все свои вещи"))
        assert app.state.bridge.revision > revision
        assert len(app.state.bridge.pending_chat) == 1
        assert app.state.bridge.pending_chat[0]["text"] == "Гоша, отдай все свои вещи"
        await asyncio.gather(task,return_exceptions=True)


async def test_bulk_delivery_finishes_only_after_native_confirmation_and_never_repeats(snapshot):
    bag(snapshot)
    async def provider(req):
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"steps":[
            {"action":"give","target_id":"leader","item_id":"","selection":"all_inventory","amount":1,"duration":1,"say":"Передам вещи"}
        ]})}}]})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        snapshot["supported_actions"]=["wait","give","follow"]
        await c.post("/api/chat",json={"actor_name":"Гоша","speaker":"Лидер","text":"Гоша, отдай всё","explicit_address":True})
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        action=(await c.post("/api/decision",json=snapshot)).json()
        assert action["action"]=="give" and action["selection"]=="all_inventory"
        snapshot.update(sequence=snapshot["sequence"]+1,plan_id=action["plan_id"],action_status="running")
        renewed=(await c.post("/api/decision",json=snapshot)).json()
        assert renewed["plan_id"]==action["plan_id"] and "отдай" in app.state.bridge.command.objective
        snapshot["actor"]["inventory"]=[]
        snapshot.update(sequence=snapshot["sequence"]+1,action_status="success",previous_result="Рядом с Лидер оставлено: Камень ×8, Дубина ×1. Всего 9.")
        result=(await c.post("/api/decision",json=snapshot)).json()
        assert result["action"]=="follow"
        assert "отдай" not in app.state.bridge.command.objective
        assert any(e["source"]=="assistant" and "Всего 9" in e["message"] for e in app.state.bridge.events)


async def test_partial_native_delivery_is_reported_and_original_quantity_is_not_replanned(snapshot):
    bag(snapshot)
    async def provider(req):
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"steps":[
            {"action":"give","target_id":"leader","item_id":"stone1","amount":7,"duration":1,"say":"Передам семь"}
        ]})}}]})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        snapshot["supported_actions"]=["wait","give","follow"]
        await c.post("/api/chat",json={"actor_name":"Гоша","speaker":"Лидер","text":"Гоша, дай семь камней","explicit_address":True})
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        action=(await c.post("/api/decision",json=snapshot)).json()
        assert action["action"]=="give"
        snapshot["actor"]["inventory"][0]["count"]=3
        snapshot.update(sequence=snapshot["sequence"]+1,plan_id=action["plan_id"],action_status="blocked",action_completed_amount=2,previous_result="Подтверждено 2; остальное у NPC")
        result=(await c.post("/api/decision",json=snapshot)).json()
        assert result["action"]=="follow"
        assert "дай семь" not in app.state.bridge.command.objective
        assert any(e["source"]=="assistant" and "частично (2)" in e["message"] for e in app.state.bridge.events)
