"""Slow model planning and fast, freshly validated control leases are independent."""
import asyncio
from collections import deque
import time
import uuid

from .protocol import Action, Decision, Plan, validate_decision, wait
from .providers import (ProviderError, ProviderUnavailable,
                        ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError)

IDLE_OBJECTIVE = "Поручение выполнено. Вернись к лидеру, помогай ему выживать, поддерживай еду, отдых и снаряжение. Ожидай новых поручений; не повторяй завершённый сброс, крафт или ответ."


class Planner:
    def __init__(self, state):
        self.state = state
        self.task: asyncio.Task | None = None
        self.steps: deque[Decision] = deque()
        self.plan_id = ""
        self.step_started = 0.0
        self.history: deque[str] = deque(maxlen=8)
        self.combat_policy = "defend"
        self.successful_steps = 0
        self.execution_epoch = 0

    def interrupt_planning(self):
        if self.task is not None: self.task.cancel()
        self.task = None

    def reset(self):
        self.execution_epoch += 1
        if self.task is not None:
            self.task.cancel()
        self.task = None
        self.steps.clear()
        self.plan_id = ""
        self.history.clear()
        self.combat_policy = "defend"
        self.successful_steps = 0

    async def close(self):
        task = self.task
        self.reset()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _plan(self, snapshot, revision, session):
        state = self.state
        pending_ids = {m["id"] for m in state.pending_chat}
        chat = tuple({**m,"pending":m["id"] in pending_ids} for m in state.chat_history)
        execution_epoch = self.execution_epoch
        try:
            result = await asyncio.wait_for(state.decide(
                state.command.objective, snapshot, planning=True, history=tuple(self.history), chat=chat,
            ), state.decision_timeout)
            if revision != state.revision or session != state.session_id or state.command.mode != "ai":
                return
            if execution_epoch != self.execution_epoch:
                # A slow answer based on pre-action inventory must not repeat
                # the action that completed while the model was thinking.
                state.event("planner", "Получен новый результат игрового действия; старый план отброшен, требуется свежий снимок")
                return
            assert isinstance(result, Plan)
            intent = result.chat_intent
            if intent is not None and intent.message_id in pending_ids and intent.addressed and intent.mode != "ignore":
                if intent.mode in ("task", "follow", "stop"):
                    mode = "ai" if intent.mode == "task" else intent.mode
                    state.command = state.command.model_copy(update={"mode": mode, "objective": intent.objective or state.command.objective})
                    state.revision += 1
                    self.successful_steps = 0
                    state.event("operator", "Из обычного чата: " + state.command.objective if mode == "ai" else "Из чата: " + mode)
                if intent.reply:
                    state.event("assistant", intent.reply)
                    state.chat_history.append({"id": 0, "speaker": "assistant", "text": intent.reply, "explicit_address": False})
            state.pending_chat = deque((m for m in state.pending_chat if m["id"] not in pending_ids), maxlen=6)
            if state.command.mode != "ai":
                self.steps.clear(); self.plan_id = ""
                return
            self.steps = deque(result.steps)
            if result.objective_complete and self.successful_steps > 0 and not (intent and intent.addressed and intent.mode == "task"):
                state.event("mission", "Поручение выполнено: " + state.command.objective)
                state.command = state.command.model_copy(update={"objective":IDLE_OBJECTIVE})
                self.successful_steps = 0
                self.steps = deque([Decision(action=Action.FOLLOW,target_id=snapshot.leader_id,duration=3,say="Возвращаюсь к лидеру")]
                    if snapshot.leader_id and "follow" in snapshot.supported_actions else [wait("Поручение выполнено; ожидаю новое")])
            self.combat_policy = result.combat_policy
            self._next_id()
            state.provider_error = None
            state.chat_suspended = False
            state.event(state.provider, "План: " + " → ".join(s.say or s.action.value for s in result.steps))
        except asyncio.CancelledError:
            raise
        except (ProviderError, TimeoutError) as exc:
            if revision != state.revision or session != state.session_id:
                return
            message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
            state.provider_error = {"provider": state.provider, "model": state.model, "message": message,
                                    "detail": exc.diagnostic if isinstance(exc, ProviderError) else ""}
            if isinstance(exc, (ProviderUnavailable, TimeoutError)):
                state.retry_after = time.monotonic() + 15
            if isinstance(exc, (ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError)):
                state.chat_suspended = True
                state.command = state.command.model_copy(update={"mode": "stop"})
                state.revision += 1
                self.steps.clear()
                self.plan_id = ""
                message = "AI остановлен. " + message
            state.event("fallback", message)
        finally:
            # A cancelled older task cannot clear its replacement's handle.
            if self.task is asyncio.current_task():
                self.task = None

    def _next_id(self):
        self.plan_id = uuid.uuid4().hex if self.steps else ""
        self.step_started = time.monotonic()

    def decision(self, snapshot):
        state = self.state
        now = time.monotonic()
        if state.command.mode != "ai":
            return wait(), "local", ""
        if self.steps:
            step = self.steps[0]
            terminal = snapshot.plan_id == self.plan_id and snapshot.action_status in ("success", "blocked")
            budget = step.duration if step.action == Action.WAIT else min(60, step.amount * 5 + 5) if step.action == Action.CRAFT else 25
            if terminal or now - self.step_started >= budget:
                outcome = snapshot.previous_result if terminal else "Шаг исчерпал время"
                self.history.append((step.action.value + ": " + outcome)[:240])
                state.event("motor", self.history[-1])
                if terminal and snapshot.action_status == "success" and step.action not in (Action.WAIT, Action.FOLLOW): self.successful_steps += 1
                if step.action not in (Action.WAIT, Action.FOLLOW): self.execution_epoch += 1
                if terminal and snapshot.action_status == "success" and step.action == Action.SPEAK:
                    state.chat_history.append({"id":0,"speaker":"assistant","text":step.text,"explicit_address":False})
                self.steps.popleft()
                # A failed prerequisite invalidates dependent steps in that plan.
                if (terminal and snapshot.action_status == "blocked") or not terminal:
                    self.steps.clear()
                self._next_id()
        can_replan = not self.steps or (len(self.steps) == 1 and self.steps[0].action in (Action.WAIT, Action.FOLLOW)) or bool(state.pending_chat)
        if (can_replan and self.task is None and not state.chat_busy and state.check_task is None and now >= max(state.next_ai_request, state.retry_after)
                and state.ready):
            state.next_ai_request = now + state.request_interval_seconds
            self.task = asyncio.create_task(self._plan(snapshot, state.revision, state.session_id))
        if self.steps:
            try:
                step = validate_decision(self.steps[0], snapshot)
                return step.model_copy(update={"duration": 3}), state.provider, self.plan_id
            except ValueError:
                self.history.append("Цель плана больше не доступна в свежем наблюдении")
                self.steps.clear()
                self._next_id()
        # Keep a useful, explicitly requested follow going while a model thinks.
        objective = state.command.objective.lower()
        if snapshot.leader_id and "follow" in snapshot.supported_actions and any(w in objective for w in ("след", "помог", "защ", "follow", "defend")):
            return Decision(action=Action.FOLLOW, target_id=snapshot.leader_id, duration=3,
                            say="Рядом с лидером; ожидаю план AI"), "motor", "follow-pending"
        return wait("Ожидаю план AI" if self.task else "Ожидаю следующий цикл AI"), "motor", ""
