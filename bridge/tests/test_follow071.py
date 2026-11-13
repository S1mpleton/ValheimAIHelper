import asyncio
import json
import time

import httpx

from test_chat040 import client, setup, finish_plan
from valheim_ai.protocol import Snapshot, Decision
from valheim_ai.task_policy import COMPANION_OBJECTIVE, planning_view


def response(step):
    return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"steps":[step]})}}]})


async def test_no_leader_companion_waits_with_valid_lease_without_wandering_or_calling_model(snapshot):
    calls=[]
    async def provider(req): calls.append(req); return response({"action":"move","target_id":"","duration":1,"say":"Вперёд"})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        snapshot["leader_id"]=""
        snapshot["leader_status"]="unavailable"
        await c.post("/api/command",json={"mode":"ai","objective":COMPANION_OBJECTIVE})
        for _ in range(5):
            snapshot["sequence"]+=1
            action=(await c.post("/api/decision",json=snapshot)).json()
            assert action["action"]=="wait" and action["duration"]==3
            assert "Лидер не виден" in action["say"]
        assert not calls and app.state.bridge.planner.task is None


async def test_follow_skill_renews_for_moving_leader_without_periodic_model_calls(snapshot):
    calls=[]
    async def provider(req):
        calls.append(req)
        return response({"action":"follow","target_id":"leader","duration":1,"say":"Следую"})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        action=(await c.post("/api/decision",json=snapshot)).json()
        assert action["action"]=="follow"
        for x in (5,10,30,70,100):
            app.state.bridge.next_ai_request=0
            leader=next(e for e in snapshot["entities"] if e["id"]=="leader")
            leader.update(distance=x,position={"x":x,"y":0,"z":0})
            snapshot.update(sequence=snapshot["sequence"]+1,plan_id=action["plan_id"],action_status="running",motor_state="follow:walk_path")
            renewed=(await c.post("/api/decision",json=snapshot)).json()
            assert renewed["action"]=="follow" and renewed["plan_id"]==action["plan_id"]
        assert len(calls)==1 and app.state.bridge.planner.task is None


async def test_pending_chat_can_interrupt_stable_follow_and_reach_model(snapshot):
    calls=[]
    async def provider(req):
        calls.append(req)
        return response({"action":"follow","target_id":"leader","duration":1,"say":"Следую"})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await finish_plan(app,c,snapshot)
        await c.post("/api/chat",json={"actor_name":"NPC","speaker":"Лидер","text":"И ещё подбери камни"})
        app.state.bridge.next_ai_request=0
        await finish_plan(app,c,snapshot)
        assert len(calls)==2
        assert "И ещё подбери камни" in json.loads(calls[-1].content)["messages"][-1]["content"]


def test_known_companion_duty_uses_follow_skill_and_free_form_tasks_keep_semantic_actions(snapshot):
    snapshot["supported_actions"]=["wait","follow","move","jump","look","pickup","interact","speak"]
    world=Snapshot.model_validate(snapshot)
    scoped=planning_view(COMPANION_OBJECTIVE,world,())
    assert "follow" in scoped.supported_actions
    assert not {"move","jump","pickup","interact"}.intersection(scoped.supported_actions)
    assert "pickup" in planning_view("Гоша, собери гриб",world,()).supported_actions
    assert "pickup" in planning_view(COMPANION_OBJECTIVE,world,({"text":"подбери"},)).supported_actions


async def test_wait_without_leader_in_native_follow_has_three_second_control_lease(snapshot):
    async def provider(req): raise AssertionError("No model needed")
    async with client(provider) as (app,c):
        await setup(c,snapshot,"follow")
        snapshot.update(sequence=snapshot["sequence"]+1,leader_id="")
        action=(await c.post("/api/decision",json=snapshot)).json()
        assert action["action"]=="wait" and action["duration"]==3


async def test_real_game_pause_does_not_consume_task_execution_budget(snapshot,monkeypatch):
    from valheim_ai import planning
    from types import SimpleNamespace
    now=[100.]
    snapshot["supported_actions"]=["wait","follow","move"]
    monkeypatch.setattr(planning,"time",SimpleNamespace(monotonic=lambda:now[0]))
    async def provider(req): return response({"action":"move","target_id":"","duration":1,"say":"Иду"})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        action=(await c.post("/api/decision",json=snapshot)).json()
        for stamp in (101,120,150,180):
            now[0]=stamp
            snapshot.update(sequence=snapshot["sequence"]+1,simulation_paused=True,plan_id=action["plan_id"],action_status="running")
            held=(await c.post("/api/decision",json=snapshot)).json()
            assert held["plan_id"]==action["plan_id"] and held["action"]=="move"
        assert not app.state.bridge.planner.history
