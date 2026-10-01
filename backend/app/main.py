"""Lia como serviço — cria a FastAPI e registra as rotas.

Executar:  uvicorn app.main:app --port 8000   (documentação em /docs)
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import chat, feedback, handoffs, health, metrics, sessions
from app.config import Settings, get_settings
from app.core.orchestrator import Orchestrator, SessionNotFound
from app.llm.client import LLMUnavailable
from app.memory.store import Store

log = logging.getLogger("lia")

DESCRICAO = """
**Lia** é a assistente da oficina de chatbots, agora como serviço: toda a inteligência
conversacional (memória, estado, regra × LLM, guardrails, handoff e métricas) mora aqui.
Qualquer tela — Streamlit, Gradio, app ou WhatsApp — é só uma *lente* que consome esta API.

**Autenticação:** envie o header `X-API-Key` (valor de `API_KEY` no `.env`). Clique em
*Authorize* para testar por esta página. `/health` é público.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(settings.db_path)
        app.state.orchestrator = Orchestrator(settings, app.state.store)
        yield

    app = FastAPI(title="Lia — Bot como Serviço", version="2.0.0", description=DESCRICAO, lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: settings
    for router in (health.router, sessions.router, chat.router, metrics.router, feedback.router, handoffs.router):
        app.include_router(router)

    @app.exception_handler(SessionNotFound)
    async def _404(request: Request, exc: SessionNotFound):
        return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada. Inicie uma nova conversa."})

    @app.exception_handler(LLMUnavailable)
    async def _503(request: Request, exc: LLMUnavailable):
        log.warning("LLM indisponível: %s", exc)
        return JSONResponse(status_code=503, content={
            "detail": "O modelo de linguagem está indisponível agora. Tente novamente em instantes."})

    @app.exception_handler(Exception)
    async def _500(request: Request, exc: Exception):
        log.exception("erro inesperado")
        return JSONResponse(status_code=500, content={"detail": "Erro interno. Tente novamente."})

    return app


app = create_app()
