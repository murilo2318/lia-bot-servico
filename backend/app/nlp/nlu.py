"""NLU híbrida: regras/regex primeiro; LLM (saída JSON) só se nada casar.

Decisão regra × LLM: as intenções críticas (handoff, tema sensível,
agendamento, cancelamento) são sempre detectadas por regra, porque não
podem depender da interpretação do modelo. O LLM só entra para reduzir
fallbacks de perguntas da FAQ escritas de outro jeito.
"""
import json
import re
from dataclasses import dataclass

from app.knowledge.faq import buscar_faq, get_faq, load_faq
from app.nlp.text import expandir_abreviacoes, normalize_text
from app.nlp.variacoes import categoria

INTENCOES = [
    "saudacao", "despedida", "faq", "continuacao", "agendar_plantao", "consultar_agendamento",
    "cancelar", "disponibilidade", "falar_professor", "tema_sensivel",
    "fora_da_base", "fora_escopo", "nao_entendi",
]


@dataclass
class NLUResult:
    intent: str
    faq_id: str | None = None
    score: float = 0.0
    origem: str = "regra"          # regra | llm | memoria
    urgente: bool = False


def _re(padrao: str, t: str) -> bool:
    return re.search(padrao, t) is not None


def pedido_de_humano(t: str) -> bool:
    """Porte de isHandoffRequest (Build Day)."""
    contato = r"(?:falar|conversar|chamar|procurar|contatar|ligar)"
    pessoa = r"(?:professor|professora|humano|humana|pessoa|atendente|alguem)"
    artigo = r"(?:o|a|um|uma)?"
    return _re(rf"\b{contato}\s+(?:com\s+)?{artigo}\s*{pessoa}\b", t) or _re(
        rf"\b(?:quero|preciso|gostaria)\s+(?:de\s+)?(?:falar com\s+)?{artigo}\s*{pessoa}\b", t
    )


def tema_sensivel(t: str) -> tuple[bool, bool]:
    """(é sensível, é urgente). Temas que a Lia nunca tenta resolver sozinha."""
    urgentes = [
        r"\b(assedio|assediad\w*|discrimina\w*|ameac\w*)\b",
        r"\b(crise|ansiedade|panico|depress\w*|hospital|internad\w*|luto|faleceu|morreu)\b",
    ]
    comuns = [
        r"\b(reclam\w*|contest\w*|revis\w*|injust\w*)\b.{0,30}\bnota\b",
        r"\bnota\b.{0,30}\b(injust\w*|errad\w*|baixa|zerad\w*)\b",
        r"\b(reprovad\w*|reprovar|reprovei|dp)\b",
        r"\b(atestado|doente|problema pessoal|problemas pessoais|questao pessoal)\b",
        r"\b(reclamacao|quero reclamar|coordenacao)\b",
    ]
    if any(_re(p, t) for p in urgentes):
        return True, True
    return any(_re(p, t) for p in comuns), False


def continuacao(t: str) -> bool:
    """Porte de isRecognizedContinuation (Build Day), com alguns acréscimos."""
    c = re.sub(r"^e\s+", "", t)
    return c in {"por que", "porque", "como assim", "explica melhor", "pode explicar melhor",
                 "como isso aparece aqui", "como isso aparece neste bot", "da um exemplo",
                 "me da um exemplo", "tem exemplo", "e dai"} or _re(
        r"^(?:isso|isto|aquilo|ele|ela|esse|essa|desse|dessa|nisso|nele|nela)\b", c
    )


def classificar_por_regras(texto: str, ultima_faq: str | None) -> NLUResult:
    t = expandir_abreviacoes(normalize_text(texto))
    if pedido_de_humano(t):
        return NLUResult("falar_professor", score=1.0)
    sensivel, urgente = tema_sensivel(t)
    if sensivel:
        return NLUResult("tema_sensivel", score=1.0, urgente=urgente)
    if _re(r"\b(cancel\w*|desmarc\w*|deixa pra la|desist\w* do (plantao|agendamento))\b", t):
        return NLUResult("cancelar", score=1.0)
    if _re(r"\b(qual|que)\b.{0,20}\b(horario|dia|data)\b.{0,25}\b(ficou|marcad\w*|agendad\w*|reservad\w*|sugeriu|sugerido|voce falou)\b", t) \
            or _re(r"\b(meu|o meu)\s+(agendamento|plantao)\b", t) or _re(r"\baquele horario\b", t):
        return NLUResult("consultar_agendamento", score=1.0)
    if _re(r"\b(agend\w*|marc\w*|reserv\w*)\b.{0,30}\b(plantao|monitoria|horario|atendimento|professor|duvida)", t) \
            or _re(r"\bplantao de duvidas?\b", t) or t in {"agendar", "quero agendar", "agendar plantao"} \
            or _re(r"^(quero|queria|gostaria de|posso|pode|vou|da pra)?\s*(agendar|marcar|reservar)\b", t):
        return NLUResult("agendar_plantao", score=1.0)
    if _re(r"\b(quais|que|tem|ha|existe\w*)\b.{0,25}\b(horarios?|plantao|plantoes|vaga\w*)\b.{0,25}(disponive\w*|livres?|\?|$|segunda|terca|quarta|quinta|sexta)", t):
        return NLUResult("disponibilidade", score=1.0)
    # expressões curtas do arquivo data/variacoes.json (comparação com a mensagem inteira)
    cat = categoria(texto)
    if cat == "despedida":
        return NLUResult("despedida", score=1.0)
    if cat == "recusa":
        return NLUResult("recusa_oferta", score=1.0)
    if cat == "aceite":
        if ultima_faq and get_faq(ultima_faq):
            return NLUResult("continuacao", ultima_faq, 1.0, origem="memoria")
        return NLUResult("aceite_sem_contexto", score=1.0)
    if cat == "confusao":
        curto = t in {"nao entendi", "nao entendo", "num entendi", "nao to entendendo", "nao estou entendendo"}
        if curto and ultima_faq and get_faq(ultima_faq):     # "não entendi" logo depois de uma resposta
            return NLUResult("continuacao", ultima_faq, 1.0, origem="memoria")
        return NLUResult("confusao", score=1.0)
    if _re(r"^(oi|ola|opa|e ai|bom dia|boa tarde|boa noite|hey|salve)( lia)?$", t):
        return NLUResult("saudacao", score=1.0)
    agradece = r"(valeu|obrigad[oa]|brigad[oa]|tchau|ate mais|ajudou( muito)?|era isso|era so isso)"
    if (_re(r"^(tchau|ate mais|ate logo|valeu|obrigad[oa]|brigad[oa]|era so isso|so isso|falou)\b", t)
            or _re(rf"^(entendi|ok|beleza|blz|show|perfeito|otimo|massa|top|certo|ah|ahh|legal|boa)( (entao|agora|tudo))?"
                   rf"( {agradece})+( (lia|demais|mesmo))?$", t)
            or _re(r"^(nao|nada|nao precisa|nada mais|nao e so isso|so isso mesmo)( mais)?"
                   r"( (obrigad[oa]|valeu|brigad[oa]|era so isso|por enquanto|ta bom|tudo certo))*$", t)) \
            and len(t.split()) <= 6 and "?" not in texto:
        return NLUResult("despedida", score=1.0)
    faq, score = buscar_faq(t)
    if faq:
        return NLUResult("faq", faq.id, float(score))
    if ultima_faq and get_faq(ultima_faq) and continuacao(t):
        return NLUResult("continuacao", ultima_faq, 1.0, origem="memoria")
    return NLUResult("nao_entendi", score=0.0)


PROMPT_NLU = """Você é o classificador de intenções da Lia, assistente de uma oficina de chatbots.
Classifique a MENSAGEM do usuário. Responda SOMENTE com um JSON, sem markdown, no formato:
{{"intent": "<faq|fora_da_base|fora_escopo|nao_entendi>", "faq_id": "<id ou null>", "confianca": <0 a 1>}}

- "faq": a pergunta é respondida por uma das FAQs abaixo (informe o faq_id).
- "fora_da_base": é sobre chatbots, LLMs ou a oficina, mas nenhuma FAQ responde.
- "fora_escopo": não tem relação com a oficina (ex.: receita, futebol, política).
- "nao_entendi": vaga demais para saber o que a pessoa quer.

FAQs:
{faqs}"""


def classificar_por_llm(llm, texto: str, contexto_recente: str) -> NLUResult:
    faqs = "\n".join(f'- {f.id}: {f.question}' for f in load_faq())
    msgs = [
        {"role": "system", "content": PROMPT_NLU.format(faqs=faqs)},
        {"role": "user", "content": f"CONTEXTO RECENTE:\n{contexto_recente or '(início)'}\n\nMENSAGEM: {texto}"},
    ]
    resposta = llm.chat(msgs, temperature=0.0, json_mode=True, purpose="nlu")
    bruto = re.sub(r"```(?:json)?|```", "", resposta.text).strip()
    try:
        dados = json.loads(bruto[bruto.find("{"): bruto.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return NLUResult("nao_entendi", origem="llm")
    intent = dados.get("intent")
    faq_id = dados.get("faq_id")
    confianca = float(dados.get("confianca") or 0)
    if intent == "faq" and get_faq(faq_id) and confianca >= 0.6:
        return NLUResult("faq", faq_id, confianca, origem="llm")
    if intent in {"fora_da_base", "fora_escopo", "nao_entendi"}:
        return NLUResult(intent, score=confianca, origem="llm")
    return NLUResult("nao_entendi", origem="llm")
