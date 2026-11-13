"""Slow model planning and fast, freshly validated control leases are independent."""
import asyncio
from collections import deque
import time
import uuid

from .protocol import Action, Decision, Plan, validate_decision, wait
from .providers import (ProviderError, ProviderUnavailable,
                        ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError, ProviderInvalidAction)

from .task_policy import IDLE_OBJECTIVE, COMPANION_OBJECTIVE, companion_duty, planning_view

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
        self.announced = False
        self.invalid_plans = 0
        self.last_tick = 0.0
        self.was_paused = False

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
        self.announced = False
        self.invalid_plans = 0
        self.last_tick = 0.0
        self.was_paused = False

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
                state.command.objective, planning_view(state.command.objective, snapshot, pending_ids), planning=True, history=tuple(self.history), chat=chat,
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
            if state.explicit_chat_id in pending_ids:
                if intent and intent.message_id != state.explicit_chat_id: intent = None
                if intent and intent.addressed and intent.mode == "reply" and state.explicit_chat_mission is not None:
                    state.command = state.command.model_copy(update={"objective": state.explicit_chat_mission})
                state.explicit_chat_mission = None
                state.explicit_chat_id = 0
            if intent is not None and intent.message_id in pending_ids and intent.addressed and intent.mode != "ignore":
                if intent.mode in ("task", "follow", "stop"):
                    mode = "ai" if intent.mode == "task" else intent.mode
                    state.command = state.command.model_copy(update={"mode": mode, "objective": intent.objective or state.command.objective})
                    state.revision += 1
                    self.successful_steps = 0
                    self.announced = False
                    state.event("operator", "Из обычного чата: " + state.command.objective if mode == "ai" else "Из чата: " + mode)
                    state.feedback("Приступаю к поручению. Сверяю действия с миром.", key="accepted-chat-" + str(intent.message_id))
                if intent.reply and intent.mode == "reply":
                    state.event("assistant", intent.reply)
                    state.chat_history.append({"id": 0, "speaker": "assistant", "text": intent.reply, "explicit_address": False})
            state.pending_chat = deque((m for m in state.pending_chat if m["id"] not in pending_ids), maxlen=6)
            if state.command.mode != "ai":
                self.steps.clear(); self.plan_id = ""
                return
            self.steps = deque(result.steps)
            if companion_duty(state.command.objective) and snapshot.leader_id and "follow" in snapshot.supported_actions and all(s.action == Action.WAIT for s in self.steps):
                self.steps = deque([Decision(action=Action.FOLLOW,target_id=snapshot.leader_id,duration=3,say="Возвращаюсь к лидеру")])
            if result.objective_complete and self.successful_steps > 0 and not (intent and intent.addressed and intent.mode == "task"):
                state.event("mission", "Поручение выполнено: " + state.command.objective)
                state.command = state.command.model_copy(update={"objective":IDLE_OBJECTIVE})
                self.successful_steps = 0
                self.steps = deque([Decision(action=Action.FOLLOW,target_id=snapshot.leader_id,duration=3,say="Возвращаюсь к лидеру")]
                    if snapshot.leader_id and "follow" in snapshot.supported_actions else [wait("Поручение выполнено; ожидаю новое")])
            self.combat_policy = result.combat_policy
            self._next_id()
            self.invalid_plans = 0
            state.provider_error = None
            state.chat_suspended = False
            state.event(state.provider, "План: " + " → ".join(s.say or s.action.value for s in result.steps))
            if not self.announced and result.steps[0].action not in (Action.WAIT, Action.SPEAK):
                state.feedback("План готов. Приступаю к выполнению.", key="start-plan", interval=5)
                self.announced = True
            elif result.steps[0].action == Action.WAIT and result.steps[0].say:
                state.feedback("AI выбрала ожидание: " + result.steps[0].say, key="waiting-plan")
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
            if isinstance(exc, ProviderInvalidAction):
                self.invalid_plans += 1
                self.history.append(message[:240])
                state.retry_after = time.monotonic() + 15
            if isinstance(exc, (ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError)) or self.invalid_plans >= 3:
                state.chat_suspended = True
                state.command = state.command.model_copy(update={"mode": "stop"})
                state.revision += 1
                self.steps.clear()
                self.plan_id = ""
                message = "AI остановлен. " + message
            state.event("fallback", message)
            state.feedback("Не могу выполнить поручение: " + message, key="plan-error-" + str(exc))
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
        if self.last_tick and (snapshot.simulation_paused or self.was_paused): self.step_started += max(0, now - self.last_tick)
        self.last_tick = now; self.was_paused = snapshot.simulation_paused
        if state.command.mode != "ai":
            return wait(), "local", ""
        if self.steps:
            step = self.steps[0]
            terminal = snapshot.plan_id == self.plan_id and snapshot.action_status in ("success", "blocked")
            budget = float("inf") if step.action == Action.FOLLOW else step.duration if step.action == Action.WAIT else 90 if step.action in (Action.PICKUP, Action.GIVE) else min(60, step.amount * 5 + 5) if step.action == Action.CRAFT else 25
            if terminal or now - self.step_started >= budget:
                outcome = snapshot.previous_result if terminal else "Ожидание завершено" if step.action == Action.WAIT else "Шаг исчерпал время"
                self.history.append((step.action.value + ": " + outcome)[:240])
                state.event("motor", self.history[-1])
                if terminal and snapshot.action_status == "success" and step.action not in (Action.WAIT, Action.FOLLOW): self.successful_steps += 1
                if step.action not in (Action.WAIT, Action.FOLLOW): self.execution_epoch += 1
                if terminal and snapshot.action_status == "success" and step.action == Action.SPEAK:
                    state.chat_history.append({"id":0,"speaker":"assistant","text":step.text,"explicit_address":False})
                self.steps.popleft()
                if terminal and snapshot.action_status == "success" and step.action == Action.SPEAK and not self.steps and not state.pending_chat:
                    # The game confirmed that this answer was spoken. A small
                    # model need not explicitly mark the question complete or
                    # keep answering the same question on every decision tick.
                    state.event("mission", "Ответ передан; ожидаю новое поручение")
                    state.command = state.command.model_copy(update={"objective": IDLE_OBJECTIVE})
                    self.successful_steps = 0
                if (terminal and snapshot.action_status == "blocked") or (not terminal and step.action not in (Action.WAIT, Action.FOLLOW)):
                    state.feedback("Не удалось выполнить " + step.action.value + ": " + outcome, key="blocked-" + step.action.value + outcome)
                elif terminal and snapshot.action_status == "success" and step.action in (Action.PICKUP, Action.DROP, Action.GIVE, Action.TAKE, Action.DEPOSIT):
                    state.feedback(outcome or "Игра подтвердила действие с предметами", key="success-" + self.plan_id, interval=0)
                    # Finishing a one-off transfer must not depend on a small
                    # model remembering to set objective_complete next cycle.
                    if not self.steps and step.action in (Action.DROP, Action.GIVE) and not state.pending_chat:
                        state.event("mission", "Передача/сброс завершены: " + state.command.objective)
                        state.command = state.command.model_copy(update={"objective": IDLE_OBJECTIVE})
                        self.successful_steps = 0
                if terminal and snapshot.action_status == "blocked" and step.action in (Action.GIVE, Action.DROP) and snapshot.action_completed_amount > 0:
                    # Do not let a new plan repeat the ORIGINAL quantity after
                    # a partial mutation. A new user request states the remainder.
                    self.steps.clear()
                    state.command = state.command.model_copy(update={"objective": IDLE_OBJECTIVE})
                    state.feedback("Передача выполнена частично (" + str(snapshot.action_completed_amount) + "). Остаток у NPC; дайте новое поручение с нужным количеством.", key="partial-" + self.plan_id, interval=0)
                # A failed prerequisite invalidates dependent steps in that plan.
                if (terminal and snapshot.action_status == "blocked") or not terminal:
                    self.steps.clear()
                self._next_id()
        can_replan = bool(state.pending_chat) or (state.command.objective != IDLE_OBJECTIVE and
                     (not self.steps or (len(self.steps) == 1 and self.steps[0].action == Action.WAIT)))
        if (can_replan and self.task is None and not state.chat_busy and state.check_task is None and now >= max(state.next_ai_request, state.retry_after)
                and state.ready and not snapshot.simulation_paused and (snapshot.leader_id or not companion_duty(state.command.objective) or state.pending_chat)):
            state.next_ai_request = now + state.request_interval_seconds
            self.task = asyncio.create_task(self._plan(snapshot, state.revision, state.session_id))
        if self.steps:
            try:
                step = validate_decision(self.steps[0], snapshot)
                return step.model_copy(update={"duration": 3}), state.provider, self.plan_id
            except ValueError:
                self.history.append("Цель плана больше не доступна в свежем наблюдении")
                state.feedback("Не могу продолжить: цель исчезла, количество предметов изменилось или действие больше недоступно.", key="target-changed")
                self.steps.clear()
                self._next_id()
        # Keep a useful, explicitly requested follow going while a model thinks.
        if snapshot.leader_id and "follow" in snapshot.supported_actions and companion_duty(state.command.objective):
            return Decision(action=Action.FOLLOW, target_id=snapshot.leader_id, duration=3,
                            say="Рядом с лидером; ожидаю план AI"), "motor", "follow-pending"
        message = "Лидер не виден. Выберите игрока во вкладке «Персонаж»; NPC ожидает на месте" if companion_duty(state.command.objective) and not snapshot.leader_id else "Ожидаю план AI" if self.task else "Ожидаю следующий цикл AI"
        return wait(message).model_copy(update={"duration": 3}), "motor", ""
