from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.deps import get_store
from app.llm.client import LLMClient
from app.memory.store import Store
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, summary="Estado da API e do modelo em uso",
            description="Rota pública (sem chave). Mostra o provedor e o modelo configurados no .env.")
def health(settings: Settings = Depends(get_settings), store: Store = Depends(get_store)):
    client = LLMClient(settings)
    store.query("SELECT 1")
    return HealthOut(status="ok", provider=client.provider, model=client.model,
                     prompt_version=settings.prompt_version,
                     history_window_turns=settings.history_window_turns, db="sqlite ok")
