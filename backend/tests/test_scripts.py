"""Scripts de avaliação: juiz (rubrica v2) e relatório do A/B."""
import json

from app.llm import client as cl
from scripts import llm_judge
from scripts.ab_test import relatorio_md, resumir


def conversa_exemplo():
    return {"id": "E2", "titulo": "x", "informativa": True, "erros": [], "checagens": [{"regra": ["final"], "ok": True}],
            "saudacao": "Oi!", "transcricao": [{"usuario": "como evitar que o bot invente?", "lia": "Use palavras-chave.",
                                                "intent": "faq", "route": "faq", "fallback": False, "handoff": False,
                                                "sentimento": "neutro", "latency_ms": 10, "nlu_origem": "llm",
                                                "guardrail": None}]}


def test_juiz_recebe_a_faq_como_referencia_e_limpa_think(monkeypatch):
    capturado = {}

    def chat(self, messages, **kw):
        capturado["system"] = messages[0]["content"]
        return cl.LLMResponse('<think>vou pensar {sem json}</think>{"justificativa": "extrapolou a FAQ", '
                              '"pior_trecho": "Use palavras-chave.", "relevancia": 4, "aderencia_ao_papel": 4, '
                              '"retencao_de_contexto": 5, "clareza": 5, "fidelidade_a_base": 2}', "qwen")

    monkeypatch.setattr(cl.LLMClient, "chat", chat)
    j = llm_judge.Juiz("mock").avaliar(conversa_exemplo())
    assert j["fidelidade_a_base"] == 2 and j["pior_trecho"] == "Use palavras-chave."
    assert "[knowledge-base]" in capturado["system"] and "quarta 07/10" in capturado["system"]


def test_nota_fora_da_escala_e_limitada(monkeypatch):
    monkeypatch.setattr(cl.LLMClient, "chat", lambda self, m, **kw: cl.LLMResponse(json.dumps(
        {"relevancia": 9, "aderencia_ao_papel": 0, "retencao_de_contexto": 3, "clareza": 3, "fidelidade_a_base": 3}), "x"))
    j = llm_judge.Juiz("mock").avaliar(conversa_exemplo())
    assert j["relevancia"] == 5 and j["aderencia_ao_papel"] == 1


def test_relatorio_lista_notas_baixas_com_trecho():
    r = conversa_exemplo()
    r["juiz"] = {"relevancia": 4, "aderencia_ao_papel": 4, "retencao_de_contexto": 5, "clareza": 5,
                 "fidelidade_a_base": 2, "justificativa": "extrapolou a FAQ.", "pior_trecho": "Use palavras-chave."}
    execucao = {"data": "hoje", "juiz": "x", "n_conversas": 1, "conversas_ids": "E2",
                "variantes": {"v2": {"resumo": resumir([r]), "conversas": [r]}}}
    md = relatorio_md(execucao)
    assert "Juiz: fidelidade" in md and "| 2.0 |" in md
    assert "**v2 / E2** (fidelidade_a_base 2): extrapolou a FAQ." in md and '"Use palavras-chave."' in md


def test_rubrica_conhece_as_regras_do_sistema():
    sistema = llm_judge.Juiz("mock").sistema
    assert "Prof. Fernando" in sistema and "PL-XXXX-PN" in sistema and "no máximo 3 horários" in sistema
    assert "Cancelar um plantão agendado é uma capacidade do sistema" in sistema


def test_rejulgar_apenas_falhas(tmp_path, monkeypatch):
    chamadas = []
    monkeypatch.setattr(cl.LLMClient, "chat", lambda self, m, **kw: chamadas.append(1) or cl.LLMResponse(json.dumps(
        {"relevancia": 4, "aderencia_ao_papel": 4, "retencao_de_contexto": 4, "clareza": 4, "fidelidade_a_base": 4}), "x"))
    ok, falha = conversa_exemplo(), conversa_exemplo()
    ok["juiz"] = {"relevancia": 5, "aderencia_ao_papel": 5, "retencao_de_contexto": 5, "clareza": 5, "fidelidade_a_base": 5}
    falha["juiz"] = {"erro": "LLMUnavailable"}
    arq = tmp_path / "ab_x.json"
    arq.write_text(json.dumps({"data": "d", "juiz": "j", "n_conversas": 2, "conversas_ids": "E2",
                               "variantes": {"v2": {"resumo": {}, "conversas": [ok, falha]}}}), encoding="utf-8")
    destino = llm_judge.rejulgar(str(arq), llm_judge.Juiz("mock"), pausa=0, apenas_falhas=True)
    dados = json.loads(destino.with_suffix(".json").read_text(encoding="utf-8"))
    assert len(chamadas) == 1 and dados["variantes"]["v2"]["conversas"][1]["juiz"]["relevancia"] == 4
