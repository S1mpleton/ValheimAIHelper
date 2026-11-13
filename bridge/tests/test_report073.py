import copy
import json

import httpx
import pytest

from test_chat040 import client, setup, finish_plan
from test_ollama import app_client, start, cycle, reply, plan
from valheim_ai.local_ids import readable_view, native_decision
from valheim_ai.protocol import Snapshot, Decision, validate_decision
from valheim_ai.reply_policy import validate_reply
from valheim_ai.task_policy import IDLE_OBJECTIVE


async def test_confirmed_spoken_answer_does_not_restart_same_question_every_few_seconds(snapshot):
    snapshot['supported_actions'] = ['wait', 'follow', 'speak']
    calls = []
    async def provider(req):
        calls.append(req)
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({'steps':[
            {'action':'speak','target_id':'','duration':1,'say':'Отвечаю','text':'У меня две малины и одна дубина.'}
        ]})}}]})
    async with client(provider) as (app, c):
        await setup(c, snapshot)
        await c.post('/api/command', json={'mode':'ai','objective':'Сколько у тебя вещей?'})
        await finish_plan(app, c, snapshot)
        snapshot['sequence'] += 1
        issued = (await c.post('/api/decision', json=snapshot)).json()
        assert issued['action'] == 'speak'
        snapshot.update(plan_id=issued['plan_id'], action_status='success', previous_result='Сообщение передано игровому чату')
        for _ in range(20):
            app.state.bridge.next_ai_request = 0
            snapshot['sequence'] += 1
            action = (await c.post('/api/decision', json=snapshot)).json()
            assert action['action'] == 'follow'
        assert len(calls) == 1
        assert app.state.bridge.command.objective == IDLE_OBJECTIVE
        assert app.state.bridge.planner.task is None
        await c.post('/api/chat', json={'actor_name':'NPC','speaker':'Лидер','text':'NPC, скажи ещё раз','explicit_address':True})
        await finish_plan(app, c, snapshot)
        assert len(calls) == 2


async def test_local_named_handles_choose_stone_after_dandelion_and_return_native_ids(snapshot):
    snapshot['supported_actions'] = ['wait','give']
    snapshot['actor']['inventory'] = [
        {'id':'inventory:123456789','name':'Одуванчик','count':1,'equipped':False,'food':False,'equipable':False},
        {'id':'inventory:998877665','name':'Камень','count':7,'equipped':False,'food':False,'equipable':False},
    ]
    original = copy.deepcopy(snapshot)
    def handler(req):
        if req.url.path == '/api/show': return httpx.Response(200,json={})
        body = json.loads(req.content)
        assert body['think'] is False
        data = json.loads(body['messages'][1]['content'].split('\n',1)[1])
        stone = next(i for i in data['actor']['inventory'] if i['name'] == 'Камень')
        leader = next(e for e in data['entities'] if e['id'] == data['leader_id'])
        assert stone['id'].startswith('@bag/Камень/') and leader['id'].startswith('@human/')
        return reply(plan('give',leader['id'],item_id=stone['id'],amount=1))
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        result = (await cycle(app,c,snapshot)).json()
        assert result['action'] == 'give' and result['item_id'] == 'inventory:998877665'
        assert result['target_id'] == 'leader' and 'Камень' in result['say']
    assert snapshot['actor'] == original['actor']


def test_same_name_quality_and_unknown_handles_remain_distinct_and_validated(snapshot):
    snapshot['supported_actions'] = ['drop']
    snapshot['actor']['inventory'][1].update(name='Малина', quality=2)
    source = Snapshot.model_validate(snapshot)
    view, reverse = readable_view(source)
    a,b = view.actor.inventory
    assert a.id != b.id and a.name == b.name and b.quality == 2
    assert source.actor.inventory[0].id == 'inventory:0'
    result = native_decision(Decision(action='drop',target_id=b.id,duration=1,say=''),reverse)
    assert result.target_id == 'inventory:1' and validate_decision(result,source)
    with pytest.raises(ValueError):
        validate_decision(native_decision(result.model_copy(update={'target_id':'@bag/Камень/UNKNOWN'}),reverse),source)


@pytest.mark.parametrize('answer', ['Гоша, сколько у тебя вещей?', 'сколько у тебя вещей?', 'Гоша подбери камни ' * 4, 'Гоша подбери камни ' * 4 + 'Г'])
def test_literal_echoes_and_repeated_orders_are_not_chat_answers(answer):
    with pytest.raises(ValueError, match='Model'):
        validate_reply(answer,'Сколько у тебя вещей?',({'text':'Гоша, сколько у тебя вещей?','pending':True,'speaker':'Лидер'},),'Гоша')
    with pytest.raises(ValueError, match='Model'):
        validate_reply(answer,'Поручение игрока Гоша: сколько у тебя вещей?',(), 'Гоша')


@pytest.mark.parametrize('answer', ['У меня 7 камней и топор.', 'Не вижу пень рядом; подведите меня ближе.', 'Вы просите камень? У меня его нет.'])
def test_real_answers_and_explanations_are_preserved(answer):
    validate_reply(answer,'Гоша, сколько у тебя вещей?',(), 'Гоша')
