from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.orchestrator import ATIVO, Orchestrator
from app.deps import get_orchestrator, get_store, require_api_key
from app.memory.store import Store
from app.schemas import ErrorOut, Handoff, SessionCreate, SessionCreated, SessionOut

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=SessionCreated, status_code=status.HTTP_201_CREATED,
             summary="Cria uma sessão",
             description="Devolve o session_id e a saudação da Lia, que declara o que ela faz e o que não faz.")
def criar(body: SessionCreate | None = None, orch: Orchestrator = Depends(get_orchestrator)):
    body = body or SessionCreate()
    s, saudacao = orch.criar_sessao(body.objetivo, body.prompt_version, body.provider)
    return SessionCreated(session_id=s.id, greeting=saudacao, prompt_version=s.prompt_version,
                          provider=s.provider, model=s.model)


@router.get("/{session_id}", response_model=SessionOut, responses={404: {"model": ErrorOut}},
            summary="Histórico, slots e situação da conversa",
            description="Tudo o que o cérebro sabe sobre a sessão: histórico, slots, etapa do fluxo, "
                        "resumo rolante e handoff. É a prova de que o estado vive no backend (T8).")
def obter(session_id: str, store: Store = Depends(get_store)):
    s = store.get_session(session_id)
    if not s:
        raise HTTPException(404, "Sessão não encontrada.")
    return SessionOut(
        session_id=s.id, created_at=s.created_at, prompt_version=s.prompt_version, provider=s.provider,
        model=s.model, turn=s.state.turn, slots=s.state.slots, etapa=s.state.etapa, resumo=s.state.resumo,
        handoff=Handoff(active=s.handoff_status in ATIVO, reason=(s.handoff or {}).get("motivo"),
                        summary=(s.handoff or {}).get("resumo")),
        handoff_status=s.handoff_status, history=store.get_messages(s.id))


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: {"model": ErrorOut}},
               summary="Apaga a sessão (direito ao esquecimento — LGPD)",
               description="Remove histórico, slots, feedback e reservas. O log de analytics mantém só "
                           "os dados agregados, sem o texto das mensagens.")
def apagar(session_id: str, store: Store = Depends(get_store)):
    if not store.delete_session(session_id):
        raise HTTPException(404, "Sessão não encontrada.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
