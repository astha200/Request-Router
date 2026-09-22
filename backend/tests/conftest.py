import os
import tempfile

import pytest

# Point the store at a throwaway DB and provide a fake key BEFORE app import.
os.environ.setdefault("FIREWORKS_API_KEY", "fw_test_key_not_real")
_TMP_DB = tempfile.mktemp(suffix=".db")
os.environ["ROUTER_DB_PATH"] = _TMP_DB


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, fireworks, store
    from app.main import app

    monkeypatch.setattr(config, "FIREWORKS_API_KEY", "fw_test_key_not_real")
    store.init_db()

    calls: list[dict] = []

    async def fake_completion(payload):
        calls.append(payload)
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        }

    monkeypatch.setattr(fireworks, "chat_completion", fake_completion)
    with TestClient(app) as c:
        c.upstream_calls = calls
        yield c
