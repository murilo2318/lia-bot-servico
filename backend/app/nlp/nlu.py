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
from app.nlp.ortografia import corrigir
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
    apelido: str | None = None     # nome com que o aluno se apresentou
    texto_interpretado: str | None = None   # como a mensagem foi entendida (raio-X)


def _re(padrao: str, t: str) -> bool:
    return re.search(padrao, t) is not None


def pedido_de_humano(t: str) -> bool:
    """Porte de isHandoffRequest (Build Day)."""
    contato = r"(?:falar|fala|conversar|chamar|chama|procurar|contatar|ligar)"
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


def interpretar(texto: str) -> str:
    """Texto que as regras enxergam: sem acento, abreviações expandidas e erros de digitação corrigidos."""
    return corrigir(expandir_abreviacoes(normalize_text(texto)))


def classificar_por_regras(texto: str, ultima_faq: str | None) -> NLUResult:
    t = interpretar(texto)
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
    cat = categoria(t, ja_normalizado=True)
    if cat == "pedido_professor":
        return NLUResult("falar_professor", score=1.0)
    if cat in {"ofensa", "identidade", "capacidades", "repetir", "elogio"}:
        return NLUResult(cat, score=1.0)
    if cat == "saudacao":
        return NLUResult("saudacao", score=1.0)
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
    m = re.match(r"^(?:(?:oi|ola|opa|e ai|bom dia|boa tarde|boa noite|hey|salve)(?: lia)? )?"
                 r"(?:eu )?(?:sou (?:a |o )?|me chamo |meu nome e |aqui e (?:a |o )?)([a-z]+)$", t)
    if m and m.group(1) not in {"aluno", "aluna", "novo", "nova", "eu", "estudante"}:
        return NLUResult("saudacao", score=1.0, apelido=m.group(1).capitalize())
    if _re(r"^(oi|ola|opa|e ai|bom dia|boa tarde|boa noite|hey|salve)( lia)?$", t):
        return NLUResult("saudacao", score=1.0)
    agradece = r"(valeu|obrigad[oa]|brigad[oa]|tchau|ate mais|ajudou( muito)?|era isso|era so isso)"
    if (_re(r"^(tchau|ate mais|ate logo|valeu|obrigad[oa]|brigad[oa]|era so isso|so isso|falou)\b", t)
            or _re(rf"^(entendi|ok|beleza|blz|show|perfeito|otimo|massa|top|certo|ah|ahh|legal|boa)( (entao|agora|tudo))?"
                   rf"( {agradece})+( (lia|demais|mesmo))?$", t)
            or _re(r"^(nao|nada|nao precisa|nada mais|nao e so isso|so isso mesmo)( mais)?"
                   r"( (obrigad[oa]|valeu|brigad[oa]|era so isso|por enquanto|ta bom|tudo certo))*$", t)) \
            and len(t.split()) <= 6 and "?" not in texto \
            and not _re(r"\b(mas|porem|so que|e a|e o|e as|e os|o que|como|qual|quais|quando|onde|por que)\b", t):
        return NLUResult("despedida", score=1.0)
    faq, score = buscar_faq(t)
    if faq:
        return NLUResult("faq", faq.id, float(score))
    if ultima_faq and get_faq(ultima_faq) and continuacao(t):
        return NLUResult("continuacao", ultima_faq, 1.0, origem="memoria")
    return NLUResult("nao_entendi", score=0.0)


INTENCOES_LLM = {
    "faq": "dúvida sobre um tema da oficina respondida por uma das FAQs (informe o faq_id)",
    "continuacao": "pede para continuar ou aprofundar a resposta anterior (\"e por quê?\", \"mais detalhes\")",
    "saudacao": "cumprimento ou apresentação, sem pergunta",
    "despedida": "encerra ou agradece, sem pedir mais nada",
    "agendar_plantao": "quer marcar plantão de dúvidas com o professor",
    "consultar_agendamento": "quer saber o plantão que já marcou",
    "cancelar": "quer cancelar ou desmarcar o plantão",
    "disponibilidade": "pergunta quais horários de plantão existem",
    "falar_professor": "pede para falar com o professor ou com uma pessoa",
    "tema_sensivel": "reclamação de nota, saúde, problema pessoal, assédio ou sofrimento",
    "capacidades": "pergunta o que a Lia faz ou pode responder",
    "identidade": "pergunta se a Lia é humana, robô ou quem ela é",
    "repetir": "pede para repetir a última resposta",
    "confusao": "diz que está perdido ou não entende nada, sem pergunta específica",
    "fora_da_base": "é sobre chatbots, LLMs ou a oficina, mas nenhuma FAQ responde",
    "fora_escopo": "não tem relação com a oficina (futebol, receita, política...)",
    "nao_entendi": "vaga demais para saber o que a pessoa quer",
}

PROMPT_NLU = """Você é o classificador de intenções da Lia, assistente de uma oficina de chatbots.
Você só CLASSIFICA; quem decide e executa é o sistema. A mensagem pode ter erros de digitação,
abreviações e gírias: interprete o que a pessoa quis dizer.

INTENÇÕES:
{intencoes}

FAQs (id: pergunta | resumo | palavras-chave):
{faqs}

Responda SOMENTE com um JSON, sem markdown:
{{"intent": "<uma das intenções>", "faq_id": "<id ou null>", "confianca": <0 a 1>,
  "texto_interpretado": "<a mensagem reescrita sem erros>", "motivo": "<frase curta>"}}
Use confiança alta (≥ 0,8) só quando não houver dúvida. Se a mensagem for ambígua, use confiança baixa."""


def _resumo_faq(f) -> str:
    resumo = f.answer.split(". ")[0][:110]
    return f"- {f.id}: {f.question} | {resumo} | {', '.join(f.keywords[:6])}"


def classificar_por_llm(llm, texto: str, contexto_recente: str, ultima_faq: str | None = None) -> NLUResult:
    """Classificador LLM com todas as intenções. O orquestrador decide o que fazer pela confiança."""
    sistema = PROMPT_NLU.format(
        intencoes="\n".join(f"- {k}: {v}" for k, v in INTENCOES_LLM.items()),
        faqs="\n".join(_resumo_faq(f) for f in load_faq()))
    estado = f"Última FAQ respondida: {ultima_faq}" if ultima_faq else "Nenhuma FAQ respondida ainda."
    msgs = [
        {"role": "system", "content": sistema},
        {"role": "user", "content": f"CONTEXTO RECENTE:\n{contexto_recente or '(início)'}\n{estado}\n\nMENSAGEM: {texto}"},
    ]
    resposta = llm.chat(msgs, temperature=0.0, json_mode=True, purpose="nlu")
    bruto = re.sub(r"<think>.*?</think>|```(?:json)?|```", "", resposta.text, flags=re.S).strip()
    try:
        dados = json.loads(bruto[bruto.find("{"): bruto.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return NLUResult("nao_entendi", origem="llm")
    intent = dados.get("intent")
    faq_id = dados.get("faq_id") if get_faq(dados.get("faq_id")) else None
    try:
        confianca = max(0.0, min(1.0, float(dados.get("confianca") or 0)))
    except (TypeError, ValueError):
        confianca = 0.0
    if intent not in INTENCOES_LLM or (intent == "faq" and not faq_id):
        return NLUResult("nao_entendi", score=confianca, origem="llm")
    if intent == "continuacao":
        faq_id = ultima_faq if get_faq(ultima_faq) else None
        if not faq_id:
            return NLUResult("nao_entendi", score=confianca, origem="llm")
    r = NLUResult(intent, faq_id, confianca, origem="llm")
    r.texto_interpretado = str(dados.get("texto_interpretado") or "")[:300] or None
    return r
