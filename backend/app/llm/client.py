"""Único ponto de acesso ao modelo de linguagem.

Gemini e Groq expõem endpoints compatíveis com a API da OpenAI, então um
mesmo cliente HTTP atende os dois. Trocar de provedor = mudar LLM_PROVIDER
no .env; nenhuma linha do bot muda. O provedor "mock" roda sem chave e sem
rede (testes automatizados e execução sem custo).
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from app.config import Settings

log = logging.getLogger("lia.llm")

ENDPOINTS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
}


class LLMUnavailable(Exception):
    """Provedor fora do ar, sem chave, com timeout ou limite de uso estourado."""

    def __init__(self, motivo: str, transitorio: bool = False, espera_s: float | None = None):
        super().__init__(motivo)
        self.transitorio = transitorio   # 503/429/timeout: vale tentar de novo
        self.espera_s = espera_s         # quanto o provedor pediu para esperar (429)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = "call_0"
    raw: dict | None = None      # chamada original (o Gemini exige a thought_signature de volta)


@dataclass
class LLMResponse:
    text: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    reserva: bool = False        # respondida pelo provedor reserva


class LLMClient:
    """Cliente com nova tentativa e provedor reserva.

    Fluxo de uma chamada: provedor principal → (503/429/timeout) espera e tenta
    de novo → se ainda falhar e houver LLM_FALLBACK_PROVIDER com chave, tenta o
    reserva → se tudo falhar, LLMUnavailable (o orquestrador degrada ou responde 503).
    """

    def __init__(self, settings: Settings, provider: str | None = None, model: str | None = None):
        self.settings = settings
        self.provider = (provider or settings.llm_provider).lower()
        self.model, self.api_key = self._credenciais(self.provider)
        if model:
            self.model = model
        reserva = (settings.llm_fallback_provider or "").lower()
        self.reserva = reserva if reserva and reserva != self.provider and self._credenciais(reserva)[1] else None

    def _credenciais(self, provider: str) -> tuple[str, str]:
        s = self.settings
        if provider == "gemini":
            return s.gemini_model, s.gemini_api_key
        if provider == "groq":
            return s.groq_model, s.groq_api_key
        if provider in {"mock", "mock_down"}:
            return "mock-lia", "mock"
        raise ValueError(f"LLM_PROVIDER inválido: {provider}")

    # ------------------------------------------------------------------
    def chat(self, messages: list[dict], *, temperature: float | None = None, json_mode: bool = False,
             tools: list[dict] | None = None, purpose: str = "answer") -> LLMResponse:
        inicio = time.perf_counter()
        try:
            resp = self._com_tentativas(self.provider, messages, temperature, json_mode, tools, purpose)
        except LLMUnavailable as exc:
            if not self.reserva:
                raise
            log.warning("LLM %s indisponível (%s); usando o reserva %s", self.provider, exc, self.reserva)
            resp = self._com_tentativas(self.reserva, messages, temperature, json_mode, tools, purpose)
            resp.reserva = True
        resp.latency_ms = int((time.perf_counter() - inicio) * 1000)
        return resp

    def _com_tentativas(self, provider, messages, temperature, json_mode, tools, purpose) -> LLMResponse:
        if provider == "mock_down":
            raise LLMUnavailable("provedor simulado fora do ar", transitorio=True)
        if provider == "mock":
            return _mock(messages, tools, purpose)
        tentativas = 1 + max(0, self.settings.llm_retries)
        for n in range(tentativas):
            try:
                return self._http(provider, messages, temperature, json_mode, tools)
            except LLMUnavailable as exc:
                if not exc.transitorio or n == tentativas - 1:
                    raise
                espera = self.settings.llm_retry_wait_s * (n + 1)
                if exc.espera_s:                       # o provedor disse quanto esperar: obedece (até 60 s)
                    espera = min(max(espera, exc.espera_s + 0.5), 60.0)
                log.warning("LLM %s: %s; nova tentativa em %.1fs", provider, exc, espera)
                time.sleep(espera)
        raise LLMUnavailable("sem tentativas")  # inalcançável

    def _http(self, provider, messages, temperature, json_mode, tools) -> LLMResponse:
        model, api_key = self._credenciais(provider)
        if provider == self.provider:
            model = self.model            # respeita um modelo escolhido na criação (ex.: juiz)
        if not api_key:
            raise LLMUnavailable(f"chave do provedor {provider} ausente no .env")
        payload: dict = {
            "model": model,
            "messages": messages if provider == "gemini" else _sem_extras_gemini(messages),
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
        }
        # O endpoint compatível do Gemini com modelos de raciocínio não é confiável com
        # response_format; o parser do NLU/juiz já extrai o JSON do texto.
        if json_mode and provider != "gemini":
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"], payload["tool_choice"] = tools, "auto"
        try:
            r = httpx.post(ENDPOINTS[provider], json=payload, timeout=self.settings.llm_timeout_s,
                           headers={"Authorization": f"Bearer {api_key}"})
        except httpx.TimeoutException as exc:
            log.warning("LLM %s/%s: timeout", provider, model)
            raise LLMUnavailable("timeout", transitorio=True) from exc
        except httpx.HTTPError as exc:
            log.warning("LLM %s/%s: falha de rede %s", provider, model, type(exc).__name__)
            raise LLMUnavailable(f"falha de rede: {type(exc).__name__}", transitorio=True) from exc
        if r.status_code >= 400:
            log.warning("LLM %s/%s: HTTP %s %s", provider, model, r.status_code, _mensagem_erro(r))
            raise LLMUnavailable(f"provedor respondeu {r.status_code}",
                                 transitorio=r.status_code in {429, 500, 502, 503, 504},
                                 espera_s=_espera_pedida(r))
        data = r.json()
        msg = data["choices"][0]["message"]
        calls = [
            ToolCall(c["function"]["name"], json.loads(c["function"].get("arguments") or "{}"),
                     c.get("id", "call_0"), raw=c)
            for c in (msg.get("tool_calls") or [])
        ]
        usage = data.get("usage") or {}
        return LLMResponse(msg.get("content") or "", data.get("model", model), calls,
                           usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


def _espera_pedida(r: httpx.Response) -> float | None:
    """Segundos que o provedor pediu para esperar (header Retry-After ou texto 'try again in 12.3s')."""
    try:
        if r.headers.get("retry-after"):
            return float(r.headers["retry-after"])
    except ValueError:
        pass
    m = re.search(r"(?:try again|retry) in ([\d.]+)\s*(ms|s)", r.text)
    if m:
        return float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
    return None


def _mensagem_erro(r: httpx.Response) -> str:
    try:
        corpo = r.json()
        corpo = corpo[0] if isinstance(corpo, list) else corpo
        return str(corpo.get("error", {}).get("message", corpo))[:200]
    except ValueError:
        return r.text[:200]


def _sem_extras_gemini(messages: list[dict]) -> list[dict]:
    """Remove campos próprios do Gemini (thought_signature) antes de enviar a outro provedor."""
    limpas = []
    for m in messages:
        if m.get("tool_calls"):
            m = {**m, "tool_calls": [{k: v for k, v in c.items() if k != "extra_content"} for c in m["tool_calls"]]}
        limpas.append(m)
    return limpas


# ----------------------------------------------------------------------
# Provedor simulado: determinístico, sem rede. Imita o comportamento
# esperado do modelo para que o fluxo completo rode sem chave.
DOMINIO = r"\b(bot|chatbot|llm|prompt|modelo|gemini|botpress|api|deploy|whatsapp|token|rag|embedding|fine tuning|oficina|streamlit|fastapi|agente)\b"
OFF = r"\b(receita|bolo|futebol|jogo|filme|politic\w*|eleic\w*|clima|tempo amanha|piada)\b"


def _mock(messages: list[dict], tools, purpose: str) -> LLMResponse:
    from app.nlp.text import normalize_text

    ultima = normalize_text(messages[-1]["content"])
    if purpose == "nlu":
        alvo = ultima.split("mensagem ", 1)[-1]
        if re.search(OFF, alvo):
            intent = "fora_escopo"
        elif re.search(DOMINIO, alvo):
            intent = "fora_da_base"
        else:
            intent = "nao_entendi"
        return LLMResponse(json.dumps({"intent": intent, "faq_id": None, "confianca": 0.8}), "mock-lia")
    if purpose == "summary":
        return LLMResponse("Resumo: " + messages[-1]["content"][-300:], "mock-lia")
    if purpose == "judge":
        nota_fid = 3 if "guardrails sao limites em codigo" in ultima else 5
        return LLMResponse(json.dumps({"justificativa": "avaliação simulada", "pior_trecho": "",
                                       "relevancia": 4, "aderencia_ao_papel": 5, "retencao_de_contexto": 4,
                                       "clareza": 4, "fidelidade_a_base": nota_fid}), "mock-lia")
    if tools and not any(m.get("role") == "tool" for m in messages):
        dia = next((d for d in ["segunda", "terca", "quarta", "quinta", "sexta"] if d in ultima), None)
        return LLMResponse("", "mock-lia", [ToolCall("consultar_agenda", {"dia": dia})])
    if any(m.get("role") == "tool" for m in messages):
        dados = json.loads(next(m for m in reversed(messages) if m.get("role") == "tool")["content"])
        rotulos = [h["rotulo"] for h in dados.get("horarios", [])]
        texto = ("Tenho estes horários livres: " + ", ".join(rotulos) + ". Quer agendar um deles?") if rotulos \
            else "Não encontrei horário livre nesse dia. Quer ver os outros dias?"
        return LLMResponse(texto, "mock-lia")
    system = messages[0]["content"]
    m = re.search(r"Resposta da FAQ: (.+)", system)
    base = m.group(1).strip() if m else "Não tenho essa informação na base da oficina."
    acolhe = "Entendo que esse ponto pode confundir. " if "TOM: acolhimento" in system else ""
    return LLMResponse(acolhe + base, "mock-lia", prompt_tokens=len(system) // 4, completion_tokens=len(base) // 4)
