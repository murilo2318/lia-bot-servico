from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_store, require_api_key
from app.memory.store import Store
from app.schemas import ErrorOut, HandoffItem, HandoffUpdate

router = APIRouter(prefix="/handoffs", tags=["handoffs"], dependencies=[Depends(require_api_key)])


def _item(s) -> HandoffItem:
    resumo = s.handoff["resumo"]
    return HandoffItem(session_id=s.id, status=s.handoff_status, motivo=s.handoff["motivo"],
                       urgencia=resumo["urgencia"], criado_em=s.handoff["criado_em"], resumo=resumo)


@router.get("", response_model=list[HandoffItem], summary="Fila de conversas transferidas ao professor",
            description="Cada item traz o resumo estruturado: motivo, urgência, dados coletados, relato, "
                        "sentimento e ações já tomadas pela Lia. Urgentes primeiro.")
def listar(status: str | None = Query(None, description="pendente, em_atendimento ou resolvido"),
           store: Store = Depends(get_store)):
    itens = [_item(s) for s in store.list_handoffs(status)]
    return sorted(itens, key=lambda i: (i.urgencia != "alta", i.status == "resolvido"))


@router.patch("/{session_id}", response_model=HandoffItem, responses={404: {"model": ErrorOut}},
              summary="Atualiza o status de um handoff (painel do professor)",
              description="Quando o status vira 'resolvido', a conversa volta a ser atendida pela Lia.")
def atualizar(session_id: str, body: HandoffUpdate, store: Store = Depends(get_store)):
    s = store.get_session(session_id)
    if not s or not s.handoff:
        raise HTTPException(404, "Handoff não encontrado para esta sessão.")
    s.handoff_status = body.status
    store.save_state(s)
    return _item(s)
