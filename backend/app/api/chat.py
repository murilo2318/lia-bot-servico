import json
from dataclasses import asdict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.orchestrator import Orchestrator
from app.deps import get_orchestrator, require_api_key
from app.schemas import ChatIn, ChatOut, ErrorOut

router = APIRouter(tags=["chat"], dependencies=[Depends(require_api_key)])

ERROS = {404: {"model": ErrorOut, "description": "session_id inexistente"},
         503: {"model": ErrorOut, "description": "Modelo de linguagem indisponível"}}


@router.post("/chat", response_model=ChatOut, responses=ERROS, summary="Envia uma mensagem à Lia",
             description="Recebe {session_id, message} e devolve a resposta com o raio-X do turno: "
                         "intenção, slots, sentimento, fallback, handoff, rota, guardrail e latência.")
def chat(body: ChatIn, orch: Orchestrator = Depends(get_orchestrator)):
    return ChatOut(**asdict(orch.processar(body.session_id, body.message)))


@router.post("/chat/stream", responses=ERROS, summary="Mesma conversa, com a resposta em streaming (SSE)",
             description="Eventos: `token` (pedaços da resposta) e `done` (raio-X completo, igual ao /chat). "
                         "A resposta é validada pelo guardrail de saída ANTES de começar a ser enviada.")
def chat_stream(body: ChatIn, orch: Orchestrator = Depends(get_orchestrator)):
    resultado = ChatOut(**asdict(orch.processar(body.session_id, body.message)))

    def eventos():
        palavras = resultado.reply.split(" ")
        for i in range(0, len(palavras), 3):
            pedaco = " ".join(palavras[i:i + 3]) + (" " if i + 3 < len(palavras) else "")
            yield f"event: token\ndata: {json.dumps(pedaco, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {resultado.model_dump_json()}\n\n"

    return StreamingResponse(eventos(), media_type="text/event-stream")
