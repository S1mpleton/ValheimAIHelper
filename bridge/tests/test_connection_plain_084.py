import json

import httpx
import pytest

from valheim_ai.ollama import generate
from valheim_ai.providers import ProviderError


@pytest.mark.parametrize("reply,reason", [("OK", "stop"), ('{"tasks":[]}', "stop"), ("Есть ответ", "length")])
async def test_plain_probe_checks_only_response_and_never_requests_game_json(reply, reason):
    def handle(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities":["completion"]})
        body = json.loads(request.content)
        assert "format" not in body and body["think"] is False and body["stream"] is False
        assert body["options"]["num_predict"] == 16
        return httpx.Response(200, json={"done":True, "done_reason":reason, "message":{"content":reply}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await generate(client,"http://127.0.0.1:11434","model:local",
            [{"role":"user","content":"Reply briefly."}],None,num_predict=16)
        assert result == reply


async def test_structured_game_generation_still_rejects_truncated_reply():
    def handle(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities":["completion"]})
        assert "format" in json.loads(request.content)
        return httpx.Response(200,json={"done":True,"done_reason":"length","message":{"content":'{"tasks":['}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError,match="не завершила JSON"):
            await generate(client,"http://127.0.0.1:11434","model:local",[],{"type":"object"})
