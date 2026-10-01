from fastapi import APIRouter, Depends, Query

from app.analytics.metrics import calcular_metricas
from app.deps import get_store, require_api_key
from app.memory.store import Store

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_api_key)])


@router.get("/metrics", summary="Métricas conversacionais calculadas a partir do log",
            description="Contenção, fallback, handoff e mensagens por conversa, além de CSAT cruzado com "
                        "contenção, FAQ-hit, eventos de guardrail, latência e tokens. Os filtros permitem "
                        "comparar variantes no teste A/B.")
def metrics(prompt_version: str | None = Query(None, description="v1 ou v2"),
            provider: str | None = Query(None, description="gemini, groq ou mock"),
            store: Store = Depends(get_store)):
    return calcular_metricas(store, prompt_version, provider)
