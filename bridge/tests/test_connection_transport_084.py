"""Availability checks extract transport text without evaluating an NPC plan."""
import httpx
import pytest

from valheim_ai.model_check import _text_reply
from valheim_ai.providers import ProviderUnavailable


@pytest.mark.parametrize("provider,payload", [
    ("openai", {"choices":[{"message":{"content":"{\"tasks\":[]}"}}]}),
    ("gemini", {"candidates":[{"content":{"parts":[{"text":"{\"tasks\":[]}"}]}}]}),
])
def test_arbitrary_nonempty_text_proves_transport_not_task_semantics(provider, payload):
    assert _text_reply(httpx.Response(200,json=payload), provider) == '{"tasks":[]}'


@pytest.mark.parametrize("provider,payload", [
    ("openai", {"choices":[]}),
    ("openai", {"choices":[{"message":{}}]}),
    ("openai", {"choices":[{"message":{"content":"  "}}]}),
    ("openai", {"choices":[{"message":{"content":None}}]}),
    ("gemini", {"candidates":[]}),
    ("gemini", {"candidates":[{"content":{"parts":[{"text":"hidden", "thought":True}]}}]}),
    ("gemini", {"candidates":[{"content":{"parts":[{"text":None}]}}]}),
])
def test_missing_or_empty_transport_text_reports_availability_error(provider, payload):
    with pytest.raises(ProviderUnavailable, match="API не вернул текст"):
        _text_reply(httpx.Response(200,json=payload), provider)


def test_gemini_hidden_reasoning_never_becomes_the_connection_reply():
    response = httpx.Response(200,json={"candidates":[{"content":{"parts":[
        {"text":"internal reasoning", "thought":True}, {"text":"Доступна"}
    ]}}]})
    assert _text_reply(response, "gemini") == "Доступна"
