"""Check selected-model availability with a tiny, unconstrained text request.

Connection settings do not test NPC planning, JSON schemas or game actions.
No observation, task pool, chat history or player order enters this request.
"""
import asyncio

import httpx

from .providers import (ProviderError, ProviderModelNotFound, ProviderUnavailable,
                        gemini_error_message, gemini_generation_config,
                        network_error_message, request_api)


CHECK_PROMPT = "Reply briefly with OK."
CHECK_OUTPUT_TOKENS = 16


def _text_reply(response, provider):
    """Extract transport text only; its meaning and format are irrelevant."""
    try:
        data = response.json()
        if provider == "openai":
            text = data["choices"][0]["message"]["content"]
        else:
            text = "".join(part.get("text", "")
                for part in data["candidates"][0]["content"]["parts"]
                if not part.get("thought"))
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty text response")
        return text
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ProviderUnavailable("API не вернул текст ответа модели. Проверьте доступность выбранной модели.") from None


async def check_connection(state, provider, model, key, base):
    """Use production transports, cancellation and timing without task parsing."""
    current = asyncio.current_task()
    state.inference_tasks.add(current)
    statistics = state.performance.begin(provider, model, "check")
    trace = state.dialogue.begin(provider, model, "check", key)
    status, result = "error", "Проверка подключения не завершена."
    try:
        client = state.client_for(provider)
        messages = [{"role": "user", "content": CHECK_PROMPT}]
        if provider == "ollama":
            # Metadata, local-only validation, model switching and think=false
            # remain in the shared adapter. No format/schema is requested.
            text = await state.ollama_session.generate(client, base, model,
                messages, None, statistics, trace=trace, num_predict=CHECK_OUTPUT_TOKENS,
                num_ctx=1024)
            checks = ["Модель доступна: OK"]
        elif provider == "openai":
            payload = {"model": model, "messages": messages,
                       "max_completion_tokens": CHECK_OUTPUT_TOKENS}
            if model.startswith(("gpt-5", "gpt-6", "o3", "o4")):
                payload["reasoning_effort"] = "low"
            trace.set("request", payload)
            response = await request_api(client, "POST", "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + key}, json=payload)
            text = _text_reply(response, provider)
            checks = ["Модель доступна: OK"]
        elif provider == "gemini":
            url = "https://generativelanguage.googleapis.com/v1beta/models/" + model
            metadata = await request_api(client, "GET", url, headers={"x-goog-api-key": key})
            try:
                card = metadata.json()
                if "generateContent" not in card.get("supportedGenerationMethods", []):
                    raise ProviderUnavailable("Выбранная модель не поддерживает generateContent.")
            except (ValueError, AttributeError, TypeError):
                raise ProviderUnavailable("API вернул некорректную карточку модели.") from None
            config = gemini_generation_config(model)
            config["maxOutputTokens"] = CHECK_OUTPUT_TOKENS
            payload = {"contents": [{"role": "user", "parts": [{"text": CHECK_PROMPT}]}],
                       "generationConfig": config}
            trace.set("request", payload)
            response = await request_api(client, "POST", url + ":generateContent",
                headers={"x-goog-api-key": key}, json=payload)
            text = _text_reply(response, provider)
            checks = ["Карточка модели: OK"]
        else:
            raise ProviderError("Неизвестный провайдер.")
        # A textual answer such as {"tasks":[]} still proves availability.
        # Never inspect JSON fields, require OK, or retry it as a broken plan.
        if not isinstance(text, str) or not text.strip():
            raise ProviderUnavailable("Модель не вернула текст ответа.")
        trace.set("response", text)
        status, result = "ok", "Подключение и доступность модели подтверждены. Составление игровых задач не проверялось."
        return [*checks, "Ответ модели: OK"]
    except asyncio.CancelledError:
        status, result = "cancelled", "Проверка подключения отменена. Игровые задачи не изменены."
        raise
    except ProviderError as exc:
        result = str(exc)
        if provider == "gemini" and isinstance(exc, ProviderModelNotFound):
            exc.args = (gemini_error_message(exc),)
            result = str(exc)
        if exc.diagnostic:
            result += "\nОтвет API: " + exc.diagnostic
        raise
    except (httpx.RequestError, TimeoutError) as exc:
        result = network_error_message(exc)
        raise ProviderUnavailable(result) from None
    finally:
        trace.finish(status, result)
        state.inference_tasks.discard(current)
        state.performance.finish(statistics, status)
