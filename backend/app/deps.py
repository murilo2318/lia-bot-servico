"""Dependências injetadas nas rotas (store, orquestrador, autenticação)."""
import secrets

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings
from app.core.orchestrator import Orchestrator
from app.memory.store import Store

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False,
                              description="Chave definida em API_KEY no .env do backend")


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def require_api_key(key: str | None = Security(api_key_header),
                    settings: Settings = Depends(get_settings)) -> None:
    if not key or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chave de API ausente ou inválida (header X-API-Key).")
