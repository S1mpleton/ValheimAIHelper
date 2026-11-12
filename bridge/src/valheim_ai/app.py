import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import secrets
import time
import re

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, SecretStr, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
import httpx

from .protocol import Action, DecisionReply, Snapshot, StrictModel, validate_decision, wait, Decision, Plan
from .providers import (ProviderError, ProviderUnavailable, ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError,
                        decide, list_gemini_models, list_openai_models, diagnose_gemini,
                        API_TIMEOUT, DECISION_TIMEOUT, OLLAMA_DECISION_TIMEOUT, CONNECTED_TIMEOUT)
from .ollama import DEFAULT_URL, local_url, valid_model, list_models as list_ollama_models
from .diagnostics import validation_diagnostics
from . import __version__
from .planning import Planner


class ProviderSettings(StrictModel):
    provider: str = Field(pattern="^(openai|gemini|ollama|offline)$")
    model: str = Field(default="", max_length=200)
    api_key: SecretStr = SecretStr("")
    request_interval_seconds: int = Field(default=20, ge=4, le=300)
    ollama_url: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def provider_fields(self):
        if self.model: validate_model_name(self.provider, self.model)
        if self.provider == "ollama" and self.ollama_url: self.ollama_url = local_url(self.ollama_url)
        return self


class ModelListRequest(StrictModel):
    api_key: SecretStr = SecretStr("")
    ollama_url: str | None = Field(default=None, max_length=256)


class ProviderCheckRequest(ModelListRequest):
    model: str = Field(min_length=1, max_length=200)


def validate_model_name(provider: str, name: str):
    if not (valid_model(name) if provider == "ollama" else re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", name)):
        raise ValueError("Неверное имя модели для выбранного провайдера")


class Command(StrictModel):
    mode: str = Field(pattern="^(stop|follow|ai)$")
    objective: str = Field(default="Следуй за лидером и помогай ему выжить.", max_length=1000)


class ChatRequest(StrictModel):
    actor_name: str = Field(min_length=1, max_length=120)
    speaker: str = Field(max_length=120)
    text: str = Field(min_length=1, max_length=1000)
    explicit_address: bool = False


class State:
    def __init__(self, token: str, client: httpx.AsyncClient, ollama_client: httpx.AsyncClient | None = None):
        self.token = token
        self.client = client
        self.ollama_url = DEFAULT_URL
        self.ollama_client = ollama_client
        self.owns_ollama_client = ollama_client is None
        self.provider = "offline"
        self.model = ""
        self.keys = {
            "openai": os.environ.get("VALHEIM_OPENAI_API_KEY", ""),
            "gemini": os.environ.get("VALHEIM_GEMINI_API_KEY", ""),
        }
        self.command = Command(mode="stop")
        self.revision = 0
        self.busy = False
        self.retry_after = 0.0
        self.next_ai_request = 0.0
        self.request_interval_seconds = 20
        self.session_id: str | None = None
        self.last_sequence = 0
        self.last_seen = 0.0
        self.last_snapshot: Snapshot | None = None
        self.snapshot_error: dict | None = None
        self.provider_error: dict | None = None
        self.events: deque[dict] = deque(maxlen=50)
        self.event_id = 0
        self.instance_id = secrets.token_hex(8)
        self.planner = Planner(self)
        self.chat_history: deque[dict] = deque(maxlen=12)
        self.pending_chat: deque[dict] = deque(maxlen=6)
        self.chat_id = 0
        self.chat_busy = False
        self.chat_listening = True
        self.chat_suspended = False
        self.chat_task: asyncio.Task | None = None
        self.check_task: asyncio.Task | None = None

    @property
    def ready(self):
        return bool(self.model and (self.provider == "ollama" or self.keys.get(self.provider)))

    @property
    def decision_timeout(self):
        return OLLAMA_DECISION_TIMEOUT if self.provider == "ollama" else DECISION_TIMEOUT

    def client_for(self, provider):
        if provider != "ollama": return self.client
        if self.ollama_client is None:
            # Loopback inference must never inherit a corporate/cloud proxy or
            # cloud-provider credentials. URLs are validated numeric loopback.
            self.ollama_client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(OLLAMA_DECISION_TIMEOUT, connect=5), limits=httpx.Limits(max_connections=4))
        return self.ollama_client

    async def decide(self, objective, snapshot, **kwargs):
        return await decide(self.client_for(self.provider), self.provider, self.model, self.keys.get(self.provider, ""),
            objective, snapshot, ollama_url=self.ollama_url, **kwargs)

    def cancel_chat(self):
        if self.chat_task is not None:
            self.chat_task.cancel()
        self.chat_task = None
        self.chat_busy = False
        self.pending_chat.clear()

    def cancel_check(self):
        if self.check_task is not None: self.check_task.cancel()
        self.check_task = None

    def event(self, source: str, message: str):
        if self.events and self.events[0]["source"] == source and self.events[0]["message"] == message:
            return
        self.event_id += 1
        self.events.appendleft({"id": self.event_id, "time": time.strftime("%H:%M:%S"), "source": source, "message": message[:1800]})


def load_token(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "bridge-token"
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise RuntimeError("Invalid bridge-token file: remove it to create a new pairing token")
        return token
    token = secrets.token_urlsafe(32)
    # Exclusive creation: never silently overwrite an existing token.
    with path.open("x", encoding="utf-8") as file:
        file.write(token + "\n")
    path.chmod(0o600)
    return token


def create_app(token: str | None = None, client: httpx.AsyncClient | None = None,
               ollama_client: httpx.AsyncClient | None = None) -> FastAPI:
    owned_client = client is None
    client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(API_TIMEOUT, connect=5), follow_redirects=False,
        limits=httpx.Limits(max_connections=4),
    )
    token = token or load_token(Path(os.environ.get("VALHEIM_AI_DATA_DIR", ".local")))
    state = State(token, client, ollama_client)

    @asynccontextmanager
    async def lifespan(app):
        yield
        chat_task = state.chat_task
        check_task = state.check_task
        state.cancel_chat()
        state.cancel_check()
        if chat_task is not None: await asyncio.gather(chat_task, return_exceptions=True)
        if check_task is not None: await asyncio.gather(check_task, return_exceptions=True)
        await state.planner.close()
        if owned_client:
            await client.aclose()
        if state.owns_ollama_client and state.ollama_client is not None:
            await state.ollama_client.aclose()

    app = FastAPI(title="Valheim AI Helper", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bridge = state
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default errors include raw rejected input; it may contain an API key.
        detail, errors = validation_diagnostics(exc.errors())
        valid_token = hmac.compare_digest(request.headers.get("authorization", "").encode(),
                                          ("Bearer " + state.token).encode())
        if request.url.path == "/api/decision" and valid_token:
            state.snapshot_error = {"message": "Данные игры отклонены (HTTP 422). " + detail, "errors": errors}
            if not state.events or state.events[0]["message"] != state.snapshot_error["message"]:
                state.event("bridge", state.snapshot_error["message"])
        return JSONResponse({"detail": detail, "errors": errors}, status_code=422)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "Cross-origin requests are not accepted"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests are not accepted"}, status_code=403)
        if request.method == "POST":
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid length"}, status_code=400)
            if length < 0 or length > 131072:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
            # Also cap chunked requests rather than trusting Content-Length alone.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 131072:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    async def authenticate(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + state.token).encode()):
            raise HTTPException(status_code=401, detail="Нужен код сопряжения bridge-token")

    static_dir = Path(__file__).parent / "static"

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/app.js")
    async def script():
        return FileResponse(static_dir / "app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def stylesheet():
        return FileResponse(static_dir / "style.css", media_type="text/css")

    @app.get("/health")
    async def health():
        return {"status": "ok", "protocol_version": 1, "version": __version__}

    @app.get("/api/status", dependencies=[Depends(authenticate)])
    async def status():
        return {
            "provider": state.provider,
            "model": state.model,
            "ollama_url": state.ollama_url,
            "provider_ready": state.ready,
            "request_interval_seconds": state.request_interval_seconds,
            "keys_present": {k: bool(v) for k, v in state.keys.items()},
            "mode": state.command.mode,
            "objective": state.command.objective,
            # Allow the configured mod polling interval (up to 60s) plus API latency.
            "connected": bool(state.last_seen and time.monotonic() - state.last_seen < CONNECTED_TIMEOUT),
            "actor": state.last_snapshot.actor.model_dump() if state.last_snapshot else None,
            "leader_id": state.last_snapshot.leader_id if state.last_snapshot else "",
            "snapshot_error": state.snapshot_error,
            "provider_error": state.provider_error,
            "events": list(state.events),
            "instance_id": state.instance_id,
            "planning": state.planner.task is not None,
            "checking_model": state.check_task is not None,
        }

    @app.post("/api/models/{provider}", dependencies=[Depends(authenticate)])
    async def models(provider: str, settings: ModelListRequest):
        if provider not in (*state.keys, "ollama"):
            raise HTTPException(404, "Неизвестный провайдер")
        key = settings.api_key.get_secret_value() or state.keys.get(provider, "")
        if provider != "ollama" and not key:
            raise HTTPException(409, "Введите API-ключ или примените его в настройках")
        try:
            if provider == "ollama":
                values = await asyncio.wait_for(list_ollama_models(state.client_for(provider), local_url(settings.ollama_url or state.ollama_url)), 20)
            else:
                loader = list_gemini_models if provider == "gemini" else list_openai_models
                values = await asyncio.wait_for(loader(state.client, key), DECISION_TIMEOUT)
            return {"models": values}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except (ProviderError, TimeoutError) as exc:
            message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
            if isinstance(exc, ProviderError) and exc.diagnostic:
                message += " Ответ API: " + exc.diagnostic
            raise HTTPException(502, message) from None

    @app.post("/api/check/{provider}", dependencies=[Depends(authenticate)])
    async def check_provider(provider: str, settings: ProviderCheckRequest):
        if provider not in (*state.keys, "ollama"):
            raise HTTPException(404, "Неизвестный провайдер")
        try:
            validate_model_name(provider, settings.model)
            base = local_url(settings.ollama_url or state.ollama_url) if provider == "ollama" else DEFAULT_URL
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        key = settings.api_key.get_secret_value() or state.keys.get(provider, "")
        if provider != "ollama" and not key:
            raise HTTPException(409, "Введите API-ключ или примените его в настройках")
        # Explicit operator-triggered check uses the exact production adapter and schema,
        # but no game observations or control state. The result is never sent to the mod.
        probe = Snapshot(
            session_id="api-check", sequence=1, supported_actions=["wait"],
            actor={"id": "api-check", "name": "API check", "position": {"x": 0, "y": 0, "z": 0},
                   "health": 1, "max_health": 1, "stamina": 0},
        )
        if state.check_task is not None: raise HTTPException(409, "Предыдущая проверка модели ещё выполняется. Стоп/F9 отменяет её.")
        revision = state.revision
        async def perform_check():
            if provider == "gemini":
                return await diagnose_gemini(state.client, settings.model, key, probe)
            elif provider == "ollama":
                await decide(state.client_for(provider), provider, settings.model, "",
                    "Проверка подключения. Верни план из единственного действия wait, target_id='', duration=1, say='Связь работает'.",
                    probe, planning=True, ollama_url=base)
                return ["Ollama: локальная модель доступна", "JSON-план игровых действий: OK"]
            else:
                await decide(state.client, provider, settings.model, key,
                             "Проверка API. Верни wait, пустой target_id, duration=1 и короткое say.", probe)
                return ["Формат действий: OK"]
        state.planner.interrupt_planning()
        state.cancel_chat()
        task = asyncio.create_task(perform_check())
        state.check_task = task
        try:
            checks = await asyncio.wait_for(task, OLLAMA_DECISION_TIMEOUT if provider == "ollama" else DECISION_TIMEOUT)
        except asyncio.CancelledError:
            if revision != state.revision: raise HTTPException(409, "Проверка модели отменена") from None
            raise
        except (ProviderError, TimeoutError) as exc:
            message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
            if provider == "openai" and isinstance(exc, ProviderError) and exc.diagnostic:
                message += " Ответ API: " + exc.diagnostic
            raise HTTPException(502, message) from None
        finally:
            if state.check_task is task: state.check_task = None
        if revision != state.revision: raise HTTPException(409, "Проверка модели отменена")
        if (state.provider == provider and state.model == settings.model
                and (state.ollama_url == base if provider == "ollama" else state.keys[provider] == key)):
            state.provider_error = None
            state.chat_suspended = False
        return {"ok": True, "model": settings.model, "checks": checks}

    @app.post("/api/provider", dependencies=[Depends(authenticate)])
    async def configure(settings: ProviderSettings):
        if settings.provider != "offline" and not settings.model:
            raise HTTPException(422, "Укажите модель")
        if settings.provider in state.keys and settings.api_key.get_secret_value():
            state.keys[settings.provider] = settings.api_key.get_secret_value()
        if settings.provider == "ollama": state.ollama_url = local_url(settings.ollama_url or state.ollama_url)
        state.retry_after = 0.0
        state.next_ai_request = 0.0
        state.request_interval_seconds = settings.request_interval_seconds
        state.provider_error = None
        state.chat_suspended = False
        state.provider = settings.provider
        state.model = settings.model
        state.revision += 1
        state.cancel_chat()
        state.cancel_check()
        state.planner.reset()
        # Changing providers never implicitly starts control.
        state.command = Command(mode="stop", objective=state.command.objective)
        state.event("bridge", "Провайдер изменён; управление остановлено")
        return {"ok": True, "key_present": bool(state.keys.get(settings.provider))}

    @app.post("/api/command", dependencies=[Depends(authenticate)])
    async def command(value: Command):
        if value.mode != "stop" and (state.last_snapshot is None or state.snapshot_error or time.monotonic() - state.last_seen >= CONNECTED_TIMEOUT):
            reason = state.snapshot_error["message"] if state.snapshot_error else "Сначала включите мод через F8 и дождитесь данных персонажа в панели"
            raise HTTPException(409, reason)
        if value.mode == "ai" and not state.ready:
            raise HTTPException(409, "Сначала выберите провайдера и добавьте API-ключ")
        state.command = value
        state.revision += 1
        state.cancel_chat()
        state.cancel_check()
        state.planner.reset()
        state.next_ai_request = 0
        state.event("operator", {"stop": "Стоп", "follow": "Следовать за лидером", "ai": value.objective}[value.mode])
        return {"ok": True, "revision": state.revision}

    @app.post("/api/chat", dependencies=[Depends(authenticate)])
    async def leader_chat(value: ChatRequest):
        if not state.chat_listening:
            return {"handled": False, "queued": False, "mode": "ignore", "objective": "", "reply": ""}
        state.chat_id += 1
        line = {"id": state.chat_id, "speaker": value.speaker, "text": value.text, "explicit_address": value.explicit_address}
        state.chat_history.append(line)
        state.event("leader-chat", value.speaker + ": " + value.text)
        if state.command.mode == "ai":
            state.pending_chat.append(line)
            if value.explicit_address:
                state.planner.interrupt_planning()
                state.next_ai_request = 0
            return {"handled": False, "queued": True, "mode": "ignore", "objective": "", "reply": ""}
        if not state.ready or state.chat_busy or state.chat_suspended or state.check_task is not None:
            return {"handled": False, "queued": False, "mode": "ignore", "objective": "", "reply": ""}
        # A listener may classify while native following/manual control continues.
        # It returns an intention only; the mod's command epoch decides whether to
        # enable control. A late classifier result cannot undo F9.
        state.chat_busy = True
        revision = state.revision
        chat_task = None
        probe = state.last_snapshot or Snapshot(session_id="chat-context", sequence=1, supported_actions=["wait"],
            actor={"id":"chat-context","name":value.actor_name,"health":1,"max_health":1,"stamina":0,"position":{"x":0,"y":0,"z":0}})
        try:
            delay = max(state.next_ai_request, state.retry_after) - time.monotonic()
            if delay > 20:
                return {"handled": False, "queued": False, "mode": "ignore", "objective": "", "reply": ""}
            async def classify():
                if delay > 0: await asyncio.sleep(delay)
                state.next_ai_request = time.monotonic() + state.request_interval_seconds
                return await state.decide(
                    "Классифицируй обращение к помощнику по контексту. Текущий режим: " + state.command.mode +
                    "; задача: " + state.command.objective + ". Не управляй персонажем: steps=[wait].", probe,
                    planning=True, chat=tuple({**m,"pending":m["id"]==line["id"]} for m in state.chat_history))
            chat_task = asyncio.create_task(classify())
            state.chat_task = chat_task
            result = await asyncio.wait_for(chat_task, state.decision_timeout)
            intent = result.chat_intent if isinstance(result, Plan) else None
            if revision != state.revision or intent is None or intent.message_id != line["id"] or not intent.addressed:
                return {"handled": False, "queued": False, "mode": "ignore", "objective": "", "reply": ""}
            return {"handled": intent.mode != "ignore", "queued": False, "mode": intent.mode, "objective": intent.objective, "reply": intent.reply}
        except asyncio.CancelledError:
            if revision != state.revision or not state.chat_listening:
                return {"handled":False,"queued":False,"mode":"ignore","objective":"","reply":""}
            raise
        except (ProviderError, TimeoutError) as exc:
            if isinstance(exc, (ProviderUnavailable, TimeoutError)): state.retry_after = time.monotonic() + 15
            if isinstance(exc, (ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError)):
                state.chat_suspended = True
                state.provider_error = {"provider":state.provider,"model":state.model,"message":str(exc),"detail":exc.diagnostic}
            state.event("chat", str(exc) if isinstance(exc, ProviderError) else "Время ожидания контекстного обращения истекло")
            return {"handled":False,"queued":False,"mode":"ignore","objective":"","reply":""}
        finally:
            if state.chat_task is chat_task:
                state.chat_busy = False
                state.chat_task = None

    @app.post("/api/decision", dependencies=[Depends(authenticate)])
    async def decision(snapshot: Snapshot):
        now = time.monotonic()
        if state.session_id != snapshot.session_id:
            same_actor = state.last_snapshot is not None and state.last_snapshot.actor.id == snapshot.actor.id
            if state.session_id and not same_actor and now - state.last_seen < 30:
                raise HTTPException(409, "Мост уже обслуживает другого персонажа")
            state.session_id = snapshot.session_id
            state.last_sequence = 0
            state.revision += 1
            state.cancel_chat()
            state.chat_history.clear()
            state.planner.reset()
            # A reconnected/new character always starts stopped.
            state.command = Command(mode="stop", objective=state.command.objective)
        if snapshot.sequence <= state.last_sequence:
            raise HTTPException(409, "Устаревшее наблюдение")
        state.last_sequence = snapshot.sequence
        if state.chat_listening and not snapshot.listen_chat:
            state.cancel_chat()
            policy = state.planner.combat_policy
            state.planner.reset()
            state.planner.combat_policy = policy
            state.chat_history.clear()
        state.chat_listening = snapshot.listen_chat
        state.last_seen = now
        state.last_snapshot = snapshot
        state.snapshot_error = None
        revision = state.revision
        mode = state.command.mode
        source = "local"
        stop_for_api_error = False
        api_attempted = False
        api_error = None
        result = wait()
        if mode == "ai" and snapshot.background_planning:
            result, source, plan_id = state.planner.decision(snapshot)
            result = validate_decision(result, snapshot)
            return DecisionReply(**result.model_dump(), sequence=snapshot.sequence, source=source,
                                 command_revision=state.revision, plan_id=plan_id, mode=state.command.mode,
                                 avoid_combat=state.planner.combat_policy == "avoid")
        if mode == "follow" and snapshot.leader_id:
            result = Decision(action=Action.FOLLOW, target_id=snapshot.leader_id, duration=3, say="Следую за лидером")
        elif mode == "follow":
            result = wait("Лидер не виден. Подойдите к нему или задайте Player.LeaderName в конфигурации мода")
        elif mode == "ai":
            if time.monotonic() < state.retry_after:
                result = wait("API перегружен (503); пауза 15 секунд перед новым запросом")
                source = "fallback"
            elif time.monotonic() < state.next_ai_request:
                result = wait("Пауза между решениями API; следование без AI доступно без паузы")
            elif state.check_task is not None:
                result = wait("Выполняется проверка модели")
            elif state.busy:
                result = wait("Предыдущий запрос ещё выполняется")
            elif not state.ready:
                result = wait("Не задан API-ключ")
            else:
                state.busy = True
                api_attempted = True
                state.next_ai_request = time.monotonic() + state.request_interval_seconds
                try:
                    result = await asyncio.wait_for(state.decide(state.command.objective, snapshot), timeout=state.decision_timeout)
                    source = state.provider
                except (ProviderError, TimeoutError) as exc:
                    stop_for_api_error = isinstance(exc, (ProviderModelNotFound, ProviderRateLimited, ProviderLocalConfigurationError))
                    if isinstance(exc, ProviderUnavailable):
                        state.retry_after = time.monotonic() + 15
                    message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
                    api_error = {"provider": state.provider, "model": state.model, "message": message,
                                 "detail": exc.diagnostic if isinstance(exc, ProviderError) else ""}
                    result = wait(message)
                    source = "fallback"
                finally:
                    state.busy = False
        # Stop/provider/objective changes and newer observations invalidate in-flight answers.
        if revision != state.revision or snapshot.sequence != state.last_sequence:
            result = wait("Команда изменилась; старый ответ отменён")
            source = "fallback"
        else:
            if api_attempted:
                state.provider_error = api_error
            if stop_for_api_error:
                state.chat_suspended = True
                state.command = Command(mode="stop", objective=state.command.objective)
                state.revision += 1
                result = wait("AI остановлен. " + result.say)
        result = validate_decision(result, snapshot)
        state.event(source, result.say or result.action.value)
        return DecisionReply(**result.model_dump(), sequence=snapshot.sequence,
                             source=source, command_revision=state.revision, mode=state.command.mode)

    return app
