"""Correção ortográfica controlada (difflib, biblioteca padrão; sem dependência nova).

Só corrige para palavras do VOCABULÁRIO da Lia (FAQ, variações e termos das regras), nunca para
qualquer palavra do português. Regras de segurança:
  - palavras com dígitos, "@" ou ponto ficam intactas (RM, e-mail, horários, datas);
  - palavras com menos de 4 letras ficam intactas;
  - 4 letras: só troca de duas letras vizinhas ("slto" → "slot");
  - 5 letras ou mais: similaridade ≥ 0,82, mesma primeira letra e diferença de tamanho ≤ 1
    (até 7 letras) ou ≤ 2 (palavras maiores);
  - a mensagem original continua sendo a gravada; a corrigida é só a "interpretada".
Os slots do agendamento (nome, RM, e-mail) são validados sobre o texto ORIGINAL, no orquestrador.
"""
import difflib
import json
import re
from functools import lru_cache

from app.config import BASE_DIR
from app.nlp.text import normalize_text

# termos usados pelas regras de intenção (nlu.py) que não aparecem na FAQ nem nas variações
TERMOS_DAS_REGRAS = """
professor professora humano humana atendente pessoa alguem falar conversar chamar procurar contatar
agendar agendamento marcar reservar plantao plantoes monitoria horario horarios disponivel disponiveis
livre livres vaga vagas cancelar cancela desmarcar segunda terca quarta quinta sexta duvida duvidas
entendendo entender entendi perdido perdida confuso confusa obrigado obrigada valeu tchau
nota injusta injusto reprovado reprovada coordenacao reclamar reclamacao atestado doente
guardrail guardrails prompt sistema memoria estado slot slots handoff inventar invente resposta respostas
evitar testar teste testes demonstracao metricas metrica checklist minimo conhecimento base design
conversacional regra regras persona fluxo exemplo exemplos agente chatbot chatbots oficina
""".split()


@lru_cache
def vocabulario() -> frozenset[str]:
    palavras = set(TERMOS_DAS_REGRAS)
    faq = json.loads((BASE_DIR / "data" / "faq.json").read_text(encoding="utf-8"))
    for item in faq:
        texto = " ".join([item["question"], item["answer"], *item["keywords"]])
        palavras.update(normalize_text(texto).split())
    variacoes = json.loads((BASE_DIR / "data" / "variacoes.json").read_text(encoding="utf-8"))
    for cat, itens in variacoes.items():
        if not cat.startswith("_"):
            for x in itens:
                palavras.update(normalize_text(x).split())
    return frozenset(p for p in palavras if len(p) >= 4 and p.isalpha())


def _troca_vizinha(a: str, b: str) -> bool:
    if len(a) != len(b) or a == b:
        return False
    dif = [i for i in range(len(a)) if a[i] != b[i]]
    return len(dif) == 2 and dif[1] == dif[0] + 1 and a[dif[0]] == b[dif[1]] and a[dif[1]] == b[dif[0]]


@lru_cache(maxsize=4096)
def corrigir_palavra(p: str) -> str:
    vocab = vocabulario()
    if len(p) < 4 or p in vocab or not p.isalpha():
        return p
    if len(p) == 4:
        candidatas = [v for v in vocab if _troca_vizinha(p, v)]
        return candidatas[0] if len(candidatas) == 1 else p
    proximas = difflib.get_close_matches(p, vocab, n=3, cutoff=0.82)
    # quem digita rápido quase nunca erra a 1ª letra ("estudando" não pode virar "testando"), e palavra
    # curta só pode mudar 1 letra de tamanho ("quebrou" não pode virar "quero")
    folga = 1 if len(p) <= 7 else 2
    proximas = [c for c in proximas if c[0] == p[0] and abs(len(c) - len(p)) <= folga][:2]
    if not proximas:
        return p
    # empate muito próximo entre duas candidatas = ambíguo: não chuta
    if len(proximas) == 2:
        r0 = difflib.SequenceMatcher(None, p, proximas[0]).ratio()
        r1 = difflib.SequenceMatcher(None, p, proximas[1]).ratio()
        if abs(r0 - r1) < 0.02 and proximas[0][:3] != proximas[1][:3]:
            return p
    return proximas[0]


def corrigir(texto_normalizado: str) -> str:
    """Recebe texto já normalizado e com abreviações expandidas; devolve a versão interpretada."""
    return " ".join(corrigir_palavra(p) if re.fullmatch(r"[a-z]+", p) else p for p in texto_normalizado.split())
