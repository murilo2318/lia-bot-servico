"""Cliente da API da Lia — o ÚNICO ponto de contato do front com o cérebro.

O front não importa nada do backend e não chama o LLM: só fala HTTP.
Cada falha vira um ApiError com uma mensagem pronta para a tela.
"""
import json
import os
from collections.abc import Iterator
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
TIMEOUT = float(os.getenv("API_TIMEOUT_S", "40"))

MENSAGENS = {
    "offline": f"Não consegui falar com a API em {API_URL}. Confira se o backend está rodando (uvicorn app.main:app --port 8000).",
    401: "A API recusou a chave. Confira se API_KEY é igual no .env do frontend e do backend.",
    404: "Essa conversa não existe mais no servidor. Comece uma nova conversa.",
    422: "A API não aceitou os dados enviados. Revise a mensagem e tente de novo.",
    503: "O modelo de linguagem está indisponível agora. Aguarde alguns segundos e envie de novo.",
    500: "A API teve um erro interno. Tente de novo; se continuar, reinicie o backend.",
    "timeout": "A API demorou demais para responder. Tente de novo em instantes.",
}


class ApiError(Exception):
    def __init__(self, tipo, detalhe: str | None = None):
        self.tipo = tipo
        self.mensagem = MENSAGENS.get(tipo, MENSAGENS[500])
        self.detalhe = detalhe
        super().__init__(self.mensagem)


def _headers() -> dict:
    return {"X-API-Key": API_KEY}


def _req(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=_headers(), timeout=TIMEOUT, **kw)
    except httpx.TimeoutException as exc:
        raise ApiError("timeout") from exc
    except httpx.HTTPError as exc:
        raise ApiError("offline") from exc
    if r.status_code >= 400:
        detalhe = r.json().get("detail") if "json" in r.headers.get("content-type", "") else r.text
        raise ApiError(r.status_code if r.status_code in MENSAGENS else 500, str(detalhe))
    return r.json() if r.content else None


def health() -> dict:
    return _req("GET", "/health")


def criar_sessao(objetivo: str | None) -> dict:
    return _req("POST", "/sessions", json={"objetivo": objetivo})


def obter_sessao(session_id: str) -> dict:
    return _req("GET", f"/sessions/{session_id}")


def apagar_sessao(session_id: str) -> None:
    _req("DELETE", f"/sessions/{session_id}")


def enviar(session_id: str, mensagem: str) -> dict:
    return _req("POST", "/chat", json={"session_id": session_id, "message": mensagem})


def enviar_stream(session_id: str, mensagem: str, destino: dict) -> Iterator[str]:
    """Gera os pedaços da resposta (SSE). O raio-X final é gravado em destino['turno']."""
    try:
        with httpx.stream("POST", f"{API_URL}/chat/stream", headers=_headers(), timeout=TIMEOUT,
                          json={"session_id": session_id, "message": mensagem}) as r:
            if r.status_code >= 400:
                r.read()
                raise ApiError(r.status_code if r.status_code in MENSAGENS else 500, r.text)
            evento = None
            for linha in r.iter_lines():
                if linha.startswith("event: "):
                    evento = linha[7:]
                elif linha.startswith("data: "):
                    dado = json.loads(linha[6:])
                    if evento == "token":
                        yield dado
                    elif evento == "done":
                        destino["turno"] = dado
    except httpx.TimeoutException as exc:
        raise ApiError("timeout") from exc
    except httpx.HTTPError as exc:
        raise ApiError("offline") from exc


def feedback(session_id: str, nota: int) -> dict:
    return _req("POST", "/feedback", json={"session_id": session_id, "score": nota})


def metricas(prompt_version: str | None = None, provider: str | None = None) -> dict:
    params = {k: v for k, v in {"prompt_version": prompt_version, "provider": provider}.items() if v}
    return _req("GET", "/metrics", params=params)
