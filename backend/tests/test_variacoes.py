"""Cobertura de linguagem da Lia: frases realistas (com maiúsculas, pontuação, acentos e abreviações)
que precisam ser entendidas por regra, e frases que NÃO podem ser capturadas por engano.

Origem: conversas manuais de 02/10, em que respostas curtas e naturais caíam em fallback.
"""
import json

import pytest

from app.config import BASE_DIR
from app.knowledge.faq import buscar_faq
from app.nlp.nlu import classificar_por_regras
from app.nlp.text import expandir_abreviacoes, normalize_text
from app.nlp.variacoes import _canon

ESPERADO = {
    "despedida": ["Tchau!", "VALEU", "vlw lia!!", "Obrigada, Lia.", "obg", "brigadão", "era só isso, obrigado",
                  "Entendi, valeu!", "show, ajudou muito", "Não, obrigado.", "tô satisfeita", "fui, flw",
                  "agora entendi, valeu", "fechou, valeu"],
    "recusa_oferta": ["Não precisa.", "n precisa", "NÃO", "agora não", "deixa pra lá", "melhor não", "dispenso",
                      "nope", "não, por enquanto"],
    "continuacao": ["Sim!", "s", "quero sim", "pode ser", "manda aí", "bora", "me dá um exemplo", "explica melhor",
                    "detalha mais", "pode continuar"],
    "confusao": ["Não tô entendendo NADA", "tô boiando", "to perdido", "Estou confusa.", "travei", "é muita informação",
                 "não sei por onde começar", "tô atrasado"],
    "saudacao": ["Oi!", "oie", "eae", "Bom dia, Lia", "oi, tudo bem?", "Olá!", "boa noite", "hey"],
    "capacidades": ["O que você faz?", "menu", "ajuda", "o que posso te perguntar?", "quais os temas?",
                    "pra que você serve?", "me ajuda"],
    "identidade": ["Você é humana?", "vc é um robô?", "quem é você?", "você é o professor?", "tem alguém aí?"],
    "falar_professor": ["chama o prof", "cadê o professor?", "quero um humano", "atendente", "me transfere",
                        "quero falar com o professor"],
    "repetir": ["repete", "pode repetir?", "fala de novo", "não vi", "dnv"],
    "elogio": ["você é ótima!", "mandou bem", "Adorei", "parabéns, Lia", "show de bola"],
    "ofensa": ["você é burra", "bot inútil", "que lixo", "não serve pra nada", "você é péssima"],
}

# Mensagens que NÃO podem ser engolidas por uma categoria curta (precisam seguir para FAQ, fluxo ou LLM).
NAO_CAPTURAR = ["não sei o que é slot", "não entendi o que é RAG", "sim, mas o que é guardrail?",
                "oi, o que é happy path?", "obrigado, e como testo o bot?", "valeu, mas e a memória?",
                "quero marcar um plantão", "não quero agendar agora, quero entender slot",
                "o professor explicou memória, mas não entendi", "repete a parte do slot"]

FAQ_PARAFRASES = {
    "memory-window": ["como o bot não esquece o que eu falei?", "por que o bot perde o fio da conversa?",
                      "o que é janela de contexto?"],
    "knowledge-base": ["como evito que o bot alucine?", "o que é RAG?", "como evitar que o bot invente resposta?"],
    "future-metrics": ["quais indicadores devo medir?", "quais kpis usar?", "como meço se o bot está bom?"],
    "slot-state": ["o que é uma entidade?", "oq é slot msm?", "como guardar dados do usuário?"],
    "rule-versus-llm": ["quando usar ia e quando usar regra?", "qnd uso regra e qnd uso llm", "o que é determinístico?"],
    "testing-demo": ["como testo meu bot?", "quais casos de teste fazer?"],
    "system-prompt": ["o que é o dna do bot?", "como escrevo as instruções do bot?"],
    "guardrails-handoff": ["como bloquear prompt injection?", "quando escalar para um humano?"],
    "conversation-design": ["o que é ux de chatbot?", "quais boas práticas de conversa?"],
    "minimum-checklist": ["quais os requisitos mínimos?", "o que um bot precisa ter?"],
    "happy-path": ["o que é happy path?", "qual o primeiro passo?"],
}


def _casos():
    for intent, frases in ESPERADO.items():
        for f in frases:
            yield intent, f


@pytest.mark.parametrize("intent,frase", list(_casos()))
def test_cobertura_por_categoria(intent, frase):
    # "continuacao" depende de haver uma resposta da FAQ antes (aceite de uma oferta)
    ultima = "slot-state" if intent == "continuacao" else None
    assert classificar_por_regras(frase, ultima).intent == intent, frase


@pytest.mark.parametrize("frase", NAO_CAPTURAR)
def test_frases_compostas_nao_sao_engolidas(frase):
    curtas = {"despedida", "recusa_oferta", "saudacao", "elogio", "ofensa", "identidade", "capacidades", "repetir",
              "confusao", "aceite_sem_contexto"}
    assert classificar_por_regras(frase, "slot-state").intent not in curtas, frase


@pytest.mark.parametrize("faq_id,frase", [(k, f) for k, fs in FAQ_PARAFRASES.items() for f in fs])
def test_parafrases_resolvidas_pela_faq(faq_id, frase):
    achada, _ = buscar_faq(expandir_abreviacoes(normalize_text(frase)))
    assert achada and achada.id == faq_id, frase


def test_nenhuma_expressao_em_duas_categorias():
    dados = json.loads((BASE_DIR / "data" / "variacoes.json").read_text(encoding="utf-8"))
    vistas: dict[str, str] = {}
    for cat, itens in dados.items():
        if cat.startswith("_"):
            continue
        for x in itens:
            c = _canon(x)
            assert vistas.setdefault(c, cat) == cat, f"'{x}' está em {vistas[c]} e em {cat}"


def test_tamanho_do_banco():
    dados = json.loads((BASE_DIR / "data" / "variacoes.json").read_text(encoding="utf-8"))
    total = sum(len(v) for k, v in dados.items() if not k.startswith("_"))
    assert total >= 500 and len([k for k in dados if not k.startswith("_")]) == 11
