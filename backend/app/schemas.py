"""Contrato da API: modelos Pydantic de entrada e saída."""
from typing import Literal

from pydantic import BaseModel, Field

LearningGoal = Literal["design", "system-prompt", "memory-state", "guardrail", "faq"]


class HealthOut(BaseModel):
    status: Literal["ok"]
    provider: str
    model: str
    prompt_version: str
    history_window_turns: int
    db: str


class SessionCreate(BaseModel):
    objetivo: LearningGoal | None = Field(None, description="Objetivo de aprendizagem escolhido na tela inicial")
    prompt_version: Literal["v1", "v2"] | None = Field(None, description="Opcional (teste A/B). Padrão: .env")
    provider: Literal["gemini", "groq", "mock"] | None = Field(None, description="Opcional (teste A/B). Padrão: .env")


class SessionCreated(BaseModel):
    session_id: str
    greeting: str = Field(..., description="Saudação que declara as capacidades da Lia")
    prompt_version: str
    provider: str
    model: str


class ChatIn(BaseModel):
    session_id: str = Field(..., min_length=4, max_length=64, examples=["8f1c2a9b3d4e"])
    message: str = Field(..., max_length=4000, examples=["Quero agendar um plantão de dúvidas"],
                         description="Até 4000 caracteres pela validação; acima de 500 a Lia pede para resumir")


class Sentiment(BaseModel):
    label: Literal["negativo", "neutro", "positivo"]
    score: float


class Handoff(BaseModel):
    active: bool
    reason: str | None = None
    summary: dict | None = None


class Guardrail(BaseModel):
    tipo: str | None = None
    motivo: str | None = None


class ChatOut(BaseModel):
    session_id: str
    reply: str
    intent: str
    route: str = Field(..., description="faq | fluxo | ferramenta | regra | fallback | handoff | guardrail")
    slots: dict
    sentiment: Sentiment
    fallback: bool
    handoff: Handoff
    turn: int
    latency_ms: int
    faq_id: str | None = None
    used_memory: bool = False
    guardrail: Guardrail | None = None
    etapa: str | None = Field(None, description="Etapa do fluxo de agendamento, se houver")
    nlu_origem: str = Field("regra", description="regra | llm | memoria")
    texto_interpretado: str | None = Field(None, description="Como a mensagem foi entendida, quando houve "
                                                         "correção de digitação ou interpretação pelo LLM")
    model: str


class Message(BaseModel):
    role: str
    content: str
    created_at: str


class SessionOut(BaseModel):
    session_id: str
    created_at: str
    prompt_version: str
    provider: str
    model: str
    turn: int
    slots: dict
    etapa: str | None
    resumo: str
    handoff: Handoff
    handoff_status: str | None
    history: list[Message]


class FeedbackIn(BaseModel):
    session_id: str
    score: int = Field(..., ge=1, le=5, description="CSAT de 1 a 5")
    comment: str | None = Field(None, max_length=500)


class FeedbackOut(BaseModel):
    session_id: str
    score: int
    saved: bool = True


class HandoffItem(BaseModel):
    session_id: str
    status: str
    motivo: str
    urgencia: str
    criado_em: str
    resumo: dict


class HandoffUpdate(BaseModel):
    status: Literal["pendente", "em_atendimento", "resolvido"]


class ErrorOut(BaseModel):
    detail: str
