import asyncio
from collections import deque
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path
import secrets
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware
import httpx

from .protocol import Action, DecisionReply, Snapshot, StrictModel, validate_decision, wait, Decision
from .providers import (ProviderError, ProviderUnavailable, ProviderModelNotFound, ProviderRateLimited,
                        decide, list_gemini_models, list_openai_models, diagnose_gemini,
                        API_TIMEOUT, DECISION_TIMEOUT, CONNECTED_TIMEOUT)
from .diagnostics import validation_diagnostics
from . import __version__
from .planning import Planner


class ProviderSettings(StrictModel):
    provider: str = Field(pattern="^(openai|gemini|offline)$")
    model: str = Field(default="", max_length=100, pattern=r"^[a-zA-Z0-9._-]*$")
    api_key: SecretStr = SecretStr("")
    request_interval_seconds: int = Field(default=20, ge=4, le=300)


class ModelListRequest(StrictModel):
    api_key: SecretStr = SecretStr("")


class ProviderCheckRequest(ModelListRequest):
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")


class Command(StrictModel):
    mode: str = Field(pattern="^(stop|follow|ai)$")
    objective: str = Field(default="Следуй за лидером и помогай ему выжить.", max_length=1000)


class State:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self.token = token
        self.client = client
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


def create_app(token: str | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    owned_client = client is None
    client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(API_TIMEOUT, connect=5), follow_redirects=False,
        limits=httpx.Limits(max_connections=4),
    )
    token = token or load_token(Path(os.environ.get("VALHEIM_AI_DATA_DIR", ".local")))
    state = State(token, client)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await state.planner.close()
        if owned_client:
            await client.aclose()

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
            if length < 0 or length > 65536:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
            # Also cap chunked requests rather than trusting Content-Length alone.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
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
        }

    @app.post("/api/models/{provider}", dependencies=[Depends(authenticate)])
    async def models(provider: str, settings: ModelListRequest):
        if provider not in state.keys:
            raise HTTPException(404, "Неизвестный провайдер")
        key = settings.api_key.get_secret_value() or state.keys[provider]
        if not key:
            raise HTTPException(409, "Введите API-ключ или примените его в настройках")
        try:
            loader = list_gemini_models if provider == "gemini" else list_openai_models
            values = await asyncio.wait_for(loader(state.client, key), DECISION_TIMEOUT)
            return {"models": values}
        except (ProviderError, TimeoutError) as exc:
            message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
            if isinstance(exc, ProviderError) and exc.diagnostic:
                message += " Ответ API: " + exc.diagnostic
            raise HTTPException(502, message) from None

    @app.post("/api/check/{provider}", dependencies=[Depends(authenticate)])
    async def check_provider(provider: str, settings: ProviderCheckRequest):
        if provider not in state.keys:
            raise HTTPException(404, "Неизвестный провайдер")
        key = settings.api_key.get_secret_value() or state.keys[provider]
        if not key:
            raise HTTPException(409, "Введите API-ключ или примените его в настройках")
        # Explicit operator-triggered check uses the exact production adapter and schema,
        # but no game observations or control state. The result is never sent to the mod.
        probe = Snapshot(
            session_id="api-check", sequence=1, supported_actions=["wait"],
            actor={"id": "api-check", "name": "API check", "position": {"x": 0, "y": 0, "z": 0},
                   "health": 1, "max_health": 1, "stamina": 0},
        )
        try:
            if provider == "gemini":
                checks = await asyncio.wait_for(diagnose_gemini(state.client, settings.model, key, probe), DECISION_TIMEOUT)
            else:
                await asyncio.wait_for(decide(state.client, provider, settings.model, key,
                                             "Проверка API. Верни wait, пустой target_id, duration=1 и короткое say.", probe), DECISION_TIMEOUT)
                checks = ["Формат действий: OK"]
        except (ProviderError, TimeoutError) as exc:
            message = str(exc) if isinstance(exc, ProviderError) else "Время ожидания API истекло"
            if provider == "openai" and isinstance(exc, ProviderError) and exc.diagnostic:
                message += " Ответ API: " + exc.diagnostic
            raise HTTPException(502, message) from None
        if state.provider == provider and state.model == settings.model and state.keys[provider] == key:
            state.provider_error = None
        return {"ok": True, "model": settings.model, "checks": checks}

    @app.post("/api/provider", dependencies=[Depends(authenticate)])
    async def configure(settings: ProviderSettings):
        if settings.provider != "offline" and not settings.model:
            raise HTTPException(422, "Укажите модель")
        if settings.provider != "offline" and settings.api_key.get_secret_value():
            state.keys[settings.provider] = settings.api_key.get_secret_value()
        state.retry_after = 0.0
        state.next_ai_request = 0.0
        state.request_interval_seconds = settings.request_interval_seconds
        state.provider_error = None
        state.provider = settings.provider
        state.model = settings.model
        state.revision += 1
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
        if value.mode == "ai" and not state.keys.get(state.provider):
            raise HTTPException(409, "Сначала выберите провайдера и добавьте API-ключ")
        state.command = value
        state.revision += 1
        state.planner.reset()
        state.next_ai_request = 0
        state.event("operator", {"stop": "Стоп", "follow": "Следовать за лидером", "ai": value.objective}[value.mode])
        return {"ok": True, "revision": state.revision}

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
            state.planner.reset()
            # A reconnected/new character always starts stopped.
            state.command = Command(mode="stop", objective=state.command.objective)
        if snapshot.sequence <= state.last_sequence:
            raise HTTPException(409, "Устаревшее наблюдение")
        state.last_sequence = snapshot.sequence
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
                                 command_revision=state.revision, plan_id=plan_id, mode=state.command.mode)
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
            elif state.busy:
                result = wait("Предыдущий запрос ещё выполняется")
            elif not state.keys.get(state.provider):
                result = wait("Не задан API-ключ")
            else:
                state.busy = True
                api_attempted = True
                state.next_ai_request = time.monotonic() + state.request_interval_seconds
                try:
                    result = await asyncio.wait_for(decide(
                        state.client, state.provider, state.model, state.keys[state.provider],
                        state.command.objective, snapshot,
                    ), timeout=DECISION_TIMEOUT)
                    source = state.provider
                except (ProviderError, TimeoutError) as exc:
                    stop_for_api_error = isinstance(exc, (ProviderModelNotFound, ProviderRateLimited))
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
                state.command = Command(mode="stop", objective=state.command.objective)
                state.revision += 1
                result = wait("AI остановлен. " + result.say)
        result = validate_decision(result, snapshot)
        state.event(source, result.say or result.action.value)
        return DecisionReply(**result.model_dump(), sequence=snapshot.sequence,
                             source=source, command_revision=state.revision, mode=state.command.mode)

    return app
