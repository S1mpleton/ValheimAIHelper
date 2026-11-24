import asyncio
import json

import httpx
import pytest

from valheim_ai.protocol import Snapshot
from valheim_ai.providers import ProviderInvalidAction
from valheim_ai.task_compiler import compile_tasks
from test_task_compiler import state_for


def observed():
    return Snapshot(session_id="transfer-repair", sequence=1, task_pool_supported=True,
        supported_actions=["wait", "follow", "give", "drop", "equip", "speak"], leader_id="human",
        actor={"id":"npc", "name":"Гоша", "position":{"x":0,"y":0,"z":0},
               "health":25,"max_health":25,"stamina":50,"free_slots":20,
               "inventory":[{"id":"bag-stone", "name":"Камень", "item_prefab":"Stone", "count":8,
                             "food":False,"equipable":False,"equipped":False}]},
        entities=[{"id":"human","name":"Игрок","kind":"player","distance":5,"position":{"x":5,"y":0,"z":0}}],
        item_catalog=[{"item_prefab":"Stone","name":"Камень"}])


def local_reply(value):
    return httpx.Response(200,json={"done":True,"message":{"content":json.dumps(value)}})


@pytest.mark.parametrize("correct_second", [True, False])
async def test_transfer_mismatch_repairs_model_proposal_once_and_rechecks_independently(correct_second):
    phases=[]
    reviews=[]
    def handle(request):
        if request.url.path=="/api/ps": return httpx.Response(200,json={"models":[]})
        if request.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        body=json.loads(request.content)
        assert body["think"] is False
        definitions=body["format"]["$defs"]
        if "TransferSpec" in definitions:
            phases.append("review")
            reviews.append(body["messages"])
            # The independent interpretation sees only the human's request
            # and catalog, never the proposed amount/scope or repair feedback.
            facts=json.loads(body["messages"][1]["content"])
            assert set(facts)=={"latest_request","item_types"}
            assert facts["latest_request"]=="Отдай камень"
            return local_reply({"transfers":[{"kind":"give","item_prefabs":["Stone"],"amount":1,"selection":"exact"}]})
        facts=json.loads(body["messages"][1]["content"])
        repair="format_repair" in facts
        phases.append("repair" if repair else "plan")
        if repair:
            assert "независимому разбору" in facts["format_repair"]["validation_errors"]
        return local_reply({"tasks":[{"kind":"give","priority":37,"item_prefabs":["Stone"],
            "amount":1,"selection":"exact" if repair and correct_second else "all_matching"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        state=state_for(client)
        before=state.task_pool.status()
        if correct_second:
            batch=await compile_tasks(state,"Отдай камень",observed())
            assert batch.tasks[0].selection=="exact" and batch.tasks[0].amount==1
            assert batch.tasks[0].priority==37
        else:
            with pytest.raises(ProviderInvalidAction,match="повторно"):
                await compile_tasks(state,"Отдай камень",observed())
        assert phases==["plan","review","repair","review"]
        assert state.task_pool.status()==before and not state.inference_tasks
        assert reviews[0]==reviews[1]


async def test_transfer_review_cancellation_never_starts_a_repair_or_mutates_the_pool():
    entered=asyncio.Event()
    phases=[]
    async def handle(request):
        if request.url.path=="/api/ps": return httpx.Response(200,json={"models":[]})
        if request.url.path=="/api/show": return httpx.Response(200,json={"capabilities":["completion"]})
        body=json.loads(request.content)
        if "TransferSpec" in body["format"]["$defs"]:
            phases.append("review"); entered.set()
            await asyncio.Future()
        phases.append("plan")
        return local_reply({"tasks":[{"kind":"give","priority":61,"item_prefabs":["Stone"],"amount":1}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        state=state_for(client)
        task=asyncio.create_task(compile_tasks(state,"Отдай камень",observed()))
        await asyncio.wait_for(entered.wait(),2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert phases==["plan","review"] and not state.task_pool.tasks and not state.inference_tasks
        assert any(record.purpose=="task-transfer" and record.status=="cancelled" for record in state.dialogue.records.values())
