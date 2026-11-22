import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from valheim_ai.chat_intent import chat_request, classify_chat
from valheim_ai.dialogue import Dialogue
from valheim_ai.ollama import ModelSession
from valheim_ai.performance import Performance
from valheim_ai.protocol import Snapshot
from valheim_ai.providers import ProviderInvalidAction, ProviderUnavailable


def observed(snapshot):
    snapshot["actor"].update(id="npc:companion", name="Гоша")
    snapshot["leader_id"] = "human:leader"
    snapshot["entities"][0].update(id="human:leader", name="Гоша")
    return Snapshot.model_validate(snapshot)


def state_for(client, provider="ollama", timeout=5):
    return SimpleNamespace(provider=provider, model={"ollama":"local-chat:1b", "openai":"gpt-6-luna", "gemini":"gemini-2.5-flash"}[provider],
        ollama_url="http://127.0.0.1:11434", keys={"openai":"cloud-openai-secret", "gemini":"cloud-gemini-secret"},
        inference_tasks=set(), performance=Performance(), dialogue=Dialogue(lambda:["cloud-openai-secret", "cloud-gemini-secret"]),
        ollama_session=ModelSession(lambda *_:None), client_for=lambda _:client, decision_timeout=timeout)


def chat(text="возьми каменный топор", **fields):
    return [{"id":3, "speaker":"Гоша", "text":text, "pending":True, "explicit_address":False, **fields}]


def answer(**fields):
    return {"addressed":True, **fields}


def model_handler(provider, response, captures):
    def handler(request):
        if request.url.path == "/api/ps": return httpx.Response(200,json={"models":[]})
        if request.url.path == "/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        payload=json.loads(request.content)
        captures.append(payload)
        raw=json.dumps(response,ensure_ascii=False)
        if provider=="ollama":
            assert payload["think"] is False and payload["options"]["num_predict"]==64
            assert "authorization" not in request.headers
            return httpx.Response(200,json={"done":True,"message":{"content":raw}})
        if provider=="openai":
            assert payload["reasoning_effort"]=="low"
            assert payload["max_completion_tokens"]==512
            assert request.headers["authorization"]=="Bearer cloud-openai-secret"
            return httpx.Response(200,json={"choices":[{"message":{"content":raw}}]})
        assert payload["generationConfig"]["thinkingConfig"]["thinkingBudget"]==0
        assert request.headers["x-goog-api-key"]=="cloud-gemini-secret"
        return httpx.Response(200,json={"candidates":[{"content":{"parts":[{"thought":True,"text":"untrusted hidden reasoning"},{"text":raw}]}}]})
    return handler


def test_compact_focus_distinguishes_same_named_characters_without_old_mission(snapshot):
    lines=[dict(id=1,speaker="Гоша",text="Привет",pending=False),*chat(),
           dict(id=4,speaker="Второй игрок",text="Следующая просьба",pending=True)]
    request=chat_request(observed(snapshot),lines)
    system=request["messages"][0]["content"]
    facts=json.loads(system.split("\nДанные контекста:\n",1)[1])
    assert facts["npc"]==dict(role="npc_companion",id="npc:companion",name="Гоша")
    assert facts["focus"]["role"]=="authorized_human" and facts["focus"]["speaker"]=="Гоша"
    assert facts["human_leader_id"]=="human:leader"
    assert request["messages"][-1]=={"role":"user","content":"возьми каменный топор"}
    assert "text" not in facts["focus"]
    assert [line["id"] for line in facts["recent_human_conversation"]]==[1]
    assert set(request["format"]["properties"])=={"addressed"}
    assert "inventory" not in facts and "entities" not in facts and "current_objective" not in facts


@pytest.mark.parametrize("provider",["ollama","openai","gemini"])
@pytest.mark.parametrize("text", ["возьми каменный топор", "не нужно собирать малину больше"])
async def test_natural_request_without_name_keeps_human_text_and_records_its_own_phase(snapshot,provider,text):
    captures=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler(provider,answer(),captures))) as client:
        state=state_for(client,provider)
        intent=await classify_chat(state,observed(snapshot),chat(text))
        assert intent.addressed and intent.mode=="task" and intent.objective==text
        assert (captures[0]["messages"][-1]["content"] if provider!="gemini" else captures[0]["contents"][0]["parts"][0]["text"])==text
        assert len(captures)==1 and not state.inference_tasks and state.performance.last["purpose"]=="chat-intent"
        trace=next(iter(state.dialogue.records.values()))
        assert trace.purpose=="chat-intent" and trace.status=="ok"
        assert "cloud-openai-secret" not in trace.request and "cloud-gemini-secret" not in trace.request
        assert "untrusted hidden reasoning" not in trace.response


@pytest.mark.parametrize("provider",["ollama","openai","gemini"])
async def test_ambient_human_conversation_cannot_adopt_model_generated_work(snapshot,provider):
    captures=[]
    response={"addressed":False}
    async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler(provider,response,captures))) as client:
        state=state_for(client,provider)
        intent=await classify_chat(state,observed(snapshot),chat("Петя, ты уже вошёл на сервер?"))
        assert not intent.addressed and intent.mode=="ignore" and intent.objective==intent.reply==""


@pytest.mark.parametrize("route", ["explicit_address", "direct_dialogue"])
async def test_verified_direct_dialogue_goes_to_compiler_without_another_model_decision(snapshot, route):
    captures=[]
    response={"addressed":False}
    async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler("ollama",response,captures))) as client:
        state=state_for(client)
        intent=await classify_chat(state,observed(snapshot),chat("возьми топор",**{route:True}))
        assert intent.addressed and intent.mode=="task" and not intent.reply
        assert intent.objective=="возьми топор" and not captures and state.performance.last is None


async def test_model_cannot_inject_message_id_or_a_new_task_into_the_boolean_result(snapshot):
    captures=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler("ollama",{**answer(),"message_id":999},captures))) as client:
        state=state_for(client)
        with pytest.raises(ProviderInvalidAction):
            await classify_chat(state,observed(snapshot),chat())
        assert not state.inference_tasks and next(iter(state.dialogue.records.values())).status=="error"


@pytest.mark.parametrize("value",["false","true",0,1])
async def test_non_boolean_address_cannot_accidentally_authorize_a_chat_command(snapshot,value):
    captures=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler("ollama",{"addressed":value},captures))) as client:
        state=state_for(client)
        with pytest.raises(ProviderInvalidAction):
            await classify_chat(state,observed(snapshot),chat())
        assert not state.inference_tasks


async def test_no_pending_message_does_not_access_model(snapshot):
    def unexpected(_): raise AssertionError("No model call expected")
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        state=state_for(client)
        assert await classify_chat(state,observed(snapshot),[]) is None
        assert not state.dialogue.records and state.performance.last is None


async def test_cancellation_finishes_trace_and_releases_inference_registration(snapshot):
    entered=asyncio.Event()
    async def handler(request):
        if request.url.path=="/api/ps": return httpx.Response(200,json={"models":[]})
        if request.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        entered.set()
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state=state_for(client)
        task=asyncio.create_task(classify_chat(state,observed(snapshot),chat()))
        await asyncio.wait_for(entered.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not state.inference_tasks and state.performance.last["status"]=="cancelled"
        assert next(iter(state.dialogue.records.values())).status=="cancelled"


async def test_timeout_is_bounded_and_does_not_classify_as_ignored(snapshot):
    async def handler(_): await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        state=state_for(client,timeout=.01)
        with pytest.raises(ProviderUnavailable,match="Время разбора"):
            await classify_chat(state,observed(snapshot),chat())
        assert not state.inference_tasks and next(iter(state.dialogue.records.values())).status=="error"
