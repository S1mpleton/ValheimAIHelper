import json

import pytest

from valheim_ai.protocol import Decision, Snapshot
from valheim_ai.task_pool import LIMIT, TaskBatch, TaskEdit, TaskNode, TaskPool, TaskSpec


def test_ai_priority_change_cannot_use_an_implicit_default():
    with pytest.raises(ValueError, match="explicitly"):
        TaskEdit(action="priority", task_id="existing_job")
    assert TaskEdit(action="priority", task_id="existing_job", priority=13).priority == 13
    assert TaskEdit(action="cancel", task_id="existing_job").action == "cancel"


def world(snapshot, *, leader=3, drops=(("berries", 2, 0),), stock=0):
    snapshot['supported_actions']=['wait','follow','pickup','interact','give','drop','speak','move','eat']
    snapshot['actor']['inventory']=[] if not stock else [dict(id='bag:berries',name='Малина',count=stock,item_prefab='Raspberry',item_key='Raspberry:1:0',food=True,equipable=False,equipped=False)]
    snapshot['entities']=[] if leader is None else [dict(id='leader',name='Человек',kind='player',distance=abs(leader),position=dict(x=leader,y=0,z=0))]
    snapshot['leader_id']='leader' if leader is not None else ''
    for target,x,z in drops:
        snapshot['entities'].append(dict(id=target,name='Малина',kind='item',item_prefab='Raspberry',count=3,distance=(x*x+z*z)**.5,position=dict(x=x,y=0,z=z)))
    return Snapshot.model_validate(snapshot)


def follow(**kwargs): return TaskSpec(id='follow',kind='follow',title='Следовать',priority=30,**kwargs)
def collect(**kwargs): return TaskSpec(id='collect',kind='collect',title='Собирать малину',priority=60,item_prefabs=['Raspberry'],**kwargs)
def move(**kwargs): return TaskSpec(kind='action',action=Decision(action='move',target_id='',duration=1,say='',destination=dict(x=2,y=0,z=0)),**kwargs)


def test_full_pool_rejects_new_batch_atomically_without_evicting_existing_tasks():
    pool=TaskPool()
    pool.apply_batch([follow(), *[collect(id=f'c{i}') for i in []]])
    # Fill with independent goals; there is no hidden FIFO replacement.
    pool.apply_batch([TaskSpec(kind='goal',objective=f'Задача {i}') for i in range(14)])
    before=pool.status()
    with pytest.raises(ValueError,match='15'):
        pool.apply_batch([TaskSpec(kind='goal',objective='Шестнадцатая')])
    assert pool.status()==before and len(pool.tasks)==LIMIT
    accepted=pool.apply_batch([follow()])
    assert len(pool.tasks)==LIMIT and accepted[0].id==pool.tasks[0].id


def test_duplicate_continuous_collection_updates_one_order_without_consuming_slots():
    pool=TaskPool()
    first,=pool.apply_batch([
        TaskSpec(id='a',kind='collect',item_prefabs=['Raspberry','Stone'],continuous=True,priority=40),
        TaskSpec(id='b',kind='collect',item_prefabs=['Stone','Raspberry'],continuous=True,priority=60),
    ])
    assert len(pool.tasks)==1 and first.priority==60
    pool.control('pause',first.id)
    pool.apply_batch([TaskSpec(kind='goal',objective=str(i)) for i in range(14)])
    accepted=pool.apply_batch([TaskSpec(kind='collect',item_prefabs=['Raspberry','Stone'],continuous=True,priority=75)])
    assert len(pool.tasks)==LIMIT and accepted==[first] and first.priority==75 and first.manual_paused


def test_collection_dedup_preserves_dependency_identity_and_rejects_continuous_dependency_atomically():
    pool=TaskPool()
    prerequisite,ongoing=pool.apply_batch([
        TaskSpec(id='before',kind='goal',objective='Сначала подготовиться'),
        TaskSpec(id='berries-a',kind='collect',item_prefabs=['Raspberry'],continuous=True,depends_on=['before']),
        TaskSpec(id='berries-b',kind='collect',item_prefabs=['Raspberry'],continuous=True,depends_on=['before']),
    ])
    assert len(pool.tasks)==2 and ongoing.spec.depends_on==[prerequisite.id]
    same,=pool.apply_batch([TaskSpec(kind='collect',item_prefabs=['Raspberry'],continuous=True,depends_on=[prerequisite.id])])
    assert same.id==ongoing.id
    before=pool.status()
    with pytest.raises(ValueError,match='continuous'):
        pool.apply_batch([
            TaskSpec(id='alias',kind='collect',item_prefabs=['Raspberry'],continuous=True,depends_on=[prerequisite.id]),
            TaskSpec(kind='goal',objective='Дождаться бесконечного сбора',depends_on=['alias']),
        ])
    assert pool.status()==before


@pytest.mark.parametrize('changes,expected',[
    ({'continuous':False},2), ({'near_leader':True},1), ({'max_distance':8},1),
    ({'selection':'all_matching'},1), ({'item_prefabs':['Stone']},2),
])
def test_one_standing_order_updates_its_conditions_but_distinct_items_and_finite_phases_remain_separate(changes,expected):
    pool=TaskPool()
    pool.apply_batch([TaskSpec(kind='collect',item_prefabs=['Raspberry'],continuous=True)])
    spec={'kind':'collect','item_prefabs':['Raspberry'],'continuous':True,**changes}
    pool.apply_batch([TaskSpec.model_validate(spec)])
    assert len(pool.tasks)==expected


def test_restated_finite_acquisition_updates_stock_goal_but_later_phases_and_transfers_are_distinct():
    pool=TaskPool()
    first=pool.apply_batch([collect(amount=5)])[0]
    first.manual_paused=True
    same=pool.apply_batch([collect(amount=8,near_leader=True)])[0]
    assert same is first and same.spec.amount==8 and same.manual_paused
    gives=pool.apply_batch([TaskSpec(id='give1',kind='give',item_prefabs=['Raspberry'],amount=5,depends_on=[first.id]),
        TaskSpec(id='give2',kind='give',item_prefabs=['Raspberry'],amount=5,depends_on=[first.id])])
    assert len(gives)==2 and gives[0].id!=gives[1].id
    later=pool.apply_batch([TaskSpec(kind='collect',item_prefabs=['Raspberry'],amount=5,depends_on=[gives[0].id])])[0]
    assert later.id!=first.id and len(pool.tasks)==4


def test_forward_dependency_aliases_identify_the_same_acquisition_phase():
    pool=TaskPool()
    nodes=pool.apply_batch([
        TaskSpec(id='first',kind='collect',item_prefabs=['Raspberry'],amount=5,depends_on=['prepare1']),
        TaskSpec(id='second',kind='collect',item_prefabs=['Raspberry'],amount=7,depends_on=['prepare2']),
        TaskSpec(id='prepare1',kind='collect',item_prefabs=['Stone'],amount=1),
        TaskSpec(id='prepare2',kind='collect',item_prefabs=['Stone'],amount=1),
    ])
    assert len(nodes)==2 and len(pool.tasks)==2
    berries=next(node for node in pool.tasks if node.spec.item_prefabs==['Raspberry'])
    stone=next(node for node in pool.tasks if node.spec.item_prefabs==['Stone'])
    assert berries.spec.depends_on==[stone.id] and berries.spec.amount==7


def test_typed_titles_report_actual_action_and_local_item_identity(snapshot):
    state=world(snapshot)
    pool=TaskPool()
    tracking,berries=pool.apply_batch([TaskSpec(kind='follow',title='Малина по пути'),
        TaskSpec(kind='collect',title='Следовать',item_prefabs=['Raspberry'],continuous=True)],snapshot=state)
    assert tracking.title=='Следовать за лидером'
    assert berries.title=='Собирать: Малина'
    rows=pool.status()['tasks']
    assert rows[0]['title']!=rows[1]['title'] and 'Малина' not in rows[0]['title']


def test_loading_080_duplicates_keeps_one_identity_and_rewrites_dependent_ids(tmp_path):
    path=tmp_path/'old-pool.json'
    specs=[TaskSpec(id='follow1',kind='follow',title='Малина по пути',priority=30),
        TaskSpec(id='follow2',kind='follow',priority=30),
        TaskSpec(id='berries1',kind='collect',item_prefabs=['Raspberry'],continuous=True,near_leader=True,max_distance=8,priority=60),
        TaskSpec(id='berries2',kind='collect',item_prefabs=['Raspberry'],continuous=True,priority=30),
        TaskSpec(id='stock1',kind='collect',item_prefabs=['Stone'],amount=5),
        TaskSpec(id='stock2',kind='collect',item_prefabs=['Stone'],amount=7),
        TaskSpec(id='delivery',kind='give',item_prefabs=['Stone'],amount=5,depends_on=['stock2'])]
    nodes=[TaskNode(spec=spec,count=2 if spec.id=='berries1' else 3 if spec.id=='berries2' else 0) for spec in specs]
    path.write_text(json.dumps({'version':1,'world_id':'world','revision':9,'tasks':[n.model_dump(mode='json') for n in nodes],
        'combat_policy':'avoid','receipts':{}},ensure_ascii=False),encoding='utf-8')
    pool=TaskPool.load(path,'world')
    assert list(pool.nodes)==['follow1','berries1','stock1','delivery'] and pool.revision==10
    assert pool.migration_note and pool.paused and all(node.manual_paused for node in pool.tasks)
    assert pool.get('berries1').count==3 and pool.get('berries1').priority==60
    assert pool.get('berries1').spec.near_leader and pool.get('berries1').spec.max_distance==8
    assert pool.get('stock1').spec.amount==7 and pool.get('delivery').spec.depends_on==['stock1']
    assert pool.get('follow1').title=='Следовать за лидером' and pool.combat_policy=='avoid'
    pool.save(path,'world')
    again=TaskPool.load(path,'world')
    assert len(again.tasks)==4 and again.revision==10 and not again.migration_note


def test_model_edit_cancels_only_requested_collection_and_keeps_other_work(snapshot):
    pool=TaskPool()
    tracking,berries,stones=pool.apply_batch([follow(),collect(continuous=True),
        TaskSpec(kind='collect',item_prefabs=['Stone'],continuous=True,priority=60)])
    pool.next_decision(world(snapshot),now=0)
    accepted=pool.apply_batch(TaskBatch(edits=[TaskEdit(action='cancel',task_id=berries.id)]),source_request='Не собирай больше малину')
    assert accepted==[] and berries.id not in pool.nodes
    assert set(pool.nodes)=={tracking.id,stones.id} and pool.history[-1].status=='cancelled'
    assert pool.next_decision(world(snapshot),now=1)[0].action.value=='follow'


def test_edit_and_new_tasks_commit_atomically_after_all_validation():
    pool=TaskPool()
    original=pool.apply_batch([TaskSpec(kind='goal',objective=str(n)) for n in range(15)])
    before=pool.status()
    with pytest.raises(ValueError,match='unknown'):
        pool.apply_batch(TaskBatch(tasks=[follow()],edits=[TaskEdit(action='cancel',task_id='unknown')]))
    assert pool.status()==before
    with pytest.raises(ValueError,match='Conflicting'):
        pool.apply_batch(TaskBatch(edits=[TaskEdit(action='pause',task_id=original[0].id),TaskEdit(action='cancel',task_id=original[0].id)]))
    assert pool.status()==before
    accepted=pool.apply_batch(TaskBatch(tasks=[follow()],edits=[TaskEdit(action='cancel',task_id=original[0].id)]))
    assert len(pool.tasks)==15 and accepted[0].kind=='follow' and original[0].id not in pool.nodes


def test_cancelled_prerequisites_cascade_and_cannot_be_reintroduced_as_new_dependencies():
    pool=TaskPool()
    stock,delivery=pool.apply_batch([collect(amount=5),TaskSpec(kind='give',item_prefabs=['Raspberry'],amount=5,depends_on=['collect'])])
    before=pool.status()
    with pytest.raises(ValueError,match='Unknown task dependency'):
        pool.apply_batch(TaskBatch(tasks=[TaskSpec(kind='goal',objective='После отменённой задачи',depends_on=[stock.id])],
            edits=[TaskEdit(action='cancel',task_id=stock.id)]))
    assert pool.status()==before
    pool.apply_batch(TaskBatch(edits=[TaskEdit(action='cancel',task_id=stock.id)]))
    assert not pool.tasks and {node.status for node in pool.history}=={'cancelled','failed'}
    assert next(node for node in pool.history if node.id==delivery.id).status=='failed'


def test_edit_pause_resume_and_priority_do_not_interrupt_an_unrelated_inventory_transaction(snapshot):
    pool=TaskPool()
    giving,berries=pool.apply_batch([TaskSpec(kind='give',item_prefabs=['Raspberry'],amount=2,priority=80),collect(continuous=True)])
    state=world(snapshot,stock=3)
    decision,lease,_=pool.next_decision(state,now=0)
    assert decision.action.value=='give'
    pool.apply_batch(TaskBatch(edits=[TaskEdit(action='pause',task_id=berries.id)]))
    assert berries.manual_paused and pool.lease_task_id==giving.id and pool.plan_id==lease
    pool.apply_batch(TaskBatch(edits=[TaskEdit(action='priority',task_id=berries.id,priority=90)]))
    assert berries.priority==90 and pool.next_decision(state,now=1)[1]==lease
    pool.apply_batch(TaskBatch(edits=[TaskEdit(action='resume',task_id=berries.id)]))
    assert not berries.manual_paused and pool.next_decision(state,now=2)[1]==lease


def test_applying_a_batch_does_not_implicitly_clear_a_global_pause(snapshot):
    pool=TaskPool()
    berries=pool.apply_batch([collect(continuous=True)])[0]
    pool.control('pause')
    pool.apply_batch(TaskBatch(edits=[TaskEdit(action='priority',task_id=berries.id,priority=80)]))
    assert pool.paused and pool.next_decision(world(snapshot),now=1)[0].action.value=='wait'
    pool.apply_batch([follow()])
    assert pool.paused
    pool.unpause()
    assert not pool.paused and pool.next_decision(world(snapshot),now=2)[2].id==berries.id


def test_equal_priority_gathering_rotates_after_receipts_and_not_while_approaching(snapshot):
    pool=TaskPool()
    berries,stones=pool.apply_batch([collect(continuous=True),TaskSpec(kind='collect',item_prefabs=['Stone'],continuous=True,priority=60)])
    state=world(snapshot)
    stone=state.entities[-1].model_copy(update={'id':'stone','item_prefab':'Stone','name':'Камень',
        'position':dict(x=3,y=0,z=0),'distance':3})
    # model_copy does not validate nested types, so keep the native Position.
    stone=stone.model_copy(update={'position':state.entities[-1].position.model_copy(update={'x':3})})
    state=state.model_copy(update={'entities':[*state.entities,stone]})
    first,lease,node=pool.next_decision(state,now=0)
    assert node.id==berries.id
    for stamp in (1,2,3):
        renewed,same,node=pool.next_decision(state.model_copy(update={'plan_id':lease,'action_status':'running'}),now=stamp)
        assert node.id==berries.id and same==lease
    second,second_id,node=pool.next_decision(state.model_copy(update={'plan_id':lease,'action_status':'success'}),now=4)
    assert node.id==stones.id and second.action.value=='pickup' and second_id!=lease
    assert pool.next_decision(state.model_copy(update={'plan_id':second_id,'action_status':'running'}),now=5)[2].id==stones.id
    assert pool.next_decision(state.model_copy(update={'plan_id':second_id,'action_status':'success'}),now=6)[2].id==berries.id


def test_generated_collectible_axe_can_be_approached_and_interacted_with_beyond_old_twenty_metre_limit(snapshot):
    state=world(snapshot,drops=())
    collectible={'id':'pickable:axe','name':'Каменный топор','kind':'interactable','category':'pickable',
        'item_prefab':'AxeStone','produces':['AxeStone'],'distance':70,'interaction':'interact',
        'position':{'x':70,'y':0,'z':0}}
    state=Snapshot.model_validate({**state.model_dump(),'entities':[ *[entity.model_dump() for entity in state.entities],collectible],
        'ground_scan_radius':128})
    pool=TaskPool()
    node=pool.apply_batch([TaskSpec(kind='collect',item_prefabs=['AxeStone'])])[0]
    decision,_,selected=pool.next_decision(state,now=0)
    assert selected is node and decision.action.value=='interact' and decision.target_id=='pickable:axe'
    # A standing "along the leader's path" rule keeps its original short
    # detour restriction even though loaded collectibles are visible farther.
    pool.apply_batch([TaskSpec(kind='collect',item_prefabs=['AxeStone'],continuous=True,near_leader=True,max_distance=8)])
    node.manual_paused=True
    assert pool.next_decision(state,now=1)[0].action.value=='wait'


def test_dependency_batch_references_are_remapped_to_persistent_ids(snapshot):
    pool=TaskPool()
    first,second=pool.apply_batch([collect(amount=5), TaskSpec(id='give',kind='give',item_prefabs=['Raspberry'],amount=5,depends_on=['collect'])])
    assert second.spec.depends_on==[first.id] and first.id!='collect'
    state=world(snapshot,stock=0)
    decision,lease,node=pool.next_decision(state,now=0)
    assert node.id==first.id and decision.action.value=='pickup'
    state=state.model_copy(update={'plan_id':lease,'action_status':'success','previous_result':'Подобрано 5 ягод',
        'actor':state.actor.model_copy(update={'inventory':world(snapshot,stock=5).actor.inventory})})
    decision,_,node=pool.next_decision(state,now=1)
    assert node.id==second.id and decision.action.value=='give' and decision.amount==5
    assert pool.history[-1].id==first.id and pool.history[-1].status=='completed'


@pytest.mark.parametrize('tasks',[
    [TaskSpec(id='a',kind='goal',objective='A',depends_on=['missing'])],
    [TaskSpec(id='a',kind='goal',objective='A',depends_on=['b']),TaskSpec(id='b',kind='goal',objective='B',depends_on=['a'])],
    [follow(),TaskSpec(kind='goal',objective='Никогда не наступит',depends_on=['follow'])],
])
def test_invalid_dependency_graph_never_mutates_pool(tasks):
    pool=TaskPool(); existing=pool.apply_batch([move()])[0]; revision=pool.revision
    with pytest.raises(ValueError): pool.apply_batch(tasks)
    assert list(pool.nodes)==[existing.id] and pool.revision==revision


def test_replacement_cancels_old_pool_and_capacity_is_counted_after_replace():
    pool=TaskPool(); old=pool.apply_batch([TaskSpec(kind='goal',objective=str(i)) for i in range(15)])
    fresh=pool.apply_batch([follow(),collect(continuous=True)],mode='replace')
    assert len(pool.tasks)==2 and len(pool.history)==15
    assert all(n.status=='cancelled' for n in old) and fresh[0].id not in {n.id for n in old}


def test_model_high_follow_priority_is_honored_even_beside_the_leader(snapshot):
    pool=TaskPool(); tracking=follow().model_copy(update={'priority':90})
    nodes=pool.apply_batch([tracking,collect(continuous=True,near_leader=True,max_distance=8)])
    decision,lease,node=pool.next_decision(world(snapshot),now=0)
    assert node.id==nodes[0].id and decision.action.value=='follow'
    renewed,renewed_id,_=pool.next_decision(world(snapshot),now=1)
    assert renewed_id==lease and renewed.target_id==decision.target_id
    assert pool.revision==1  # Scheduling/progress does not invalidate UI edits.


def test_near_leader_geometry_pauses_collection_and_resumes_without_hidden_hysteresis(snapshot):
    pool=TaskPool(); nodes=pool.apply_batch([follow(),collect(continuous=True,near_leader=True,max_distance=8)])
    _,initial,node=pool.next_decision(world(snapshot),now=0)
    assert node.id==nodes[1].id
    result,_,node=pool.next_decision(world(snapshot,leader=20),now=1)
    assert result.action.value=='follow' and nodes[1].status=='paused'
    result,new_id,node=pool.next_decision(world(snapshot,leader=11),now=2)
    assert result.action.value=='pickup' and node.id==nodes[1].id and new_id!=initial


def test_model_priorities_are_never_capped_or_raised_by_task_kind_or_leader_distance(snapshot):
    pool=TaskPool()
    tracking,gathering=pool.apply_batch([
        TaskSpec(kind='follow',priority=5),
        TaskSpec(kind='collect',item_prefabs=['Raspberry'],continuous=True,priority=90),
    ])
    for stamp,distance in enumerate((3,20,60)):
        decision,_,selected=pool.next_decision(world(snapshot,leader=distance),now=stamp)
        assert selected is gathering and decision.action.value=='pickup'
        assert tracking.priority==5 and gathering.priority==90
        assert {row['id']:row['priority'] for row in pool.status()['tasks']}=={tracking.id:5,gathering.id:90}


def test_duplicate_migration_keeps_canonical_model_priority_without_selecting_a_larger_one(tmp_path):
    path=tmp_path/'old-pool.json'
    nodes=[TaskNode(spec=TaskSpec(id='first',kind='collect',item_prefabs=['Raspberry'],continuous=True,priority=5)),
           TaskNode(spec=TaskSpec(id='second',kind='collect',item_prefabs=['Raspberry'],continuous=True,priority=90))]
    path.write_text(json.dumps({'version':1,'world_id':'world','revision':1,
        'tasks':[node.model_dump(mode='json') for node in nodes],'receipts':{}}),encoding='utf-8')
    pool=TaskPool.load(path,'world')
    assert len(pool.tasks)==1 and pool.tasks[0].id=='first' and pool.tasks[0].priority==5


@pytest.mark.parametrize('leader,drops',[(8,(("berry",-7,0),)),(3,(("berry",11,0),))])
def test_along_way_collection_cannot_use_large_model_radius_to_wander(snapshot,leader,drops):
    pool=TaskPool(); pool.apply_batch([follow(),collect(continuous=True,near_leader=True,max_distance=128)])
    decision,_,_=pool.next_decision(world(snapshot,leader=leader,drops=drops),now=0)
    assert decision.action.value=='follow'


def test_standalone_collection_can_approach_visible_far_drop_waiting_for_ownership(snapshot):
    pool=TaskPool(); pool.apply_batch([collect()])
    state=world(snapshot,drops=(("far",60,0),))
    state.entities[-1].pickup_allowed=False
    decision,_,_=pool.next_decision(state,now=0)
    assert decision.action.value=='pickup' and decision.target_id=='far'


def test_continuous_collection_survives_missing_targets_and_reacts_to_new_stock(snapshot):
    pool=TaskPool(); node=pool.apply_batch([collect(continuous=True)])[0]
    _,lease,_=pool.next_decision(world(snapshot),now=0)
    ack=world(snapshot,drops=(),stock=3).model_copy(update={'plan_id':lease,'action_status':'success'})
    pool.next_decision(ack,now=1)
    assert node.id in pool.nodes and node.count==3 and node.status=='blocked'
    decision,_,resumed=pool.next_decision(world(snapshot,drops=(("next",4,0),),stock=3),now=2)
    assert resumed.id==node.id and decision.target_id=='next'


def test_target_leaving_safe_detour_does_not_keep_old_action_lease(snapshot):
    pool=TaskPool(); pool.apply_batch([follow(),collect(continuous=True,near_leader=True)])
    _,lease,_=pool.next_decision(world(snapshot,drops=(("old",2,0),("new",4,0))),now=0)
    decision,fresh,_=pool.next_decision(world(snapshot,drops=(("old",20,0),("new",4,0))),now=1)
    assert decision.target_id=='new' and fresh!=lease


def test_dependency_failure_and_cancel_cascade_without_running_child(snapshot):
    pool=TaskPool(); a,b,c=pool.apply_batch([collect(),TaskSpec(id='b',kind='goal',objective='B',depends_on=['collect']),TaskSpec(id='c',kind='goal',objective='C',depends_on=['b'])])
    pool.control('cancel',a.id)
    assert not pool.tasks
    assert {n.id:n.status for n in pool.history}=={a.id:'cancelled',b.id:'failed',c.id:'failed'}


def test_matching_native_ack_is_consumed_once_and_old_preempted_ack_is_ignored(snapshot):
    pool=TaskPool(); collector=pool.apply_batch([collect(continuous=True)])[0]
    state=world(snapshot)
    _,old,_=pool.next_decision(state,now=0)
    pool.interrupt('Новое поручение')
    _,fresh,_=pool.next_decision(state,now=1)
    pool.observe(state.model_copy(update={'plan_id':old,'action_status':'success'}),now=2)
    assert collector.confirmed_steps==0 and pool.plan_id==fresh
    ack=state.model_copy(update={'plan_id':fresh,'action_status':'success'})
    pool.observe(ack,now=3); pool.observe(ack,now=4)
    assert collector.confirmed_steps==1


def test_partial_transfer_is_terminal_and_never_reissues_original_quantity(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='give',item_prefabs=['Raspberry'],amount=5)])[0]
    state=world(snapshot,stock=7)
    decision,lease,_=pool.next_decision(state,now=0)
    assert decision.amount==5
    ack=state.model_copy(update={'plan_id':lease,'action_status':'blocked','action_completed_amount':2,'previous_result':'Часть сброшена'})
    pool.observe(ack,now=1)
    assert not pool.nodes and node.status=='failed' and node.completed_amount==2
    assert pool.next_decision(state,now=2)[0].action.value=='wait'


@pytest.mark.parametrize('kind',['give','drop'])
@pytest.mark.parametrize('actual',[0,2,6])
def test_inconsistent_success_receipt_fails_transfer_without_repeating_quantity(snapshot,kind,actual):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind=kind,item_prefabs=['Raspberry'],amount=5)])[0]
    state=world(snapshot,stock=7)
    _,lease,_=pool.next_decision(state,now=0)
    ack=state.model_copy(update={'plan_id':lease,'action_status':'success','action_completed_amount':actual})
    pool.observe(ack,now=1)
    pool.observe(ack,now=2)
    assert node.status=='failed' and node.completed_amount==actual and node.confirmed_steps==0
    assert len(pool.history)==1 and not pool.tasks
    assert pool.next_decision(state,now=3)[0].action.value=='wait'


@pytest.mark.parametrize('kind',['give','drop'])
@pytest.mark.parametrize('selection,amount,actual',[('exact',5,5),('all_matching',1,7),('all_inventory',1,7)])
def test_transfer_completes_only_after_matching_positive_native_quantity(snapshot,kind,selection,amount,actual):
    prefabs=[] if selection=='all_inventory' else ['Raspberry']
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind=kind,item_prefabs=prefabs,selection=selection,amount=amount)])[0]
    state=world(snapshot,stock=7)
    _,lease,_=pool.next_decision(state,now=0)
    ack=state.model_copy(update={'plan_id':lease,'action_status':'success','action_completed_amount':actual})
    pool.observe(ack,now=1); pool.observe(ack,now=2)
    assert node.status=='completed' and node.completed_amount==actual and node.confirmed_steps==1
    assert len(pool.history)==1


def test_goal_transfer_receipt_without_quantity_does_not_advance_goal(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='goal',objective='Дай пять ягод')])[0]
    state=world(snapshot,stock=7)
    pool.next_decision(state,now=0)
    _,lease=pool.issue(node.id,Decision(action='give',target_id='leader',item_id='bag:berries',amount=5,duration=1,say=''),state)
    pool.observe(state.model_copy(update={'plan_id':lease,'action_status':'success','action_completed_amount':0}),now=1)
    assert node.status=='failed' and node.confirmed_steps==0 and not pool.tasks


@pytest.mark.parametrize('operation',['pause','priority','cancel'])
def test_manual_interruption_of_unconfirmed_transfer_requires_new_task(snapshot,operation):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='drop',item_prefabs=['Raspberry'],amount=5)])[0]
    pool.next_decision(world(snapshot,stock=7),now=0)
    pool.control(operation,node.id,priority=70 if operation=='priority' else None)
    assert node.id not in pool.nodes and node.status=='failed' and len(pool.history)==1
    pool.control('resume')
    assert pool.next_decision(world(snapshot,stock=7),now=1)[0].action.value=='wait'


def test_transfers_wait_for_native_receipt_before_priority_preemption(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='give',priority=50,item_prefabs=['Raspberry'],amount=1)])[0]
    state=world(snapshot,stock=3); _,lease,_=pool.next_decision(state,now=0)
    pool.apply_batch([TaskSpec(kind='goal',objective='Важнее',priority=90)])
    decision,same,selected=pool.next_decision(state,now=1)
    assert selected.id==node.id and same==lease and decision.action.value=='give'


def test_goal_ack_records_progress_without_pretending_whole_goal_completed(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='goal',objective='Сложное поручение')])[0]
    state=world(snapshot); assert pool.next_decision(state,now=0)[0] is None
    _,lease=pool.issue(node.id,Decision(action='pickup',target_id='berries',duration=1,say=''),state)
    pool.observe(state.model_copy(update={'plan_id':lease,'action_status':'success'}),now=1)
    assert node.id in pool.nodes and node.confirmed_steps==1
    pool.complete(node.id,'Проверено всё поручение')
    assert node.status=='completed' and not pool.nodes


def test_pause_resume_and_game_pause_do_not_consume_execution_deadline(snapshot):
    pool=TaskPool(); node=pool.apply_batch([move(timeout_seconds=5)])[0]
    state=world(snapshot); pool.next_decision(state,now=0); pool.next_decision(state,now=3)
    for stamp in (4,40,100): pool.next_decision(state.model_copy(update={'simulation_paused':True}),now=stamp)
    pool.next_decision(state,now=200)
    assert node.id in pool.nodes and node.elapsed==3
    pool.next_decision(state,now=201)
    assert node.elapsed==4
    pool.next_decision(state,now=202)
    assert node.status=='failed'


def test_game_pause_preserves_unacknowledged_transfer_identity(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='give',item_prefabs=['Raspberry'],amount=5)])[0]
    state=world(snapshot,stock=7)
    issued,lease,_=pool.next_decision(state,now=0)
    paused=state.model_copy(update={'simulation_paused':True})
    for stamp in (1,100,1000):
        kept,same,task=pool.next_decision(paused,now=stamp)
        assert same==lease and kept.action==issued.action and kept.amount==5 and task.id==node.id
    resumed,same,_=pool.next_decision(state,now=1001)
    assert same==lease and resumed.amount==5 and node.elapsed==0


def test_continuous_follow_does_not_expire_after_three_minutes(snapshot):
    pool=TaskPool(); node=pool.apply_batch([follow()])[0]
    state=world(snapshot,drops=()); pool.next_decision(state,now=0)
    assert pool.next_decision(state,now=500)[0].action.value=='follow' and node.id in pool.nodes


def test_follow_target_change_rebinds_native_lease(snapshot):
    pool=TaskPool(); pool.apply_batch([follow()]); state=world(snapshot,drops=())
    _,lease,_=pool.next_decision(state,now=0)
    state.leader_id='next'; state.entities[0].id='next'
    decision,fresh,_=pool.next_decision(state,now=1)
    assert decision.target_id=='next' and fresh!=lease


def test_atomic_wait_uses_simulation_time_instead_of_needing_nonexistent_receipt(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='action',action=Decision(action='wait',target_id='',duration=2,say=''))])[0]
    state=world(snapshot); pool.next_decision(state,now=0)
    pool.next_decision(state,now=2)
    assert node.status=='completed'


def test_retry_is_bounded_and_never_counts_missing_prerequisite_as_an_attempt(snapshot):
    pool=TaskPool(); node=pool.apply_batch([collect(continuous=True)])[0]
    state=world(snapshot,drops=()); pool.next_decision(state,now=0)
    assert node.attempts==0 and node.status=='blocked'
    for stamp in (1,30,60):
        state=world(snapshot)
        _,lease,_=pool.next_decision(state,now=stamp)
        pool.observe(state.model_copy(update={'plan_id':lease,'action_status':'blocked','previous_result':'Нет пути'}),now=stamp+1)
    assert node.status=='failed' and len(pool.history)==1


def test_save_load_is_world_scoped_paused_and_keeps_dependency_receipt(tmp_path,snapshot):
    pool=TaskPool(); a,b=pool.apply_batch([collect(amount=3),TaskSpec(kind='give',item_prefabs=['Raspberry'],amount=3,depends_on=['collect'])])
    pool.observe(world(snapshot,stock=3),now=0)
    assert a.status=='completed'
    pool.combat_policy='avoid'
    path=tmp_path/'tasks.json'; pool.save(path,'world-a')
    loaded=TaskPool.load(path,'world-a')
    assert loaded.paused and loaded.combat_policy=='avoid' and len(loaded.tasks)==1
    assert loaded.next_decision(world(snapshot,stock=3),now=1)[0].action.value=='wait'
    loaded.control('resume')
    assert loaded.next_decision(world(snapshot,stock=3),now=2)[0].action.value=='give'
    with pytest.raises(ValueError,match='world'): TaskPool.load(path,'world-b')


def test_new_request_cannot_implicitly_resume_restored_tasks(tmp_path,snapshot):
    pool=TaskPool(); old=pool.apply_batch([TaskSpec(kind='goal',objective='Старая задача',priority=90)])[0]
    path=tmp_path/'tasks.json'; pool.save(path,'world')
    loaded=TaskPool.load(path,'world')
    restored=loaded.get(old.id)
    loaded.unpause()
    state=world(snapshot,drops=())
    assert loaded.next_decision(state,now=0)[0].action.value=='wait'
    new=loaded.apply_batch([follow()])[0]
    loaded.unpause()
    _,_,selected=loaded.next_decision(state,now=1)
    assert selected.id==new.id and restored.status=='paused' and restored.manual_paused
    loaded.control('resume',restored.id)
    _,_,selected=loaded.next_decision(state,now=2)
    assert selected.id==restored.id and not restored.manual_paused


def test_persistence_never_reuses_transient_instance_targets_or_unconfirmed_transfer(tmp_path,snapshot):
    pool=TaskPool(); atomic=pool.apply_batch([TaskSpec(kind='action',action=Decision(action='interact',target_id='world:123',duration=1,say=''))])[0]
    path=tmp_path/'task.json'; pool.save(path,'world')
    assert 'world:123' not in path.read_text(encoding='utf-8')
    loaded=TaskPool.load(path,'world'); loaded.control('resume')
    assert loaded.get(atomic.id).needs_refresh
    assert loaded.next_decision(world(snapshot),now=1)[0].action.value=='wait'
    pool=TaskPool(); transfer=pool.apply_batch([TaskSpec(kind='give',item_prefabs=['Raspberry'])])[0]
    pool.next_decision(world(snapshot,stock=3),now=0); pool.save(path,'world')
    loaded=TaskPool.load(path,'world'); loaded.control('resume')
    assert loaded.get(transfer.id).needs_refresh
    assert loaded.next_decision(world(snapshot,stock=3),now=1)[0].action.value=='wait'


def test_terminal_history_and_dependency_receipts_are_bounded(snapshot):
    pool=TaskPool()
    for _ in range(45):
        node=pool.apply_batch([move()])[0]; pool.complete(node.id)
    assert len(pool.history)==30 and len(pool._receipts)==30


def test_model_cannot_bypass_priority_ceiling_via_model_copy():
    pool=TaskPool(); bad=follow().model_copy(update={'priority':100})
    with pytest.raises(ValueError): pool.apply_batch([bad])
    assert not pool.tasks


def test_issuing_a_valid_native_action_cannot_bypass_task_priority_selection(snapshot):
    pool=TaskPool(); high,low=pool.apply_batch([TaskSpec(kind='goal',objective='Высокий',priority=80),TaskSpec(kind='goal',objective='Низкий',priority=10)])
    state=world(snapshot); pool.next_decision(state,now=0)
    with pytest.raises(ValueError,match='selected'):
        pool.issue(low.id,Decision(action='pickup',target_id='berries',duration=1,say=''),state)
    assert pool.active_id==high.id and not pool.plan_id


def test_native_reflex_freezes_task_budget_and_preserves_lease_without_selecting_another_goal(snapshot):
    pool=TaskPool(); node=pool.apply_batch([move(timeout_seconds=5)])[0]
    state=world(snapshot); _,lease,_=pool.next_decision(state,now=0)
    reflex=state.model_copy(update={'reflex_active':True})
    for stamp in (1,50,200):
        decision,same,current=pool.next_decision(reflex,now=stamp)
        assert same==lease and current.id==node.id and decision.action.value=='move'
    assert pool.next_decision(state,now=201)[1]==lease and node.elapsed==0


def test_give_replenishes_food_lost_after_collection_dependency_without_premature_completion(snapshot):
    pool=TaskPool(); a,b=pool.apply_batch([collect(amount=5),TaskSpec(kind='give',item_prefabs=['Raspberry'],amount=5,depends_on=['collect'])])
    pool.observe(world(snapshot,stock=5),now=0)
    assert a.status=='completed'
    state=world(snapshot,stock=4)
    decision,pickup_id,node=pool.next_decision(state,now=1)
    assert decision.action.value=='pickup' and node.id==b.id
    ack=world(snapshot,stock=7,drops=()).model_copy(update={'plan_id':pickup_id,'action_status':'success','previous_result':'Подобрано 3 ягоды'})
    decision,give_id,node=pool.next_decision(ack,now=2)
    assert node.id==b.id and b.confirmed_steps==1 and decision.action.value=='give' and decision.amount==5
    assert give_id!=pickup_id and b.status=='running'
    pool.observe(ack.model_copy(update={'plan_id':give_id,'action_status':'success','action_completed_amount':5}),now=3)
    assert b.status=='completed' and b.completed_amount==5


def test_drop_does_not_collect_extra_stock_to_satisfy_an_insufficient_drop_request(snapshot):
    pool=TaskPool(); node=pool.apply_batch([TaskSpec(kind='drop',item_prefabs=['Raspberry'],amount=5)])[0]
    decision,_,selected=pool.next_decision(world(snapshot,stock=2),now=0)
    assert decision.action.value=='wait' and selected is None and node.status=='blocked'


@pytest.mark.parametrize('action',['give','drop','pickup','follow'])
def test_atomic_task_cannot_bypass_typed_item_or_follow_validation(action):
    with pytest.raises(ValueError,match='typed'):
        TaskSpec(kind='action',action=Decision(action=action,target_id='target',duration=1,say=''))


def test_automatic_unpause_for_new_work_preserves_individual_pause(snapshot):
    pool=TaskPool(); first,second=pool.apply_batch([move(priority=50),move(priority=60)])
    pool.control('pause',first.id)
    pool.control('pause')
    pool.apply_batch([follow()])
    pool.unpause()
    state=world(snapshot,drops=())
    _,_,selected=pool.next_decision(state,now=0)
    assert first.manual_paused and first.status=='paused' and selected.id==second.id
    pool.control('resume')  # Explicit Resume All is different from new-work wake.
    assert not first.manual_paused


def test_original_human_request_is_trusted_separately_and_survives_save(tmp_path,snapshot):
    pool=TaskPool(); original='Гриша, дай мне пять камней'
    node=pool.apply_batch([TaskSpec(kind='goal',objective='Модель переформулировала')],source_request=original)[0]
    assert node.source_request==original
    with pytest.raises(ValueError):
        TaskSpec.model_validate({'kind':'goal','objective':'X','source_request':'Подменено моделью'})
    path=tmp_path/'pool.json'; pool.save(path,'world')
    assert TaskPool.load(path,'world').get(node.id).source_request==original


def test_capability_change_blocks_builtin_tasks_instead_of_emitting_unsupported_input(snapshot):
    pool=TaskPool(); node=pool.apply_batch([collect()])[0]
    state=world(snapshot).model_copy(update={'supported_actions':['wait']})
    decision,_,selected=pool.next_decision(state,now=0)
    assert selected is None and decision.action.value=='wait' and node.status=='blocked'
