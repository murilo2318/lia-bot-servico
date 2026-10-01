import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

API_KEY = "chave-de-teste"
H = {"X-API-Key": API_KEY}


def make_client(tmp_path, **overrides) -> TestClient:
    # Isolamento total do .env do desenvolvedor: sem chaves reais, sem reserva, sem internet.
    cfg = dict(api_key=API_KEY, llm_provider="mock", db_path=str(tmp_path / "t.db"), _env_file=None,
               gemini_api_key="", groq_api_key="", llm_fallback_provider="", llm_retries=0,
               prompt_version="v2", history_window_turns=6, summary_enabled=True, nlu_llm_fallback=True,
               max_input_chars=500, max_output_chars=700)
    cfg.update(overrides)
    return TestClient(create_app(Settings(**cfg)))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


class Conversa:
    def __init__(self, client, **body):
        self.c = client
        r = client.post("/sessions", json=body, headers=H)
        assert r.status_code == 201
        self.sid = r.json()["session_id"]
        self.greeting = r.json()["greeting"]

    def diz(self, msg: str) -> dict:
        r = self.c.post("/chat", json={"session_id": self.sid, "message": msg}, headers=H)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def conversa(client):
    return lambda **body: Conversa(client, **body)
