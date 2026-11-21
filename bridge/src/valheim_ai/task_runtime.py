"""Own slow decomposition and generic planning; the pool owns fast scheduling."""
import asyncio
from collections import deque
import hashlib
from pathlib import Path
import time

from .protocol import Action, Plan, Decision, validate_decision, wait
from .providers import ProviderError, ProviderInvalidAction, ProviderUnavailable, ProviderRateLimited, ProviderModelNotFound, ProviderLocalConfigurationError
from .task_policy import companion_duty
from .task_pool import TaskPool, TaskSpec
from .task_compiler import compile_tasks


class TaskRuntime:
    def __init__(self, state, storage=None):
        self.state = state
        self.pool = TaskPool()
        self.storage = Path(storage) if storage is not None else None
        self.world = None
        self.path = None
        self.task = None
        self.epoch = 0
        self.pending = None
        self.resume_after_compile = False
        self.next_retry = 0.
        self.attempts = 0
        self.notice = ""
        self.seen = {}
        self.goal_steps = {}
        self.last_saved = None
        self.save_at = 0.

    def cancel_generation(self, clear_request=True):
        self.epoch += 1
        if self.task is not None and self.task is not asyncio.current_task(): self.task.cancel()
        self.task = None
        if clear_request: self.pending = None
        self.goal_steps.clear()

    async def close(self):
        task = self.task
        self.cancel_generation()
        if task is not None: await asyncio.gather(task, return_exceptions=True)
        self.save(force=True)

    def bind(self, snapshot):
        key = (snapshot.world_id, snapshot.actor.id)
        if key == self.world: return
        self.save(force=True)
        self.cancel_generation()
        self.world, self.path = key, None
        self.pool = TaskPool()
        self.notice = ""
        self.seen.clear(); self.last_saved = None
        if self.storage is not None and snapshot.world_id:
            identity = snapshot.world_id + ":" + snapshot.actor.id
            self.path = self.storage / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
            if self.path.exists():
                try:
                    self.pool = TaskPool.load(self.path, identity)
                    self.notice = "Сохранённые задачи загружены на паузе. Нажмите «Продолжить»."
                except (ValueError, OSError):
                    self.notice = "Не удалось прочитать сохранённый пул; автоматический запуск заблокирован."
                    self.pool.control("pause")
        self.state.task_pool = self.pool

    def save(self, force=False):
        if self.path is None or self.world is None: return
        now = time.monotonic()
        if not force and now < self.save_at: return
        self.save_at = now + 2
        try:
            self.pool.save(self.path, self.world[0] + ":" + self.world[1])
        except OSError:
            self.notice = "Не удалось сохранить пул задач. Подробности доступны в журнале."
            self.state.feedback(self.notice, key="task-save", interval=60)

    def status(self):
        return {**self.pool.status(), "notice": self.notice, "compiling": self.task is not None,
                "world_id": self.world[0] if self.world else "", "actor_id": self.world[1] if self.world else ""}

    def pause(self, reason="Задачи приостановлены"):
        self.cancel_generation()
        self.pool.control("pause")
        self.notice = reason
        self.save(force=True)

    def stop(self, preserve=False):
        self.cancel_generation()
        self.pool.control("pause" if preserve else "clear")
        self.notice = "Задачи сохранены на паузе" if preserve else "Задачи отменены игроком"
        self.save(force=True)

    def command(self, mode, objective):
        self.cancel_generation()
        self.notice = ""
        if mode == "follow":
            self.pool.apply_batch([TaskSpec(kind="follow", title="Следовать за лидером", priority=30)], "append", self.state.last_snapshot, source_request=objective)
            self.pool.unpause()
            self.state.feedback("Следую за лидером.", key="tasks-follow")
        elif companion_duty(objective):
            if not self.pool.tasks:
                self.pool.apply_batch([TaskSpec(kind="follow", title="Следовать и помогать выжить", priority=30)], "append", self.state.last_snapshot)
            self.pool.unpause()
        else:
            self.request(objective)
        self.save(force=True)

    def request(self, objective, chat=()):
        resume = self.resume_after_compile if self.pending else not self.pool.paused
        self.cancel_generation()
        self.pool.control("pause")
        self.resume_after_compile = resume
        self.pending = (objective, tuple(chat))
        self.attempts = 0; self.next_retry = 0
        self.notice = "Разбираю поручение на задачи…"

    def control(self, action, task_id="", priority=None):
        if task_id and self.pool.get(task_id) is None: raise ValueError("Task not found")
        self.cancel_generation()
        # A UI control changes native command revision. Never replay an
        # inventory transaction whose receipt may still be in transit.
        self.pool.control(action, task_id, priority)
        self.notice = ""
        self.save(force=True)

    def accept_pending_chat(self, snapshot):
        if self.task is None and self.state.ready and not self.state.chat_suspended:
            self.task = asyncio.create_task(self._classify_chat(snapshot))

    async def _classify_chat(self, snapshot):
        state = self.state
        epoch, revision = self.epoch, state.revision
        pending = {m["id"] for m in state.pending_chat}
        chat = tuple({**m,"pending":m["id"] in pending} for m in state.chat_history)
        try:
            if not pending: return
            result = await asyncio.wait_for(state.decide("Определи, обращается ли разрешённый игрок к NPC. Текущая задача: " + state.command.objective + ". Не выполняй действия: steps=[wait].", snapshot,
                planning=True, purpose="chat", chat=chat), state.decision_timeout)
            if (epoch, revision) != (self.epoch, state.revision) or not state.chat_listening: return
            pending.intersection_update(m["id"] for m in state.pending_chat)
            if not pending: return
            intent = result.chat_intent
            if intent is None or intent.message_id not in pending:
                # A classification without an identified line cannot consume
                # other requests queued while the model was working.
                state.pending_chat = deque((m for m in state.pending_chat if m["id"] != min(pending)), maxlen=6)
                return
            state.pending_chat = deque((m for m in state.pending_chat if m["id"] != intent.message_id), maxlen=6)
            if not intent.addressed or intent.mode == "ignore": return
            if intent.mode == "stop":
                self.stop(); state.command = state.command.model_copy(update={"mode":"stop"}); state.revision += 1
            elif intent.mode == "follow":
                self.command("follow", ""); state.command = state.command.model_copy(update={"mode":"follow"}); state.revision += 1
            elif intent.mode == "task":
                objective = next(m["text"] for m in chat if m["id"] == intent.message_id)
                state.command = state.command.model_copy(update={"mode":"ai", "objective":objective}); state.revision += 1
                self.request(objective, chat)
            elif intent.reply:
                state.feedback(intent.reply, key="task-chat-reply-" + str(intent.message_id), interval=0)
        except asyncio.CancelledError:
            raise
        except (ProviderError, TimeoutError):
            if (epoch, revision) == (self.epoch, state.revision):
                state.pending_chat = deque((m for m in state.pending_chat if m["id"] not in pending), maxlen=6)
                state.feedback("Не удалось разобрать сообщение чата; текущие задачи сохранены.", key="task-chat-error")
        finally:
            if self.task is asyncio.current_task(): self.task = None

    async def _compile(self, snapshot):
        state = self.state
        epoch, world, revision = self.epoch, self.world, state.revision
        objective, chat = self.pending
        try:
            batch = await asyncio.wait_for(compile_tasks(state, objective, snapshot, chat), state.decision_timeout)
            if (epoch, world, revision) != (self.epoch, self.world, state.revision) or state.command.mode != "ai": return
            self.pool.apply_batch(batch.tasks, batch.mode, snapshot, source_request=objective)
            self.pool.combat_policy = batch.combat_policy
            self.pool.unpause()
            self.pending = None; self.notice = ""
            state.event("tasks", "Принят план: " + " → ".join(t.title or t.objective or t.kind for t in batch.tasks))
            state.feedback("Принял задач: " + str(len(batch.tasks)) + ". Приступаю к выполнению.", key="tasks-accepted-" + str(epoch), interval=0)
            self.save(force=True)
        except asyncio.CancelledError:
            raise
        except (ProviderError, TimeoutError, ValueError) as exc:
            if (epoch, world, revision) != (self.epoch, self.world, state.revision): return
            self.attempts += 1
            message = str(exc) if isinstance(exc, ProviderError) or type(exc) is ValueError else "Время разбора задач истекло" if isinstance(exc, TimeoutError) else "План задач не прошёл проверку"
            self.notice = message[:240]
            state.event("tasks", self.notice)
            state.feedback("Не могу принять план: " + self.notice, key="tasks-compile-error-" + str(epoch))
            if isinstance(exc, (ProviderRateLimited, ProviderModelNotFound, ProviderLocalConfigurationError)):
                self.pending = None
                state.provider_error = {"provider":state.provider,"model":state.model,"message":message,"detail":exc.diagnostic}
                state.command = state.command.model_copy(update={"mode":"stop"}); state.revision += 1
            elif self.attempts < 3 and isinstance(exc, (ProviderUnavailable, TimeoutError)):
                self.next_retry = time.monotonic() + 15
            else:
                self.pending = None
                if self.resume_after_compile: self.pool.unpause()
            self.save(force=True)
        finally:
            if self.task is asyncio.current_task(): self.task = None

    async def _plan_goal(self, node, snapshot):
        state = self.state
        epoch, world, revision = self.epoch, self.world, state.revision
        task_id = node.id
        try:
            result = await asyncio.wait_for(state.decide(node.objective, snapshot, planning=True,
                purpose="task-step", history=(node.last_outcome,) if node.last_outcome else ()), state.decision_timeout)
            if (epoch, world, revision) != (self.epoch, self.world, state.revision) or state.command.mode != "ai": return
            current = self.pool.get(task_id)
            if current is None or current.status in ("failed","cancelled","completed") or current.manual_paused: return
            assert isinstance(result, Plan)
            if any(step.action in (Action.GIVE, Action.DROP) for step in result.steps):
                raise ProviderInvalidAction("Передачу и сброс предметов нужно оформить отдельными задачами с типом и количеством. Переформулируйте поручение.")
            if result.objective_complete and current.confirmed_steps > 0 and all(step.action == Action.WAIT for step in result.steps):
                self.pool.complete(task_id, "Выполнение подтверждено результатами игры")
            else:
                self.goal_steps[task_id] = deque(result.steps)
            self.save(force=True)
        except asyncio.CancelledError:
            raise
        except (ProviderError, TimeoutError, ValueError) as exc:
            if (epoch, world, revision) != (self.epoch, self.world, state.revision): return
            message = str(exc) if isinstance(exc, ProviderError) else "Не удалось подготовить допустимый шаг задачи"
            self.pool.fail(task_id, message)
            state.feedback("Не могу выполнить «" + node.title + "»: " + message, key="task-goal-error-" + task_id)
            self.save(force=True)
        finally:
            if self.task is asyncio.current_task(): self.task = None

    def _feedback(self):
        for node in [*self.pool.tasks, *self.pool.history]:
            current = (node.status, node.reason)
            if self.seen.get(node.id) == current: continue
            self.seen[node.id] = current
            if node.status in ("completed", "failed"):
                self.state.feedback(("Выполнено: " if node.status == "completed" else "Не удалось: ") + node.title + (". " + node.reason if node.reason else ""), key="task-end-" + node.id, interval=0)
            elif node.status == "blocked":
                self.state.feedback("Ожидаю: " + node.title + ". " + node.reason, key="task-block-" + node.id + node.reason, interval=60)
        if len(self.seen) > 100: self.seen = {n.id:(n.status,n.reason) for n in [*self.pool.tasks,*self.pool.history]}

    def decision(self, snapshot):
        self.bind(snapshot)
        now = time.monotonic()
        if self.pending and self.task is None and now >= max(self.next_retry, self.state.retry_after) and self.state.ready and not snapshot.simulation_paused:
            self.task = asyncio.create_task(self._compile(snapshot))
        # Native reflexes retain priority100; pause task clocks while defending.
        decision, plan_id, node = self.pool.next_decision(snapshot)
        self._feedback()
        if decision is None and node is not None:
            steps = self.goal_steps.get(node.id)
            if steps:
                proposed = steps.popleft()
                try: decision, plan_id = self.pool.issue(node.id, proposed, snapshot)
                except ValueError:
                    self.goal_steps.pop(node.id, None)
                    self.pool.fail(node.id, "Цель шага изменилась; требуется новое поручение")
            if decision is None and self.task is None and self.pending is None and self.state.ready and now >= max(self.state.next_ai_request,self.state.retry_after) and not snapshot.simulation_paused:
                self.state.next_ai_request = now + self.state.request_interval_seconds
                self.task = asyncio.create_task(self._plan_goal(node, snapshot))
        self.save()
        return (decision or wait(self.notice or "Ожидаю решение AI для задачи")).model_copy(update={"duration":3}), "tasks", plan_id
