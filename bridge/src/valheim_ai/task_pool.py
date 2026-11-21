"""Bounded, snapshot-driven NPC goals. No model, game mutation or network I/O.

Native actions are short renewable leases. Only a matching game acknowledgement
advances a task; a valid model response is never execution evidence.
"""
from collections import OrderedDict, deque
import json
import math
from pathlib import Path
import time
from typing import Literal
import uuid

from pydantic import Field, model_validator

from .protocol import Action, Decision, StrictModel, validate_decision, wait
from .item_intent import prefab_of

LIMIT = 15
TERMINAL = {"completed", "failed", "cancelled"}
MUTATING = {Action.GIVE, Action.DROP, Action.CRAFT, Action.UPGRADE, Action.DEPOSIT,
            Action.TAKE, Action.USE_ON, Action.USE, Action.EAT, Action.REPAIR}


class TaskSpec(StrictModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["follow", "collect", "give", "drop", "action", "goal"]
    title: str = Field(default="", max_length=160)
    objective: str = Field(default="", max_length=1000)
    priority: int = Field(default=50, ge=0, le=90)
    item_prefabs: list[str] = Field(default_factory=list, max_length=12)
    amount: int = Field(default=1, ge=1, le=100000)
    selection: Literal["exact", "all_matching", "all_inventory"] = "exact"
    continuous: bool = False
    depends_on: list[str] = Field(default_factory=list, max_length=15)
    near_leader: bool = False
    max_distance: float = Field(default=128, ge=.5, le=128)
    timeout_seconds: float = Field(default=180, ge=5, le=3600)
    action: Decision | None = None

    @model_validator(mode="after")
    def coherent(self):
        if any(not p or len(p) > 160 for p in self.item_prefabs) or len(set(self.item_prefabs)) != len(self.item_prefabs):
            raise ValueError("Invalid or duplicate item prefab")
        if len(set(self.depends_on)) != len(self.depends_on) or self.id in self.depends_on:
            raise ValueError("Duplicate or self task dependency")
        if self.kind == "follow": self.continuous = True
        if self.continuous and self.kind not in ("follow", "collect", "goal"):
            raise ValueError("This task cannot be continuous")
        if self.kind == "collect" and (not self.item_prefabs or self.selection == "all_inventory"):
            raise ValueError("Collection needs explicit item types")
        if self.kind in ("give", "drop"):
            if self.selection == "all_inventory":
                if self.item_prefabs or self.amount != 1: raise ValueError("Whole bag has no item selector or quantity")
            elif not self.item_prefabs: raise ValueError("Transfer needs explicit item types")
        if self.selection == "all_matching" and self.amount != 1:
            raise ValueError("All matching uses amount=1")
        if self.kind == "action":
            if self.action is None: raise ValueError("Atomic task needs an action")
            if self.action.action in (Action.GIVE, Action.DROP, Action.PICKUP, Action.FOLLOW):
                raise ValueError("Follow, collection and transfers require their typed task kinds")
        elif self.action is not None: raise ValueError("Only an atomic task stores an action")
        if self.kind == "goal" and not self.objective.strip(): raise ValueError("Goal needs an objective")
        return self


class TaskBatch(StrictModel):
    mode: Literal["append", "replace"] = "append"
    combat_policy: Literal["defend", "avoid"] = "defend"
    tasks: list[TaskSpec] = Field(min_length=1, max_length=LIMIT)


# Alias for integrations that call a model's response a proposal.
TaskProposal = TaskBatch


class TaskNode(StrictModel):
    spec: TaskSpec
    # Set by the trusted request boundary, never by the model's TaskSpec.
    source_request: str = Field(default="", max_length=1000)
    status: Literal["queued", "running", "paused", "blocked", "completed", "failed", "cancelled"] = "queued"
    reason: str = ""
    count: int = 0
    elapsed: float = 0
    attempts: int = 0
    confirmed_steps: int = 0
    completed_amount: int = 0
    manual_paused: bool = False
    needs_refresh: bool = False
    retry_at: float = 0
    last_outcome: str = ""

    @property
    def id(self): return self.spec.id
    @property
    def kind(self): return self.spec.kind
    @property
    def priority(self): return self.spec.priority
    @property
    def title(self): return self.spec.title or self.spec.objective or self.spec.kind
    @property
    def objective(self): return self.spec.objective or self.title


class TaskPool:
    def __init__(self):
        self.nodes: OrderedDict[str, TaskNode] = OrderedDict()
        self.history: deque[TaskNode] = deque(maxlen=30)
        self.revision = 0
        self.paused = False
        self.combat_policy = "defend"
        self.active_id = ""
        self._lease_id = ""
        self._lease_task = ""
        self._lease: Decision | None = None
        self._last_tick: float | None = None
        self._was_suspended = False
        self._catchup = False
        self._excluded: dict[str, float] = {}
        self._last_snapshot = None
        self._receipts: dict[str, str] = {}

    @property
    def tasks(self): return list(self.nodes.values())
    @property
    def plan_id(self): return self._lease_id
    @property
    def lease(self): return self._lease
    @property
    def lease_task_id(self): return self._lease_task

    def get(self, task_id): return self.nodes.get(task_id)

    def _clear_lease(self):
        self._lease_id = self._lease_task = ""
        self._lease = None

    def _record_terminal(self, node, status, reason):
        node.status, node.reason = status, reason[:240]
        self.nodes.pop(node.id, None)
        self.history.append(node)
        self._receipts[node.id] = status
        if self.active_id == node.id: self.active_id = ""
        if self._lease_task == node.id: self._clear_lease()
        keep = {n.id for n in self.history} | {d for n in self.nodes.values() for d in n.spec.depends_on}
        self._receipts = {k: v for k, v in self._receipts.items() if k in keep}

    def _cascade(self):
        changed = True
        while changed:
            changed = False
            failed = {task_id for task_id, status in self._receipts.items() if status in ("failed", "cancelled")}
            for node in list(self.nodes.values()):
                if failed.intersection(node.spec.depends_on):
                    self._record_terminal(node, "failed", "Предшествующая задача отменена или не выполнена")
                    changed = True

    def apply_batch(self, specs, mode="append", snapshot=None, *, source_request=""):
        if not isinstance(source_request, str) or len(source_request) > 1000:
            raise ValueError("Invalid original source request")
        policy = self.combat_policy
        if isinstance(specs, TaskBatch): policy, mode, specs = specs.combat_policy, specs.mode, specs.tasks
        if mode not in ("append", "replace"): raise ValueError("Unknown task batch mode")
        values = [TaskSpec.model_validate(s.model_dump() if isinstance(s, TaskSpec) else s) for s in specs]
        if not values or len(values) > LIMIT: raise ValueError("Task batch must contain 1..15 tasks")
        if len({s.id for s in values}) != len(values): raise ValueError("Duplicate task ids")
        existing = {} if mode == "replace" else self.nodes
        completed = {task_id for task_id, status in self._receipts.items() if status == "completed"}
        follow = next((n for n in existing.values() if n.kind == "follow"), None)
        mapping, rewritten = {}, []
        first_follow = follow.id if follow else None
        for spec in values:
            if spec.kind == "follow" and first_follow:
                mapping[spec.id] = first_follow
            else:
                mapping[spec.id] = uuid.uuid4().hex
                if spec.kind == "follow": first_follow = mapping[spec.id]
        # An ongoing collection rule is a standing order, like follow. Repeated
        # phrasing updates that order rather than consuming another pool slot.
        def collection_key(spec, remap=False):
            return (frozenset(spec.item_prefabs), spec.selection, spec.near_leader,
                    spec.max_distance, frozenset(mapping.get(d, d) if remap else d for d in spec.depends_on))
        collections = {collection_key(n.spec): n.id for n in existing.values()
                       if n.kind == "collect" and n.spec.continuous}
        for spec in values:
            if spec.kind == "collect" and spec.continuous:
                key = collection_key(spec, remap=True)
                if key in collections: mapping[spec.id] = collections[key]
                else: collections[key] = mapping[spec.id]
        known = set(existing) | completed | set(mapping)
        for spec in values:
            if any(d not in known for d in spec.depends_on): raise ValueError("Unknown task dependency")
            rewritten.append(spec.model_copy(update={"id": mapping[spec.id], "depends_on": [mapping.get(d, d) for d in spec.depends_on]}))
        final_specs = {n.id: n.spec for n in existing.values()}
        for spec in rewritten: final_specs[spec.id] = spec
        if len(final_specs) > LIMIT: raise ValueError("Пул заполнен: максимум 15 активных задач")
        self._validate_graph(final_specs, completed)
        # Everything above is side-effect free. A rejected batch never evicts.
        if mode == "replace":
            for node in list(self.nodes.values()): self._record_terminal(node, "cancelled", "Заменено новым поручением")
            self._catchup = False
        accepted = []
        for spec in rewritten:
            if spec.id in self.nodes:
                node = self.nodes[spec.id]
                node.spec = spec
                if source_request: node.source_request = source_request
                if not node.manual_paused: node.status, node.reason = "queued", ""
            else:
                node = TaskNode(spec=spec, source_request=source_request)
                self.nodes[spec.id] = node
            if node not in accepted: accepted.append(node)
        self.paused = False
        self.combat_policy = policy
        self.revision += 1
        if snapshot is not None: self._update_counts(snapshot)
        return accepted

    @staticmethod
    def _validate_graph(specs, completed):
        visiting, visited = set(), set()
        def visit(task_id):
            if task_id in completed or task_id in visited: return
            if task_id in visiting: raise ValueError("Task dependencies contain a cycle")
            if task_id not in specs: raise ValueError("Unknown task dependency")
            visiting.add(task_id)
            for dep in specs[task_id].depends_on:
                if dep in specs and specs[dep].continuous:
                    raise ValueError("A dependency on a continuous task can never complete")
                visit(dep)
            visiting.remove(task_id); visited.add(task_id)
        for task_id in specs: visit(task_id)

    def _clock(self, snapshot, now):
        now = time.monotonic() if now is None else now
        dt = max(0, now - self._last_tick) if self._last_tick is not None else 0
        suspended = snapshot.simulation_paused or getattr(snapshot, "reflex_active", False) or self.paused or snapshot.actor.health <= 0
        if not suspended and not self._was_suspended:
            node = self.nodes.get(self.active_id)
            if node and node.status == "running": node.elapsed += dt
        self._last_tick = now
        self._was_suspended = suspended
        self._last_snapshot = snapshot
        return now

    def _update_counts(self, snapshot):
        for node in list(self.nodes.values()):
            if node.kind in ("collect", "give", "drop"):
                node.count = sum(i.count for i in snapshot.actor.inventory if prefab_of(i, snapshot) in node.spec.item_prefabs)
                if node.kind == "collect" and not node.spec.continuous and node.count >= node.spec.amount:
                    self._record_terminal(node, "completed", "Нужное количество подтверждено инвентарём")
        self._cascade()

    def observe(self, snapshot, now=None):
        now = self._clock(snapshot, now)
        self._excluded = {target: until for target, until in self._excluded.items() if until > now}
        if self._lease_id and snapshot.plan_id == self._lease_id and snapshot.action_status in ("success", "blocked"):
            node, decision = self.nodes.get(self._lease_task), self._lease
            self._clear_lease()  # ACK consumed exactly once even on duplicate snapshots.
            if node and decision:
                node.last_outcome = snapshot.previous_result[:240]
                if snapshot.action_status == "success":
                    transfer = decision.action in (Action.GIVE, Action.DROP)
                    if transfer: node.completed_amount += snapshot.action_completed_amount
                    if transfer and (snapshot.action_completed_amount == 0 or
                                     decision.selection == "exact" and snapshot.action_completed_amount != decision.amount):
                        self._record_terminal(node, "failed",
                            "Игра не подтвердила запрошенное количество передачи: " + str(snapshot.action_completed_amount) +
                            "; проверьте инвентарь, исходное количество не повторяется")
                    else:
                        node.confirmed_steps += 1
                        node.attempts = 0
                        if node.spec.continuous: node.elapsed = 0
                        if node.kind == "action" or node.kind == "give" and decision.action == Action.GIVE or node.kind == "drop" and decision.action == Action.DROP:
                            if not transfer: node.completed_amount += snapshot.action_completed_amount
                            self._record_terminal(node, "completed", snapshot.previous_result or "Игра подтвердила действие")
                        else: node.status, node.reason = "queued", ""
                elif decision.action in MUTATING and snapshot.action_completed_amount > 0:
                    node.completed_amount += snapshot.action_completed_amount
                    self._record_terminal(node, "failed", "Передача частично выполнена; исходное количество не повторяется. " + snapshot.previous_result)
                else:
                    node.attempts += 1
                    if decision.target_id: self._excluded[decision.target_id] = now + 20
                    if node.attempts >= 3:
                        self._record_terminal(node, "failed", "Три отказа исполнения: " + snapshot.previous_result)
                    else:
                        node.status, node.reason = "blocked", snapshot.previous_result or "Игра отклонила действие"
                        node.retry_at = now + 2 ** node.attempts
        self._update_counts(snapshot)
        for node in list(self.nodes.values()):
            if node.kind == "action" and node.spec.action.action == Action.WAIT and node.elapsed >= node.spec.action.duration:
                self._record_terminal(node, "completed", "Пауза завершена по игровому времени")
                continue
            if node.kind != "follow" and node.elapsed >= node.spec.timeout_seconds:
                self._record_terminal(node, "failed", "Исчерпано время активного выполнения задачи")
        self._cascade()
        return now

    @staticmethod
    def _distance(a, b):
        return math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2)

    def _leader(self, snapshot):
        return next((e for e in snapshot.entities if e.id == snapshot.leader_id and e.kind == "player"), None)

    def _sources(self, node, snapshot, now):
        leader = self._leader(snapshot)
        out = []
        for entity in snapshot.entities:
            if self._excluded.get(entity.id, 0) > now: continue
            # CanPickup may be false until the NPC approaches and requests
            # network ownership; native execution decides whether pickup works.
            matching = entity.kind == "item" and "pickup" in snapshot.supported_actions and prefab_of(entity, snapshot) in node.spec.item_prefabs
            matching |= entity.kind == "interactable" and "interact" in snapshot.supported_actions and entity.category == "pickable" and bool(set(entity.produces).intersection(node.spec.item_prefabs))
            if not matching: continue
            distance = self._distance(entity.position, snapshot.actor.position)
            radius = min(node.spec.max_distance, 8) if node.spec.near_leader else node.spec.max_distance
            if entity.kind == "interactable": radius = min(radius, 20)
            if distance > radius: continue
            if node.spec.near_leader and (not leader or self._distance(entity.position, leader.position) > 10 or self._catchup): continue
            out.append(entity)
        return sorted(out, key=lambda e: (self._distance(e.position, snapshot.actor.position), e.id))

    def _bag(self, node, snapshot):
        return [i for i in snapshot.actor.inventory if not i.quest and
                (node.spec.selection == "all_inventory" or prefab_of(i, snapshot) in node.spec.item_prefabs)]

    def _transfer_item(self, node, snapshot):
        items = self._bag(node, snapshot)
        if node.spec.selection == "all_inventory": return items[0] if items else None
        groups = {}
        for item in items:
            group = item.item_key or item.id
            groups[group] = groups.get(group, 0) + item.count
        return next((i for i in items if node.spec.selection != "exact" or groups[i.item_key or i.id] >= node.spec.amount), None)

    def _ready(self, node, snapshot, now):
        if node.manual_paused: return False, "Приостановлено игроком"
        if node.needs_refresh: return False, "После загрузки нужна проверка цели или неподтверждённой операции; дайте новое поручение"
        done = {task_id for task_id, status in self._receipts.items() if status == "completed"}
        if any(d not in done for d in node.spec.depends_on): return False, "Ожидаю завершения предыдущей задачи"
        if node.retry_at > now: return False, "Пауза после отказа игры"
        leader = self._leader(snapshot)
        if node.kind == "follow": return bool(leader) and "follow" in snapshot.supported_actions, "Лидер не виден или следование не поддерживается"
        if node.spec.near_leader and (not leader or self._catchup): return False, "Сбор приостановлен: нужно вернуться к лидеру"
        if node.kind == "collect":
            if snapshot.actor.encumbered: return False, "Сумка перегружена"
            return bool(self._sources(node, snapshot, now)), "Подходящих вещей или урожая в разрешённом радиусе не видно"
        if node.kind in ("give", "drop"):
            if node.kind not in snapshot.supported_actions: return False, "Передача или сброс не поддерживается установленным модом"
            if node.kind == "give" and (not leader or leader.distance > 60): return False, "Получатель не виден или слишком далеко"
            stock = self._transfer_item(node, snapshot) is not None
            replenishment = node.kind == "give" and node.spec.selection != "all_inventory" and not snapshot.actor.encumbered and bool(self._sources(node, snapshot, now))
            return stock or replenishment, "В сумке недостаточно подходящих неквестовых предметов одного варианта; пополнение не видно"
        if node.kind == "action":
            try: validate_decision(node.spec.action, snapshot)
            except ValueError: return False, "Цель или условия атомарного действия недоступны"
        return True, ""

    def select(self, snapshot, now=None):
        now = self.observe(snapshot, now)
        if self.paused or snapshot.simulation_paused or getattr(snapshot, "reflex_active", False) or snapshot.actor.health <= 0:
            for node in self.nodes.values():
                if not node.manual_paused: node.status, node.reason = "paused", "Пул приостановлен" if self.paused else "NPC недоступен" if snapshot.actor.health <= 0 else "Игра выполняет немедленную защиту" if getattr(snapshot, "reflex_active", False) else "Игра на паузе"
            self.active_id = ""
            return None
        leader = self._leader(snapshot)
        follow_exists = any(n.kind == "follow" and not n.manual_paused for n in self.nodes.values())
        if follow_exists:
            distance = self._distance(snapshot.actor.position, leader.position) if leader else float("inf")
            if distance >= 14: self._catchup = True
            elif distance <= 6: self._catchup = False
        else: self._catchup = False
        candidates = []
        for index, node in enumerate(self.nodes.values()):
            ready, reason = self._ready(node, snapshot, now)
            if not ready:
                node.status = "paused" if node.manual_paused or node.spec.near_leader and self._catchup else "blocked"
                node.reason = reason
                continue
            priority = node.priority
            if node.kind == "follow":
                priority = max(priority, 80) if self._catchup else min(priority, 30)
            node.status, node.reason = "queued", "Ожидает более приоритетную задачу"
            candidates.append((priority, node.id == self.active_id, -index, node))
        selected = max(candidates, key=lambda c: c[:3])[3] if candidates else None
        leased = self.nodes.get(self._lease_task)
        if leased and self._lease and self._lease.action in MUTATING:
            # Do not lose a transfer's receipt then accidentally replay its full
            # quantity. Native safety reflexes still preempt controls locally.
            selected = leased
        new_id = selected.id if selected else ""
        if self._lease_task and self._lease_task != new_id: self._clear_lease()
        for node in self.nodes.values():
            if node == selected: node.status, node.reason = "running", ""
            elif node.status == "running": node.status, node.reason = "queued", "Ожидает более приоритетную задачу"
        self.active_id = new_id
        return selected

    def issue(self, task_id, decision, snapshot):
        node = self.nodes.get(task_id)
        if node is None or self.paused or node.manual_paused: raise ValueError("Task is not executable")
        if self.active_id != task_id: raise ValueError("Task is not selected by the scheduler")
        decision = validate_decision(decision, snapshot).model_copy(update={"duration": 3})
        fingerprint = lambda d: d.model_dump(exclude={"duration", "say"})
        if self._lease_task != task_id or self._lease is None or fingerprint(self._lease) != fingerprint(decision):
            self._lease_id = uuid.uuid4().hex
        self._lease_task, self._lease = task_id, decision
        node.status, node.reason = "running", ""
        self.active_id = task_id
        return decision, self._lease_id

    def next_decision(self, snapshot, now=None):
        if (snapshot.simulation_paused or getattr(snapshot, "reflex_active", False)) and not self.paused and self._lease is not None:
            self.observe(snapshot, now)
            node = self.nodes.get(self._lease_task)
            if node and self._lease is not None:
                # Settings/pause must not replace the native executor identity.
                # It may already have committed an operation before its receipt
                # was captured; resuming a new lease could repeat the quantity.
                node.status, node.reason = "paused", "Игра на паузе" if snapshot.simulation_paused else "Игра выполняет немедленную защиту"
                return self._lease.model_copy(update={"duration": 3}), self._lease_id, node
        node = self.select(snapshot, now)
        if node is None: return wait("Пул приостановлен" if self.paused else "Нет готовой задачи").model_copy(update={"duration": 3}), "", None
        if self._lease_task == node.id and self._lease is not None:
            if self._lease.action == Action.FOLLOW and self._lease.target_id != snapshot.leader_id:
                self._clear_lease()
            elif node.kind == "collect" and not any(e.id == self._lease.target_id for e in self._sources(node, snapshot, time.monotonic() if now is None else now)):
                self._clear_lease()
        if self._lease_task == node.id and self._lease is not None:
            try:
                decision = validate_decision(self._lease, snapshot).model_copy(update={"duration": 3})
                return decision, self._lease_id, node
            except ValueError:
                if self._lease.action in MUTATING:
                    self.fail(node.id, "Условия вещевого действия изменились; новое поручение предотвращает повтор исходного количества")
                    return wait("Задача прервана: изменился инвентарь").model_copy(update={"duration": 3}), "", None
                self._clear_lease()
        spec = node.spec
        if spec.kind == "goal": return None, "", node
        if spec.kind == "follow":
            decision = Decision(action=Action.FOLLOW, target_id=snapshot.leader_id, duration=3, say="Следую за лидером")
        elif spec.kind == "collect":
            sources = self._sources(node, snapshot, time.monotonic() if now is None else now)
            if not sources: return wait("Подходящая цель исчезла").model_copy(update={"duration": 3}), "", node
            target = sources[0]
            decision = Decision(action=Action.PICKUP if target.kind == "item" else Action.INTERACT,
                                target_id=target.id, duration=3, say="Собираю: " + target.name[:220])
        elif spec.kind in ("give", "drop"):
            item = self._transfer_item(node, snapshot)
            if not item:
                sources = self._sources(node, snapshot, time.monotonic() if now is None else now) if spec.kind == "give" and spec.selection != "all_inventory" else []
                if not sources: return wait("Нужные предметы изменились").model_copy(update={"duration": 3}), "", node
                target = sources[0]
                decision = Decision(action=Action.PICKUP if target.kind == "item" else Action.INTERACT,
                    target_id=target.id, duration=3, say="Пополняю запас для передачи: " + target.name[:200])
                decision, lease = self.issue(node.id, decision, snapshot)
                return decision, lease, node
            decision = Decision(action=Action.GIVE if spec.kind == "give" else Action.DROP,
                target_id=snapshot.leader_id if spec.kind == "give" else "" if spec.selection == "all_inventory" else item.id,
                item_id=("" if spec.selection == "all_inventory" else item.id) if spec.kind == "give" else "",
                selection=spec.selection, amount=spec.amount, duration=3, say="Передаю вещи" if spec.kind == "give" else "Сбрасываю вещи")
        else: decision = spec.action
        decision, lease = self.issue(node.id, decision, snapshot)
        return decision, lease, node

    def complete(self, task_id, reason="Поручение выполнено"):
        node = self.nodes.get(task_id)
        if node: self._record_terminal(node, "completed", reason)

    def fail(self, task_id, reason):
        node = self.nodes.get(task_id)
        if node: self._record_terminal(node, "failed", reason); self._cascade()

    def interrupt(self, reason="Действие прервано игроком"):
        node = self.nodes.get(self._lease_task)
        if node and self._lease is not None:
            if self._lease.action in MUTATING:
                self._record_terminal(node, "failed", reason + "; результат вещевого действия требует проверки, исходное количество не повторяется")
            else:
                node.status, node.reason = "queued", reason[:240]
        self._clear_lease()
        self.active_id = ""
        self._cascade()

    def control(self, action, task_id="", priority=None):
        if action not in ("pause", "resume", "cancel", "clear", "priority"): raise ValueError("Unknown task control")
        node = self.nodes.get(task_id) if task_id else None
        if task_id and node is None: raise ValueError("Task not found")
        if action == "priority" and (node is None or type(priority) is not int or not 0 <= priority <= 90):
            raise ValueError("Priority must be 0..90")
        self.interrupt("Управление пулом изменено")
        if node is not None and node.id not in self.nodes:
            self.revision += 1
            return self.status()
        if action == "priority":
            if node.id not in self.nodes:
                self.revision += 1
                return self.status()
            node.spec = node.spec.model_copy(update={"priority": priority})
        elif action == "clear":
            for item in list(self.nodes.values()): self._record_terminal(item, "cancelled", "Пул очищен игроком")
            self.paused = False
        elif action == "cancel":
            targets = [node] if node else list(self.nodes.values())
            for item in targets: self._record_terminal(item, "cancelled", "Отменено игроком")
            self._cascade()
        elif action == "pause":
            targets = [node] if node else list(self.nodes.values())
            for item in targets:
                if self._lease_task == item.id and self._lease is not None and self._lease.action in MUTATING:
                    self._record_terminal(item, "failed", "Вещевое действие прервано; проверьте инвентарь перед новым поручением")
                    continue
                if node: item.manual_paused = True
                item.status, item.reason = "paused", "Приостановлено игроком"
            if node is None: self.paused = True
            if node is None or self._lease_task == node.id: self._clear_lease(); self.active_id = ""
            self._cascade()
        elif action == "resume":
            if node is None: self.paused = False
            for item in [node] if node else self.nodes.values():
                item.manual_paused = False; item.retry_at = 0; item.attempts = 0
                item.status, item.reason = "queued", ""
        self.revision += 1
        return self.status()

    def unpause(self):
        """Automatic wake for a new request; keep individually paused tasks."""
        if self.paused:
            self.paused = False
            self.revision += 1
        for node in self.nodes.values():
            if not node.manual_paused and node.status == "paused":
                node.status, node.reason = "queued", ""
        return self.status()

    def unpause_preserving_manual(self):
        return self.unpause()

    def status(self):
        def row(n):
            target = ", ".join(n.spec.item_prefabs) or ("лидер" if n.kind == "follow" else "все вещи" if n.spec.selection == "all_inventory" else "")
            return {"id": n.id, "kind": n.kind, "title": n.title, "objective": n.objective,
                "priority": n.priority, "status": n.status, "reason": n.reason, "depends_on": n.spec.depends_on,
                "continuous": n.spec.continuous, "progress": str(n.count) + (" / ∞" if n.spec.continuous else " / " + str(n.spec.amount)) if n.kind == "collect" else str(n.completed_amount) if n.completed_amount else "",
                "target": target, "confirmed_steps": n.confirmed_steps}
        return {"limit": LIMIT, "revision": self.revision, "paused": self.paused, "active_id": self.active_id,
                "combat_policy": self.combat_policy,
                "tasks": [row(n) for n in self.nodes.values()], "history": [row(n) for n in reversed(self.history)]}

    def save(self, path, world_id):
        if not world_id: raise ValueError("World identity is required for task persistence")
        rows = []
        for node in self.nodes.values():
            data = node.model_dump(mode="json")
            data["retry_at"] = 0
            data["status"] = "paused"
            data["reason"] = "Восстановлено из сохранения; запуск вручную"
            if data["spec"]["action"] is not None:
                action = data["spec"]["action"]
                if action.get("target_id") or action.get("item_id") or action.get("destination"):
                    data["needs_refresh"] = True
                    action["target_id"] = action["item_id"] = ""
                    action["destination"] = None
            if self._lease_task == node.id and self._lease is not None and self._lease.action in MUTATING:
                data["needs_refresh"] = True
            rows.append(data)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        referenced = {d for node in self.nodes.values() for d in node.spec.depends_on}
        receipts = {k: v for k, v in self._receipts.items() if k in referenced}
        payload = {"version": 1, "world_id": str(world_id), "tasks": rows, "revision": self.revision,
                   "combat_policy": self.combat_policy, "receipts": receipts}
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path, world_id):
        pool = cls()
        path = Path(path)
        if not path.exists(): return pool
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("world_id") != str(world_id): raise ValueError("Saved task pool belongs to another world")
        nodes = [TaskNode.model_validate(value) for value in data.get("tasks", [])]
        if len(nodes) > LIMIT or len({n.id for n in nodes}) != len(nodes): raise ValueError("Invalid saved task pool size or ids")
        pool.nodes = OrderedDict((n.id, n) for n in nodes if n.status not in TERMINAL)
        receipts = data.get("receipts", {})
        if not isinstance(receipts, dict) or any(not isinstance(k, str) or v not in TERMINAL for k, v in receipts.items()):
            raise ValueError("Invalid task completion receipts")
        pool._receipts = receipts
        cls._validate_graph({n.id: n.spec for n in pool.nodes.values()}, {k for k, v in receipts.items() if v == "completed"})
        # Completed dependencies no longer need a receipt after explicit save;
        # unknown remaining dependencies remain blocked rather than executing.
        pool.paused = bool(pool.nodes)
        pool.revision = max(0, int(data.get("revision", 0)))
        policy = data.get("combat_policy", "defend")
        if policy not in ("defend", "avoid"): raise ValueError("Invalid combat policy")
        pool.combat_policy = policy
        for node in pool.nodes.values():
            node.status, node.reason = "paused", "Восстановлено из сохранения; запуск вручную"
            # New chat requests wake the pool automatically. Restored work must
            # remain held until an explicit task/Resume All control clears it.
            node.manual_paused = True
            node.retry_at = 0
        return pool
