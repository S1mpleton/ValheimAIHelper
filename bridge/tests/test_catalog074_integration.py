import json

import httpx

from test_ollama import app_client, start, cycle, reply, plan


def catalog_world(snapshot):
    snapshot.update(item_catalog_version='catalog-v1', item_catalog_total=4, item_catalog=[
        {'item_prefab':'AxeStone','name':'Каменный топор','token':'$item_axe_stone','category':'OneHandedWeapon'},
        {'item_prefab':'Raspberry','name':'Малина','token':'$item_raspberry','category':'Consumable'},
        {'item_prefab':'Stone','name':'Камень','token':'$item_stone','category':'Material'},
        {'item_prefab':'Dandelion','name':'Одуванчик','token':'$item_dandelion','category':'Material'},
    ], supported_actions=['wait','follow','pickup','interact','speak','give','drop'])
    snapshot['entities'] = [snapshot['entities'][0],
        {'id':'fire','name':'Огонь','kind':'interactable','category':'fire','interaction':'interact','distance':2,'position':{'x':2,'y':0,'z':0}},
        {'id':'bush','name':'Малина','kind':'interactable','category':'pickable','produces':['Raspberry'],'interaction':'interact','distance':2,'position':{'x':1,'y':0,'z':0}},
    ]
    return snapshot


async def test_absent_axe_resolves_catalog_type_and_explains_without_planning_fire(snapshot):
    catalog_world(snapshot)
    calls=[]
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content); calls.append(body)
        assert body['think'] is False and body['format']['title']=='ItemIntent'
        return reply({'goal':'collect','item_prefabs':['AxeStone'],'amount':1,'selection':'exact'})
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/command',json={'mode':'ai','objective':'Гоша, подбери топор'})
        result=(await cycle(app,c,snapshot)).json()
        assert result['action']=='speak' and result['target_id']=='' and 'Каменный топор' in result['text']
        assert len(calls)==1 and not any(s.action.value=='interact' for s in app.state.bridge.planner.steps)
        assert any(r.purpose=='item-intent' for r in app.state.bridge.dialogue.records.values())


async def test_raspberry_request_only_offers_its_observed_harvest_not_fire(snapshot):
    catalog_world(snapshot)
    calls=[]
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content); calls.append(body)
        if body['format']['title']=='ItemIntent':
            return reply({'goal':'collect','item_prefabs':['Raspberry'],'amount':1,'selection':'exact'})
        data=json.loads(body['messages'][1]['content'].split('\n',1)[1])
        assert 'item_catalog' not in data
        targets=[e for e in data['entities'] if e['kind']=='interactable']
        assert len(targets)==1 and targets[0]['produces']==['Raspberry']
        return reply(plan('interact',targets[0]['id']))
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/command',json={'mode':'ai','objective':'Собери малины'})
        result=(await cycle(app,c,snapshot)).json()
        assert result['action']=='interact' and result['target_id']=='bush'
        assert len(calls)==2


async def test_wrong_raw_model_target_cannot_bypass_item_scope(snapshot):
    catalog_world(snapshot)
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content)
        if body['format']['title']=='ItemIntent':
            return reply({'goal':'collect','item_prefabs':['Raspberry'],'amount':1,'selection':'exact'})
        return reply(plan('interact','fire'))
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/command',json={'mode':'ai','objective':'Собери малину'})
        result=(await cycle(app,c,snapshot)).json()
        assert result['action']=='wait' and not app.state.bridge.planner.steps
        assert app.state.bridge.provider_error


async def test_give_schema_binds_stone_and_quantity_before_model_selects_stack(snapshot):
    catalog_world(snapshot)
    snapshot['actor']['inventory']=[
        {'id':'flower','name':'Одуванчик','item_prefab':'Dandelion','item_key':'Dandelion:1:0','count':20,'food':False,'equipped':False,'equipable':False},
        {'id':'stone','name':'Камень','item_prefab':'Stone','item_key':'Stone:1:0','count':7,'food':False,'equipped':False,'equipable':False},
    ]
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content)
        if body['format']['title']=='ItemIntent':
            return reply({'goal':'give','item_prefabs':['Stone'],'amount':5,'selection':'exact'})
        variants=body['format']['$defs']['Decision']['anyOf']
        give=next(v['properties'] for v in variants if v['properties']['action']['const']=='give')
        assert give['amount']['const']==5 and give['selection']['const']=='exact'
        assert len(give['item_id']['enum'])==1 and 'Камень' in give['item_id']['enum'][0]
        return reply(plan('give',give['target_id']['enum'][0],item_id=give['item_id']['enum'][0],amount=5))
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/command',json={'mode':'ai','objective':'Дай мне пять камней'})
        result=(await cycle(app,c,snapshot)).json()
        assert result['action']=='give' and result['item_id']=='stone' and result['amount']==5


async def test_full_catalog_larger_than_old_limit_is_acknowledged_cached_and_requested_after_loss(snapshot):
    catalog_world(snapshot)
    snapshot['item_catalog']=[{'item_prefab':f'ModItem{i}','name':'Предмет '+('длинное имя '*6)+str(i),'token':f'$item_mod_{i}','category':'Material'} for i in range(1500)]
    snapshot['item_catalog_total']=1500
    def handler(req): raise AssertionError('Catalog exchange must not call a model')
    async with app_client(handler) as (app,c):
        first=await c.post('/api/decision',json=snapshot)
        assert len(first.request.content)>131072 and first.status_code==200
        assert first.json()['item_catalog_version']=='catalog-v1'
        snapshot.update(sequence=snapshot['sequence']+1,item_catalog=[])
        again=await c.post('/api/decision',json=snapshot)
        assert again.json()['item_catalog_version']=='catalog-v1'
        assert len(app.state.bridge.last_snapshot.item_catalog)==1500
        app.state.bridge.item_catalog=[]; app.state.bridge.item_catalog_version=''
        snapshot['sequence']+=1
        lost=await c.post('/api/decision',json=snapshot)
        assert lost.status_code==200 and lost.json()['item_catalog_version']==''


async def test_item_intent_is_cached_while_stock_is_rechecked_from_new_snapshot(snapshot):
    catalog_world(snapshot)
    snapshot['entities'].append({'id':'berries','name':'Малина','kind':'item','item_prefab':'Raspberry','count':2,'distance':1,'position':{'x':1,'y':0,'z':0}})
    intents=[]
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content)
        if body['format']['title']=='ItemIntent':
            intents.append(body)
            return reply({'goal':'collect','item_prefabs':['Raspberry'],'amount':1,'selection':'exact'})
        return reply(plan('pickup','berries'))
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/command',json={'mode':'ai','objective':'Собирай малину'})
        first=(await cycle(app,c,snapshot)).json()
        assert first['action']=='pickup'
        snapshot['entities']=[e for e in snapshot['entities'] if e['id'] not in ('berries','bush')]
        snapshot.update(sequence=snapshot['sequence']+1,plan_id=first['plan_id'],action_status='success',previous_result='Подобрано: Малина ×2')
        app.state.bridge.next_ai_request=0
        await c.post('/api/decision',json=snapshot)
        if app.state.bridge.planner.task: await app.state.bridge.planner.task
        snapshot['sequence']+=1
        second=(await c.post('/api/decision',json=snapshot)).json()
        assert second['action']=='speak' and len(intents)==1


async def test_whole_bag_misread_is_reviewed_and_retried_without_bulk_branch(snapshot):
    catalog_world(snapshot)
    snapshot['actor']['inventory']=[{'id':'stone','name':'Камень','item_prefab':'Stone','item_key':'Stone:1:0','count':7,'food':False,'equipped':False,'equipable':False}]
    calls=[]
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content); calls.append(body)
        if body['format'].get('title')=='ItemIntent':
            if len(calls)==1: return reply({'goal':'give','item_prefabs':[],'amount':1,'selection':'all_inventory'})
            assert all(v['properties']['selection'].get('const')!='all_inventory' for v in body['format']['anyOf'])
            return reply({'goal':'give','item_prefabs':['Stone'],'amount':5,'selection':'exact'})
        if 'confirmed' in body['format'].get('properties',{}): return reply({'confirmed':False})
        data=json.loads(body['messages'][1]['content'].split('\n',1)[1])
        stone=next(i for i in data['actor']['inventory'] if i['name']=='Камень')
        return reply(plan('give',data['leader_id'],item_id=stone['id'],amount=5))
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/command',json={'mode':'ai','objective':'Дай мне 5 камней'})
        result=(await cycle(app,c,snapshot)).json()
        assert len(calls)==4 and result['action']=='give' and result['item_id']=='stone'
        assert result['selection']=='exact' and result['amount']==5
        assert any(r.purpose=='item-scope' for r in app.state.bridge.dialogue.records.values())


async def test_nonexplicit_chat_classifies_before_new_item_plan(snapshot):
    catalog_world(snapshot)
    calls=[]
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content); calls.append(body)
        if body['format'].get('title')=='ItemIntent':
            return reply({'goal':'collect','item_prefabs':['AxeStone'],'amount':1,'selection':'exact'})
        variants=body['format']['$defs']['Decision']['anyOf']
        assert [v['properties']['action']['const'] for v in variants]==['wait']
        return reply({**plan(), 'chat_intent':{'message_id':1,'addressed':True,'mode':'task','objective':'Подбери топор','reply':''}})
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/chat',json={'actor_name':'NPC','speaker':'Лидер','text':'Подбери топор'})
        await cycle(app,c,snapshot)
        assert app.state.bridge.command.objective=='Подбери топор'
        await cycle(app,c,snapshot)
        snapshot['sequence']+=1
        result=(await c.post('/api/decision',json=snapshot)).json()
        assert result['action']=='speak' and len(calls)==2


async def test_reply_intent_cannot_be_overridden_by_model_stop_label(snapshot):
    catalog_world(snapshot)
    def handler(req):
        if req.url.path=='/api/show': return httpx.Response(200,json={})
        body=json.loads(req.content)
        if body['format'].get('title')=='ItemIntent':
            return reply({'goal':'reply','item_prefabs':[],'amount':1,'selection':'exact'})
        return reply({**plan(), 'chat_intent':{'message_id':1,'addressed':True,'mode':'stop','objective':'','reply':'У меня две малины.'}})
    async with app_client(handler) as (app,c):
        await start(c,snapshot)
        await c.post('/api/chat',json={'actor_name':'NPC','speaker':'Лидер','text':'NPC, сколько малины?','explicit_address':True})
        result=(await cycle(app,c,snapshot)).json()
        assert result['mode']=='ai' and result['action']=='speak' and result['text']=='У меня две малины.'
