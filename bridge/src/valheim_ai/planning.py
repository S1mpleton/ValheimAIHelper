"""Slow model planning and fast, freshly validated control leases are independent."""
import asyncio
from collections import deque
import time
import uuid

from .protocol import Action, Decision, Plan, validate_decision, wait
from .providers import (decide, DECISION_TIMEOUT, ProviderError, ProviderUnavailable,
                        ProviderModelNotFound, ProviderRateLimited)


class Planner:
    def __init__(self, state):
        self.state = state
        self.task: asyncio.Task | None = None
        self.steps: deque[Decision] = deque()
        self.plan_id = ""
        self.step_started = 0.0
        self.history: deque[str] = deque(maxlen=8)

    def reset(self):
        if self.task is not None:
            self.task.cancel()
        self.task = None
        self.steps.clear()
        self.plan_id = ""
        self.history.clear()

    async def close(self):
        task = self.task
        self.reset()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _plan(self, snapshot, revision, session):
        state = self.state
        try:
            result = await asyncio.wait_for(decide(
                state.client, state.provider, state.model, state.keys[state.provider],
                state.command.objective, snapshot, planning=True, history=tuple(self.history),
            ), DECISION_TIMEOUT)
            if revision != state.revision or session != state.session_id or state.command.mode != "ai":
                return
            assert isinstance(result, Plan)
            self.steps = deque(result.steps)
            self._next_id()
            state.provider_error = None
            state.event(state.provider, "План: " + " → ".join(s.say or s.action.value for s in result.steps))
        except asyncio.CancelledError:
            raise
        except (ProviderError, TimeoutError) as exc:
            if revision != state.revision or session != state.session_id:
                return
            message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
            state.provider_error = {"provider": state.provider, "model": state.model, "message": message,
                                    "detail": exc.diagnostic if isinstance(exc, ProviderError) else ""}
            if isinstance(exc, ProviderUnavailable):
                state.retry_after = time.monotonic() + 15
            if isinstance(exc, (ProviderModelNotFound, ProviderRateLimited)):
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
            budget = step.duration if step.action == Action.WAIT else 25
            if terminal or now - self.step_started >= budget:
                outcome = snapshot.previous_result if terminal else "Шаг исчерпал время"
                self.history.append((step.action.value + ": " + outcome)[:240])
                state.event("motor", self.history[-1])
                self.steps.popleft()
                # A failed prerequisite invalidates dependent steps in that plan.
                if (terminal and snapshot.action_status == "blocked") or not terminal:
                    self.steps.clear()
                self._next_id()
        if (self.task is None and now >= max(state.next_ai_request, state.retry_after)
                and state.keys.get(state.provider)):
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
        if snapshot.leader_id and any(w in objective for w in ("след", "помог", "защ", "follow", "defend")):
            return Decision(action=Action.FOLLOW, target_id=snapshot.leader_id, duration=3,
                            say="Рядом с лидером; ожидаю план AI"), "motor", "follow-pending"
        return wait("Ожидаю план AI" if self.task else "Ожидаю следующий цикл AI"), "motor", ""
