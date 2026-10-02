"""Robustez a erros de digitação (proposta de melhoria do classificador, 02/10/2026).

Escrita ANTES da implementação, para medir o antes e o depois. Camada testada aqui: regras +
correção ortográfica, sem LLM. A lógica de confiança do classificador LLM é testada com o modelo
simulado em test_api.py; a medição com o modelo real fica em scripts/robustez.py.
"""
import pytest

from app.nlp.nlu import classificar_por_regras

# (frase com erro, intenção esperada, faq esperada ou None)
COM_ERRO = [
    ("o que é slto?", "faq", "slot-state"),
    ("oq e slto msm", "faq", "slot-state"),
    ("como evitr q o bot invete resposta", "faq", "knowledge-base"),
    ("quero fala cm o profssor", "falar_professor", None),
    ("quero falar com o porfessor", "falar_professor", None),
    ("chama o profesor", "falar_professor", None),
    ("quero agendr um plantao", "agendar_plantao", None),
    ("quero agendar um plantaõ", "agendar_plantao", None),
    ("quais horaios tem quarta", "disponibilidade", None),
    ("quais os horarios disponiveis", "disponibilidade", None),
    ("nao to entedendo nada", "confusao", None),
    ("o que é guardrial?", "faq", "guardrails-handoff"),
    ("o que é sistem prompt", "faq", "system-prompt"),
    ("como funciona a memoira do bot?", "faq", "memory-window"),
    ("quais metircas devo acompanhar?", "faq", "future-metrics"),
    ("como testo o bot antes da demostracao?", "faq", "testing-demo"),
    ("o que é base de conhecimneto?", "faq", "knowledge-base"),
    ("quando uso regar e quando uso llm", "faq", "rule-versus-llm"),
    ("o que é desing conversacional?", "faq", "conversation-design"),
    ("qual o cheklist minimo?", "faq", "minimum-checklist"),
    ("quero cancelr meu plantao", "cancelar", None),
    ("obrigdo", "despedida", None),
    ("valew", "despedida", None),
]

# Limitação conhecida (de propósito): dois erros na mesma palavra curta, como "cancleat", não são
# corrigidos; aceitar isso exigiria baixar o limite de similaridade e aumentaria os falsos positivos.
# Esses casos seguem para o classificador LLM.

# Frases que a correção NÃO pode transformar em outra intenção
NAO_ALTERAR = [
    ("o que é slot?", "faq", "slot-state"),
    ("quero agendar um plantão", "agendar_plantao", None),
    ("tchau", "despedida", None),
    ("minha nota foi boa", None, None),          # não é tema sensível
    ("como faço uma prova de conceito?", None, None),
]

# Palavras comuns que a correção não pode tocar (falsos positivos achados em 02/10)
INTACTAS = ["estudando", "quebrou", "remarcar", "mariana", "henrique", "fernanda", "streamlit", "gemini",
            "amanha", "entregar", "janela", "projeto", "simpatica", "errado", "quarta"]


@pytest.mark.parametrize("palavra", INTACTAS)
def test_palavras_comuns_intactas(palavra):
    from app.nlp.ortografia import corrigir_palavra
    assert corrigir_palavra(palavra) == palavra


@pytest.mark.parametrize("frase,intent,faq", COM_ERRO)
def test_erros_de_digitacao(frase, intent, faq):
    r = classificar_por_regras(frase, None)
    assert r.intent == intent, f"{frase!r} → {r.intent}"
    if faq:
        assert r.faq_id == faq, f"{frase!r} → {r.faq_id}"


def test_taxa_de_acerto_minima():
    acertos = sum(1 for f, i, q in COM_ERRO
                  if (r := classificar_por_regras(f, None)).intent == i and (q is None or r.faq_id == q))
    assert acertos / len(COM_ERRO) >= 0.85, f"{acertos}/{len(COM_ERRO)}"


@pytest.mark.parametrize("frase,intent,faq", NAO_ALTERAR)
def test_correcao_nao_cria_falso_positivo(frase, intent, faq):
    r = classificar_por_regras(frase, None)
    if intent:
        assert r.intent == intent and (faq is None or r.faq_id == faq)
    else:
        assert r.intent not in {"tema_sensivel", "falar_professor", "cancelar", "agendar_plantao"}, r.intent


# --- lógica de confiança do classificador LLM (LLM simulado) ---------------------------------
from conftest import H, make_client  # noqa: E402


def _cliente_com_llm(tmp_path, monkeypatch, intent, conf, faq_id=None):
    from app.core import orchestrator as orq
    from app.nlp.nlu import NLUResult

    def falso(llm, texto, contexto, ultima_faq=None):
        r = NLUResult(intent, faq_id, conf, origem="llm")
        r.texto_interpretado = "texto interpretado pelo LLM"
        return r
    monkeypatch.setattr(orq, "classificar_por_llm", falso)
    return make_client(tmp_path)


def _sessao(c, *msgs):
    sid = c.post("/sessions", json={}, headers=H).json()["session_id"]
    r = None
    for m in msgs:
        r = c.post("/chat", json={"session_id": sid, "message": m}, headers=H).json()
    return sid, r


MSG_LIVRE = "zzz xyzw qwerty"


def test_faq_com_confianca_alta_responde(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "faq", 0.9, "slot-state") as c:
        _, r = _sessao(c, MSG_LIVRE)
        assert r["route"] == "faq" and r["faq_id"] == "slot-state" and r["nlu_origem"] == "llm"
        assert r["texto_interpretado"] == "texto interpretado pelo LLM"


def test_faq_com_confianca_media_pergunta_antes(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "faq", 0.6, "slot-state") as c:
        sid, r = _sessao(c, MSG_LIVRE)
        assert r["intent"] == "esclarecimento" and "Você quis perguntar" in r["reply"] and not r["fallback"]
        r = c.post("/chat", json={"session_id": sid, "message": "sim"}, headers=H).json()
        assert r["route"] == "faq" and r["faq_id"] == "slot-state"


def test_esclarecimento_negado_pede_outra_forma(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "faq", 0.6, "slot-state") as c:
        sid, _ = _sessao(c, MSG_LIVRE)
        r = c.post("/chat", json={"session_id": sid, "message": "não"}, headers=H).json()
        assert r["intent"] == "esclarecimento_negado" and "outro jeito" in r["reply"]


def test_confianca_baixa_cai_em_fallback(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "falar_professor", 0.4) as c:
        _, r = _sessao(c, MSG_LIVRE)
        assert r["fallback"] and not r["handoff"]["active"]


def test_professor_com_confianca_alta_faz_handoff(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "falar_professor", 0.9) as c:
        _, r = _sessao(c, MSG_LIVRE)
        assert r["handoff"]["active"] and r["nlu_origem"] == "llm"


def test_cancelar_pelo_llm_nunca_executa_sem_confirmacao(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "cancelar", 0.95) as c:
        sid, _ = _sessao(c, "quero agendar um plantão", "Ana Souza", "553344", "ana@fiap.com.br", "memória", "1", "sim")
        r = c.post("/chat", json={"session_id": sid, "message": MSG_LIVRE}, headers=H).json()
        assert "Quer mesmo cancelar" in r["reply"] and r["slots"]["protocolo"]          # ainda agendado
        r = c.post("/chat", json={"session_id": sid, "message": "não"}, headers=H).json()
        assert r["slots"]["protocolo"] and "continua marcado" in r["reply"]
        c.post("/chat", json={"session_id": sid, "message": MSG_LIVRE}, headers=H)
        r = c.post("/chat", json={"session_id": sid, "message": "sim"}, headers=H).json()
        assert r["slots"]["protocolo"] is None and "Cancelei" in r["reply"]


def test_agendar_pelo_llm_so_abre_o_fluxo(tmp_path, monkeypatch):
    with _cliente_com_llm(tmp_path, monkeypatch, "agendar_plantao", 0.9) as c:
        _, r = _sessao(c, MSG_LIVRE)
        assert r["etapa"] == "plantao:nome" and r["slots"]["protocolo"] is None


def test_correcao_aparece_no_raio_x(conversa):
    r = conversa().diz("o que é slto?")
    assert r["texto_interpretado"] == "o que e slot" and r["faq_id"] == "slot-state"


def test_agradecimento_seguido_de_pergunta_nao_encerra():
    """Achado em scripts/robustez.py (02/10): 'vlw mas e a memoria' era tratado como despedida."""
    for frase in ["vlw mas e a memoria", "valeu, e o que é slot", "obrigado mas como testo o bot"]:
        assert classificar_por_regras(frase, None).intent != "despedida", frase
    assert classificar_por_regras("vlw mas e a memoria", None).faq_id == "memory-window"
