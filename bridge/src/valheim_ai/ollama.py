"""Native, local-only Ollama adapter. No cloud credentials, pull or shell calls."""
import asyncio
import ipaddress
import re
from urllib.parse import urlsplit

import httpx

from .providers import (OLLAMA_DECISION_TIMEOUT, ProviderError, ProviderUnavailable,
                        ProviderModelNotFound, ProviderLocalConfigurationError)
from .performance import ollama_statistics, accumulate_statistics

DEFAULT_URL = "http://127.0.0.1:11434"
MODEL_PATTERN = r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}"


def local_url(value: str) -> str:
    try:
        if any(ord(c) <= 32 or ord(c) == 127 for c in value): raise ValueError()
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "http" or not host or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment): raise ValueError()
        if host.lower() == "localhost": host = "127.0.0.1"
        address = ipaddress.ip_address(host)
        if not address.is_loopback: raise ValueError()
        port = parsed.port
        if port is not None and not 1 <= port <= 65535: raise ValueError()
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        return f"http://{host}" + (f":{port}" if port is not None else "")
    except (ValueError, TypeError):
        raise ValueError("Ollama: нужен локальный HTTP-адрес, например http://127.0.0.1:11434, без пути и пароля") from None


def valid_model(name: str) -> bool:
    return bool(re.fullmatch(MODEL_PATTERN, name)) and not any(p in ("", ".", "..") for p in name.split("/"))


def cloud_model(name: str, metadata: dict | None = None) -> bool:
    data = metadata or {}
    return name.lower().endswith((":cloud", "-cloud")) or bool(data.get("remote_host") or data.get("remote_model"))


def _error_detail(response: httpx.Response) -> str:
    """Keep the server's explanation, bounded and safe for logs/local UI."""
    try:
        data = response.json()
        detail = data.get("error", "") if isinstance(data, dict) else ""
        if isinstance(detail, dict): detail = detail.get("message", "")
    except (ValueError, UnicodeError):
        detail = response.text
    if not isinstance(detail, str): return ""
    # Local Ollama needs no credential, but an upstream error can echo configuration.
    detail = re.sub(r"(?:sk-|AQ\.|AIza)[A-Za-z0-9_-]{20,}", "[REDACTED]", detail)
    detail = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", detail)
    detail = re.sub(r"https?://\S+", "[URL]", detail)
    detail = re.sub(r"(?i)(api[_-]?key|access_token|token|key)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", detail)
    return " ".join("".join(c if c.isprintable() else " " for c in detail).split())[:600]


def _thinking_rejected(detail: str) -> bool:
    """Recognize an explicit parameter rejection, not an incidental 'think'."""
    patterns = (
        r"\b(?:does not|doesn't|do not|cannot|can't)\s+support\s+(?:the\s+)?(?:thinking|['\"]?think['\"]?(?:\s+(?:parameter|option|field))?)\b",
        r"\b(?:unknown|unrecognized|unsupported|invalid)\s+(?:parameter|option|field)\s*[:=]?\s*['\"]?think\b",
        r"\b(?:parameter|option|field)\s+['\"]?think['\"]?\s+(?:is\s+)?(?:unsupported|not supported|not recognized|unknown)\b",
        r"\b(?:thinking|think)\s+(?:value\s+)?['\"]?false['\"]?\s+(?:is\s+)?(?:unsupported|not supported|not allowed)\b",
        r"\b(?:cannot|can't|does not|doesn't)\s+(?:disable|turn off)\s+thinking\b",
    )
    return any(re.search(pattern, detail, re.IGNORECASE) for pattern in patterns)


async def request(client: httpx.AsyncClient, base: str, method: str, path: str, *, payload=None, timeout=15):
    try:
        response = await asyncio.wait_for(client.request(method, local_url(base) + path, json=payload,
            timeout=httpx.Timeout(timeout, connect=5), follow_redirects=False), timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise ProviderUnavailable("Ollama не запущена или недоступна по локальному адресу. Запустите Ollama и проверьте адрес.") from None
    except (httpx.RequestError, TimeoutError):
        raise ProviderUnavailable("Ollama не ответила вовремя. Первая загрузка модели может занять несколько минут; проверьте память и выберите меньшую модель.") from None
    if response.is_redirect:
        raise ProviderLocalConfigurationError(f"Ollama: перенаправление (HTTP {response.status_code}) не поддерживается. Укажите прямой локальный адрес.")
    if response.is_error:
        detail = _error_detail(response)
        status = f"HTTP {response.status_code}"
        if response.status_code == 404:
            error = ProviderModelNotFound(f"Ollama: модель не установлена или API недоступен ({status}). Проверьте имя в ollama list и версию Ollama.")
        elif response.status_code == 503:
            error = ProviderUnavailable(f"Ollama занята ({status}). Повтор после паузы.")
        elif any(s in detail.lower() for s in ("memory", "out of memory", "cuda", "vram")):
            error = ProviderLocalConfigurationError(f"Ollama: ошибка памяти/устройства модели ({status}). Выберите меньшую текстовую модель и проверьте память/GPU.")
        elif (response.status_code in (400, 422) and method.upper() == "POST"
                and path in ("/api/chat", "/api/generate") and isinstance(payload, dict)
                and payload.get("think") is False and _thinking_rejected(detail)):
            error = ProviderLocalConfigurationError(f"Ollama отклонила отключение размышлений think=false ({status}). Обновите Ollama или выберите модель с поддержкой отключения размышлений.")
        else:
            error = ProviderLocalConfigurationError(f"Ollama отклонила запрос ({status}). Проверьте ответ Ollama, версию сервера и настройки модели.")
        error.diagnostic = detail
        raise error
    try:
        data = response.json()
        if not isinstance(data, dict) or data.get("error"): raise ValueError()
        return data
    except (ValueError, TypeError):
        raise ProviderError("Ollama вернула некорректный ответ JSON") from None


async def list_models(client: httpx.AsyncClient, base: str) -> list[str]:
    data = await request(client, base, "GET", "/api/tags")
    values = data.get("models")
    if not isinstance(values, list): raise ProviderError("Ollama вернула некорректный список моделей")
    names = set()
    for item in values:
        if not isinstance(item, dict): continue
        name = item.get("name", item.get("model", ""))
        if isinstance(name, str) and valid_model(name) and not cloud_model(name, item): names.add(name)
    return sorted(names)[:256]


async def model_info(client: httpx.AsyncClient, base: str, model: str) -> dict:
    if not valid_model(model): raise ProviderLocalConfigurationError("Ollama: неверное имя модели")
    if cloud_model(model): raise ProviderLocalConfigurationError("Выберите локальную модель Ollama: облачные модели не используются.")
    info = await request(client, base, "POST", "/api/show", payload={"model": model, "verbose": False})
    if cloud_model(model, info): raise ProviderLocalConfigurationError("Выберите локальную модель Ollama: эта модель выполняется в облаке.")
    capabilities = info.get("capabilities")
    if isinstance(capabilities, list) and "completion" not in capabilities:
        raise ProviderLocalConfigurationError("Эта модель Ollama не генерирует текст. Выберите текстовую instruct/chat-модель.")
    return info


def thinking_option(info: dict):
    values = info.get("thinking", {}).get("values") if isinstance(info.get("thinking"), dict) else None
    if isinstance(values, list) and values:
        if any(v is False for v in values): return False
        raise ProviderLocalConfigurationError("Ollama: эта модель не поддерживает отключение размышлений (think=false). Выберите модель с отключаемым reasoning.")
    family = info.get("details", {}).get("family", "") if isinstance(info.get("details"), dict) else ""
    if isinstance(family, str) and family.lower() in ("gptoss", "gpt-oss"):
        raise ProviderLocalConfigurationError("Ollama: у этой модели обязательные размышления. Нужна модель с поддержкой think=false.")
    return False


class ModelSession:
    """Serialize unload/load/inference; cancelling a request releases this lock."""
    def __init__(self, event):
        self.lock = asyncio.Lock()
        self.selected = None
        self.event = event

    async def _select(self, client, base, model):
        selection = (local_url(base), model)
        if selection == self.selected: return
        data = await request(client, base, "GET", "/api/ps")
        rows = data.get("models")
        if not isinstance(rows, list): raise ProviderError("Ollama: не удалось получить список загруженных моделей; переключение отменено")
        names = set()
        for row in rows:
            name = row.get("name", row.get("model", "")) if isinstance(row, dict) else ""
            if not isinstance(name, str) or not valid_model(name):
                raise ProviderError("Ollama: некорректное имя в списке загруженных моделей; переключение отменено")
            if name != model and name != model + ":latest": names.add(name)
        for name in sorted(names):
            await request(client, base, "POST", "/api/generate",
                payload={"model": name, "keep_alive": 0, "stream": False, "think": False}, timeout=30)
            self.event("ollama-memory", "Выгружена модель: " + name)
        if names:
            remaining = await request(client, base, "GET", "/api/ps")
            if not isinstance(remaining.get("models"), list) or any(
                row.get("name", row.get("model")) in names for row in remaining["models"] if isinstance(row, dict)):
                raise ProviderUnavailable("Ollama ещё не выгрузила прежние модели. Дождитесь освобождения памяти и повторите переключение.")
        self.selected = selection
        self.event("ollama-memory", "Выбрана модель: " + model + "; остальные ранее загруженные модели выгружены")

    async def select(self, client, base, model):
        async with self.lock:
            await self._select(client, base, model)

    async def generate(self, client, base, model, messages, schema, statistics, trace=None, num_predict=2048):
        async with self.lock:
            await self._select(client, base, model)
            return await generate(client, base, model, messages, schema, statistics=statistics, trace=trace, num_predict=num_predict)


async def generate(client: httpx.AsyncClient, base: str, model: str, messages: list, schema: dict, *, statistics=None, trace=None, num_predict=2048) -> str:
    info = await model_info(client, base, model)
    payload = {"model": model, "messages": messages, "format": schema, "stream": False,
               "keep_alive": "10m", "options": {"temperature": 0, "num_ctx": 16384, "num_predict": num_predict}}
    payload["think"] = thinking_option(info)
    if trace is not None: trace.set("request", payload)
    data = await request(client, base, "POST", "/api/chat", payload=payload, timeout=OLLAMA_DECISION_TIMEOUT - 15)
    if statistics is not None: accumulate_statistics(statistics, ollama_statistics(data))
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if trace is not None and isinstance(content, str): trace.set("response", content)
    if not isinstance(content, str) or not content.strip() or data.get("done_reason") == "length":
        raise ProviderError("Ollama не завершила JSON с действием. Выберите instruct/chat-модель; reasoning-модель могла потратить лимит на размышления.")
    # Reasoning is a separate field; it is never executed, displayed or parsed.
    return content
