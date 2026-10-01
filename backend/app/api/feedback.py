from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_store, require_api_key
from app.memory.store import Store
from app.schemas import ErrorOut, FeedbackIn, FeedbackOut

router = APIRouter(tags=["feedback"], dependencies=[Depends(require_api_key)])


@router.post("/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED,
             responses={404: {"model": ErrorOut}}, summary="Nota de 1 a 5 da conversa (CSAT)",
             description="Uma nota por sessão; enviar de novo substitui a anterior.")
def feedback(body: FeedbackIn, store: Store = Depends(get_store)):
    if not store.get_session(body.session_id):
        raise HTTPException(404, "Sessão não encontrada.")
    store.save_feedback(body.session_id, body.score, body.comment)
    return FeedbackOut(session_id=body.session_id, score=body.score)
