import asyncio
import json
import time
from contextlib import asynccontextmanager
import httpx
import pytest

from valheim_ai.app import create_app

TOKEN = "context-chat-test-token-not-a-secret"


@asynccontextmanager
async def client(provider):
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        app = create_app(token=TOKEN, client=upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://localhost",headers={"Authorization":"Bearer "+TOKEN}) as c:
            try: yield app,c
            finally:
                app.state.bridge.cancel_chat()
                await app.state.bridge.planner.close()


def response(addressed, mode, objective="", reply="", steps=None):
    return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({
        "steps":steps or [{"action":"wait","target_id":"","duration":1,"say":""}],
        "chat_intent":{"message_id":1,"addressed":addressed,"mode":mode,"objective":objective,"reply":reply}
    })}}]})


async def setup(c, snapshot, mode="ai"):
    snapshot["background_planning"] = True
    await c.post("/api/decision",json=snapshot)
    await c.post("/api/provider",json={"provider":"openai","model":"gpt-6-luna","api_key":"mock-key"})
    if mode != "stop": await c.post("/api/command",json={"mode":mode,"objective":"Следуй и защищай"})


async def finish_plan(app, c, snapshot):
    snapshot["sequence"] += 1
    await c.post("/api/decision",json=snapshot)
    task = app.state.bridge.planner.task
    if task is not None: await task


async def test_ordinary_chat_is_batched_with_plan_and_unrelated_text_does_not_replace_objective(snapshot):
    calls=[]
    async def provider(req):
        calls.append(req)
        assert b"Leader conversation" in req.content
        return response(False,"ignore",objective="do not adopt")
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        posted=await c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Сегодня хороший день"})
        assert posted.json()["queued"] and not calls
        await finish_plan(app,c,snapshot)
        assert len(calls)==1 and app.state.bridge.command.objective=="Следуй и защищай"
        assert not app.state.bridge.pending_chat


async def test_contextual_followup_updates_mission_and_replies_in_game_event(snapshot):
    async def provider(req): return response(True,"task","Подбери камни и вернись","Соберу камни")
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"И ещё подбери эти камни"})
        await finish_plan(app,c,snapshot)
        assert app.state.bridge.command.objective=="Подбери камни и вернись"
        assert any(e["source"]=="assistant" and e["message"]=="Соберу камни" for e in app.state.bridge.events)


async def test_contextual_stop_returns_stop_mode_and_no_cached_action_survives(snapshot):
    async def provider(req): return response(True,"stop",reply="Остановился")
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Хватит, остановись"})
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        r=await c.post("/api/decision",json=snapshot)
        assert r.json()["mode"]=="stop" and r.json()["action"]=="wait"
        assert not app.state.bridge.planner.steps


async def test_classifier_when_native_following_returns_intent_without_enabling_ai(snapshot):
    async def provider(req): return response(True,"task","Собери камни","Сделаю")
    async with client(provider) as (app,c):
        await setup(c,snapshot,"follow")
        result=await c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Теперь собери камни"})
        assert result.json()["handled"] and result.json()["mode"]=="task"
        assert app.state.bridge.command.mode=="follow"


async def test_stop_cancels_slow_chat_classification_and_late_reply_cannot_wake_ai(snapshot):
    entered=asyncio.Event()
    async def provider(req):
        entered.set(); await asyncio.Event().wait()
    async with client(provider) as (app,c):
        await setup(c,snapshot,"follow")
        pending=asyncio.create_task(c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Иди сюда"}))
        await asyncio.wait_for(entered.wait(),1)
        await c.post("/api/command",json={"mode":"stop"})
        result=await asyncio.wait_for(pending,1)
        assert not result.json()["handled"] and app.state.bridge.command.mode=="stop"
        assert not app.state.bridge.chat_busy


async def test_chat_requires_pairing_and_history_is_bounded(snapshot):
    async def provider(req): raise AssertionError("No API call while unconfigured")
    async with client(provider) as (app,c):
        message={"actor_name":"Бъерн","speaker":"Лидер","text":"привет"}
        assert (await c.post("/api/chat",json=message,headers={"Authorization":""})).status_code==401
        for _ in range(20): await c.post("/api/chat",json=message)
        assert len(app.state.bridge.chat_history)==12


async def test_disabling_listener_cancels_pending_context_and_does_not_use_more_api(snapshot):
    entered=asyncio.Event()
    calls=[]
    async def provider(req):
        calls.append(req); entered.set(); await asyncio.Event().wait()
    async with client(provider) as (app,c):
        await setup(c,snapshot,"follow")
        message={"actor_name":"Бъерн","speaker":"Лидер","text":"Собери камни"}
        pending=asyncio.create_task(c.post("/api/chat",json=message))
        await asyncio.wait_for(entered.wait(),1)
        snapshot.update(sequence=2,listen_chat=False)
        assert (await c.post("/api/decision",json=snapshot)).status_code==200
        assert not (await asyncio.wait_for(pending,1)).json()["handled"]
        assert not (await c.post("/api/chat",json=message)).json()["queued"]
        assert len(calls)==1 and app.state.bridge.command.mode=="follow"


async def test_model_avoid_policy_is_sent_to_motor_with_ordinary_follow(snapshot):
    async def provider(req):
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"steps":[
            {"action":"follow","target_id":"leader","duration":3,"say":"Следую без боя"}],"combat_policy":"avoid"})}}]})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        result=(await c.post("/api/decision",json=snapshot)).json()
        assert result["action"]=="follow" and result["avoid_combat"]


async def test_confirmed_spoken_answer_becomes_conversation_context(snapshot):
    async def provider(req):
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"steps":[
            {"action":"speak","target_id":"","duration":1,"say":"Отвечаю","text":"У меня мало выносливости"}]})}}]})
    snapshot["supported_actions"]=["wait","speak"]
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        reply=(await c.post("/api/decision",json=snapshot)).json()
        snapshot.update(sequence=snapshot["sequence"]+1,plan_id=reply["plan_id"],action_status="success",previous_result="Передано чату")
        await c.post("/api/decision",json=snapshot)
        assert app.state.bridge.chat_history[-1]["text"]=="У меня мало выносливости"


async def test_chat_quota_suspends_automatic_classification_until_settings_change(snapshot):
    calls=[]
    async def provider(req):
        calls.append(req); return httpx.Response(429,json={"error":{"message":"quota for key=mock-key"}})
    async with client(provider) as (app,c):
        await setup(c,snapshot,"follow")
        message={"actor_name":"Бъерн","speaker":"Лидер","text":"Поможешь собрать камни?"}
        await c.post("/api/chat",json=message)
        app.state.bridge.next_ai_request=0
        await c.post("/api/chat",json=message)
        assert len(calls)==1 and app.state.bridge.chat_suspended
        assert app.state.bridge.command.mode=="follow"
        assert "mock-key" not in (await c.get("/api/status")).text


async def test_chat_503_keeps_native_follow_and_sets_cooldown(snapshot,monkeypatch):
    from valheim_ai import providers
    monkeypatch.setattr(providers,"RETRY_DELAYS",(0,0))
    calls=[]
    async def provider(req):
        calls.append(req); return httpx.Response(503,json={"error":{"message":"busy"}})
    async with client(provider) as (app,c):
        await setup(c,snapshot,"follow")
        await c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер","text":"Пойдёшь со мной?"})
        assert len(calls)==3 and app.state.bridge.retry_after > time.monotonic()+14
        assert app.state.bridge.command.mode=="follow"


async def test_named_question_preserves_mission_and_processed_chat_stays_in_context(snapshot):
    calls=[]
    async def provider(req):
        calls.append(json.loads(req.content))
        return response(True,"reply",reply="У меня два предмета в сумке")
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await c.post("/api/chat",json={"actor_name":"Бъерн","speaker":"Лидер",
            "text":"Бъерн, что у тебя в сумке?","explicit_address":True})
        await finish_plan(app,c,snapshot)
        assert app.state.bridge.command.objective=="Следуй и защищай"
        assert not app.state.bridge.pending_chat
        app.state.bridge.next_ai_request=0
        await finish_plan(app,c,snapshot)
        context=calls[1]["messages"][-1]["content"].split("Leader conversation:\n",1)[1]
        memory=json.loads(context)
        assert any(m["text"]=="Бъерн, что у тебя в сумке?" and not m["pending"] for m in memory)
        assert any(m["speaker"]=="assistant" and not m["pending"] for m in memory)
        assert sum(e["source"]=="assistant" for e in app.state.bridge.events)==1


async def test_completed_one_off_drop_returns_to_leader_instead_of_repeating(snapshot):
    calls=[]
    async def provider(req):
        calls.append(req)
        steps=[{"action":"drop","target_id":"inventory:0","amount":1,"duration":1,"say":"Отдам одну ягоду"}] if len(calls)==1 else [
            {"action":"wait","target_id":"","duration":1,"say":"Готово"}]
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({
            "steps":steps,"objective_complete":len(calls)>1})}}]})
    snapshot["supported_actions"]=["wait","follow","drop"]
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await c.post("/api/command",json={"mode":"ai","objective":"Выбрось одну ягоду"})
        await finish_plan(app,c,snapshot)
        snapshot["sequence"]+=1
        leased=(await c.post("/api/decision",json=snapshot)).json()
        assert leased["action"]=="drop" and leased["amount"]==1
        snapshot.update(sequence=snapshot["sequence"]+1,plan_id=leased["plan_id"],action_status="success",previous_result="Сброшено: 1")
        snapshot["actor"]["inventory"][0]["count"]=1
        app.state.bridge.next_ai_request=0
        await c.post("/api/decision",json=snapshot)
        pending=app.state.bridge.planner.task
        if pending is not None: await pending
        snapshot["sequence"]+=1
        result=(await c.post("/api/decision",json=snapshot)).json()
        assert result["action"]=="follow" and result["target_id"]=="leader"
        assert "Поручение выполнено" in app.state.bridge.command.objective
        assert any(e["source"]=="mission" for e in app.state.bridge.events)


async def test_model_cannot_mark_request_completed_without_confirmed_action(snapshot):
    async def provider(req):
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({
            "steps":[{"action":"wait","target_id":"","duration":1,"say":"Готово"}],
            "objective_complete":True})}}]})
    async with client(provider) as (app,c):
        await setup(c,snapshot)
        await finish_plan(app,c,snapshot)
        assert app.state.bridge.command.objective=="Следуй и защищай"
        assert not any(e["source"]=="mission" for e in app.state.bridge.events)
