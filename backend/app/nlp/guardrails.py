"""Guardrails em código — não dependem de o LLM obedecer ao prompt.

Entrada: tamanho, tentativa de prompt injection e pedido para fazer a
atividade pelo aluno. Saída: vazamento do prompt, promessas indevidas
(nota, prazo, contato) e tamanho máximo.
"""
import re
from dataclasses import dataclass
from functools import lru_cache

from app.nlp.text import normalize_text

# --- entrada -----------------------------------------------------------
INJECTION = [
    r"\b(ignor\w*|esquec\w*|desconsider\w*|descart\w*)\b.{0,40}\b(instruc\w*|regras?|prompt|orientac\w*|diretriz\w*)",
    r"\b(mostr\w*|revel\w*|repit\w*|exib\w*|imprim\w*|copi\w*|diga|conte|passa\w*|qual e)\b.{0,30}\b(seu|teu|suas|tuas)\s+(proprio\s+|proprias\s+)?(system prompt|prompt|instruc\w*|regras)",
    r"\b(voce agora e|a partir de agora voce|finja que|faca de conta que|aja como|atue como|roleplay)\b",
    r"\b(modo (desenvolvedor|dev|admin|deus)|jailbreak|dan mode|sem restric\w*|sem filtro)\b",
    r"\b(ignore (all|previous)|developer mode)\b",
]
PEDIDO_INDEVIDO = [
    r"\b(faca|faz|resolve|resolva|escreve|escreva|monta|monte|entrega|termina|termine)\b.{0,30}\b(meu|minha|o|a|nosso|nossa)?\s*(trabalho|atividade|checkpoint|cp|prova|exercicio|tarefa|entrega|codigo do (cp|trabalho))\b",
    r"\b(me )?(passa|manda|da|de)\b.{0,15}\b(as )?respostas?\b.{0,20}\b(prova|atividade|checkpoint|exercicio)",
    r"\b(cola|gabarito)\b",
    # achados nas conversas manuais (Eduarda, 02/10)
    r"\b(escreve|escreva|faz|faca|cria|crie|monta|monte|gera|gere|programa|desenvolve|redige)\b.{0,50}\b(pra mim|para mim|por mim)\b",
    r"\b(escreve|escreva|faz|faca|cria|crie|monta|monte|gera|gere|programa|desenvolve)\b.{0,30}\b(do|da|de|o|a)?\s*(meu|minha|nosso|nossa)\b.{0,20}\b(bot|chatbot|prompt|system prompt|codigo|projeto|fluxo|ficha|readme)\b",
    r"\b(me passa|me manda|me da|manda|passa)\b.{0,25}\b(pronto|prontinho|feito|completo|inteiro)\b",
]


@dataclass
class GuardrailResult:
    bloqueado: bool
    tipo: str | None = None   # tamanho | prompt_injection | pedido_indevido | saida_*
    motivo: str | None = None


def checar_entrada(texto: str, max_chars: int) -> GuardrailResult:
    if not texto.strip():
        return GuardrailResult(True, "vazio", "mensagem vazia")
    if len(texto) > max_chars:
        return GuardrailResult(True, "tamanho", f"{len(texto)} caracteres (> {max_chars})")
    t = normalize_text(texto)
    for padrao in INJECTION:
        if re.search(padrao, t):
            return GuardrailResult(True, "prompt_injection", padrao[:40])
    for padrao in PEDIDO_INDEVIDO:
        if re.search(padrao, t):
            return GuardrailResult(True, "pedido_indevido", padrao[:40])
    return GuardrailResult(False)


# --- saída -------------------------------------------------------------
# Rótulos do bloco de contexto que o orquestrador injeta (nunca devem aparecer na resposta).
VAZAMENTO = [
    r"contexto da faq", r"pergunta da faq", r"resposta da faq", r"objetivo de aprendizagem nao definido",
    r"\btom (acolhimento|normal)\b", r"system prompt v\d", r"voce e a lia assistente virtual da oficina",
]
JANELA_VAZAMENTO = 9   # palavras seguidas iguais ao system prompt = vazamento


@lru_cache
def _trechos_do_prompt() -> frozenset[str]:
    """Sequências de 9 palavras dos system prompts (sem a seção de exemplos, que ensina o tom da resposta).

    Achado em 02/10 (Eduarda): padrões genéricos como "regras e guardrails" bloqueavam explicações legítimas
    sobre system prompt. Comparar com trechos LITERAIS do prompt real separa vazamento de explicação.
    """
    from app.config import BASE_DIR
    trechos = set()
    for arquivo in (BASE_DIR / "prompts").glob("*.md"):
        texto = arquivo.read_text(encoding="utf-8").split("## 5. Exemplos")[0]
        palavras = normalize_text(texto).split()
        trechos.update(" ".join(palavras[i:i + JANELA_VAZAMENTO]) for i in range(len(palavras) - JANELA_VAZAMENTO + 1))
    return frozenset(trechos)
PROMESSA = [
    r"\b(garanto|prometo|vou garantir)\b",
    r"\b(vou|irei|posso)\b.{0,20}\b(te dar|aumentar|mudar|alterar|revisar)\b.{0,15}\bnota\b",
    r"\bprazo\b.{0,30}\b(prorrogad\w*|estendid\w*|adiad\w*)\b",
    r"\b(vou|irei|ja)\b.{0,15}\b(contatar|chamar|avisar|mandar mensagem|enviar e mail)\b.{0,20}\b(professor|professora)\b",
    r"\bvoce (esta|foi|vai ser) aprovad[oa]\b",
]


def checar_saida(texto: str, max_chars: int) -> tuple[str, GuardrailResult]:
    """Devolve o texto final e o resultado do guardrail de saída."""
    t = normalize_text(texto)
    for padrao in VAZAMENTO:
        if re.search(padrao, t):
            return "", GuardrailResult(True, "saida_vazamento", padrao)
    palavras = t.split()
    trechos = _trechos_do_prompt()
    for i in range(len(palavras) - JANELA_VAZAMENTO + 1):
        if " ".join(palavras[i:i + JANELA_VAZAMENTO]) in trechos:
            return "", GuardrailResult(True, "saida_vazamento", "trecho literal do system prompt")
    for padrao in PROMESSA:
        if re.search(padrao, t):
            return "", GuardrailResult(True, "saida_promessa", padrao)
    if len(texto) > max_chars:
        return _cortar_em_frase(texto, max_chars), GuardrailResult(False, "saida_tamanho", f"cortado de {len(texto)}")
    return texto, GuardrailResult(False)


def _cortar_em_frase(texto: str, limite: int) -> str:
    trecho = texto[:limite]
    fim = max(trecho.rfind(". "), trecho.rfind("! "), trecho.rfind("? "))
    return trecho[: fim + 1] if fim > limite // 3 else trecho.rstrip() + "…"
