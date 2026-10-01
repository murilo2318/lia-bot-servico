"""Contrato HTTP: autenticação, códigos de resposta e rotas diferenciais."""
from conftest import H, make_client


def test_health_publico(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["provider"] == "mock"


def test_401_sem_chave(client):
    assert client.post("/sessions", json={}).status_code == 401
    assert client.post("/sessions", json={}, headers={"X-API-Key": "errada"}).status_code == 401


def test_404_sessao_inexistente(client):
    r = client.post("/chat", json={"session_id": "naoexiste", "message": "oi"}, headers=H)
    assert r.status_code == 404 and "Sessão não encontrada" in r.json()["detail"]
    assert client.get("/sessions/naoexiste", headers=H).status_code == 404


def test_422_requisicao_invalida(client):
    assert client.post("/chat", json={"message": "oi"}, headers=H).status_code == 422
    assert client.post("/sessions", json={"objetivo": "culinaria"}, headers=H).status_code == 422
    assert client.post("/feedback", json={"session_id": "x", "score": 9}, headers=H).status_code == 422


def test_503_modelo_indisponivel_nao_corrompe_estado(tmp_path):
    with make_client(tmp_path, llm_provider="mock_down") as c:
        sid = c.post("/sessions", json={}, headers=H).json()["session_id"]
        r = c.post("/chat", json={"session_id": sid, "message": "o que é um slot?"}, headers=H)
        assert r.status_code == 503 and "indisponível" in r.json()["detail"]
        assert c.get(f"/sessions/{sid}", headers=H).json()["turn"] == 0
        # rotas por regra continuam funcionando sem o modelo (degradação graciosa)
        r = c.post("/chat", json={"session_id": sid, "message": "quero agendar um plantão"}, headers=H)
        assert r.status_code == 200 and r.json()["etapa"] == "plantao:nome"
        r = c.post("/chat", json={"session_id": sid, "message": "queria ver umas coisas"}, headers=H)
        assert r.status_code == 200


def test_mensagem_longa_guardrail(conversa):
    r = conversa().diz("a" * 501)
    assert r["guardrail"]["tipo"] == "tamanho" and "500" in r["reply"]


def test_feedback_csat_e_metricas(client, conversa):
    cv = conversa()
    cv.diz("o que é um slot?")
    r = client.post("/feedback", json={"session_id": cv.sid, "score": 5}, headers=H)
    assert r.status_code == 201
    cv2 = conversa()
    cv2.diz("quero falar com o professor")
    client.post("/feedback", json={"session_id": cv2.sid, "score": 2}, headers=H)
    m = client.get("/metrics", headers=H).json()
    assert m["conversas"] == 2 and m["taxa_contencao"] == 0.5 and m["taxa_handoff"] == 0.5
    assert m["mensagens_por_conversa"] == 1.0
    assert m["csat"]["media_conversas_contidas"] == 5 and m["csat"]["media_conversas_com_handoff"] == 2


def test_delete_direito_ao_esquecimento(client, conversa):
    cv = conversa()
    cv.diz("meu email é marina.alves@fiap.com.br e rm 562358, queria ver umas coisas")
    assert client.delete(f"/sessions/{cv.sid}", headers=H).status_code == 204
    assert client.get(f"/sessions/{cv.sid}", headers=H).status_code == 404
    assert client.get("/metrics", headers=H).json()["conversas"] == 1   # agregado permanece


def test_log_mascara_dados_pessoais(client, conversa):
    cv = conversa()
    cv.diz("meu email é marina.alves@fiap.com.br e rm 562358, queria ver umas coisas")
    ultimos = client.get("/metrics", headers=H).json()["ultimos_fallbacks"]
    assert "[email]" in ultimos[-1] and "[rm]" in ultimos[-1] and "562358" not in ultimos[-1]


def test_handoff_resolvido_devolve_conversa(client, conversa):
    cv = conversa()
    cv.diz("quero falar com o professor")
    assert cv.diz("oi")["intent"] == "handoff_ativo"
    r = client.patch(f"/handoffs/{cv.sid}", json={"status": "resolvido"}, headers=H)
    assert r.status_code == 200
    assert cv.diz("oi")["intent"] == "saudacao"


def test_function_calling_agenda(conversa):
    r = conversa().diz("quais horários tem na quinta?")
    assert r["route"] == "ferramenta" and "quinta 08/10 às 18h" in r["reply"] and "terça" not in r["reply"]


def test_stream_sse(client, conversa):
    cv = conversa()
    with client.stream("POST", "/chat/stream", json={"session_id": cv.sid, "message": "o que é um slot?"},
                       headers=H) as r:
        corpo = "".join(r.iter_text())
    assert "event: token" in corpo and "event: done" in corpo and '"faq_id":"slot-state"' in corpo


def test_ab_sessao_com_variante(client):
    r = client.post("/sessions", json={"prompt_version": "v1"}, headers=H).json()
    assert r["prompt_version"] == "v1"
    sid = r["session_id"]
    client.post("/chat", json={"session_id": sid, "message": "o que é um slot?"}, headers=H)
    assert client.get("/metrics?prompt_version=v1", headers=H).json()["conversas"] == 1
    assert client.get("/metrics?prompt_version=v2", headers=H).json()["conversas"] == 0


def test_resumo_rolante(tmp_path):
    with make_client(tmp_path, history_window_turns=2) as c:
        sid = c.post("/sessions", json={}, headers=H).json()["session_id"]
        for m in ["o que é um slot?", "o que é guardrail?", "o que é system prompt?", "o que é rag?"]:
            c.post("/chat", json={"session_id": sid, "message": m}, headers=H)
        s = c.get(f"/sessions/{sid}", headers=H).json()
        assert s["resumo"].startswith("Resumo:")


def test_agenda_por_regra_respeita_o_dia_quando_o_modelo_cai(tmp_path):
    with make_client(tmp_path, llm_provider="mock_down") as c:
        sid = c.post("/sessions", json={}, headers=H).json()["session_id"]
        r = c.post("/chat", json={"session_id": sid, "message": "tem horário de plantão na quarta?"}, headers=H).json()
        assert "quarta 07/10" in r["reply"] and "terça" not in r["reply"] and "quinta" not in r["reply"]


def test_nlu_acerta_mas_resposta_falha_vira_503_e_nao_falso_fallback(tmp_path, monkeypatch):
    """Bug achado no teste real de 01/10: a falha da RESPOSTA virava 'não entendi'."""
    import json as _json

    from app.llm import client as cl
    original = cl.LLMClient.chat

    def chat(self, messages, **kw):
        if kw.get("purpose") == "nlu":
            return cl.LLMResponse(_json.dumps({"intent": "faq", "faq_id": "memory-window", "confianca": 0.9}), "m")
        if kw.get("purpose") == "answer":
            raise cl.LLMUnavailable("503 high demand", transitorio=True)
        return original(self, messages, **kw)

    monkeypatch.setattr(cl.LLMClient, "chat", chat)
    with make_client(tmp_path) as c:
        sid = c.post("/sessions", json={}, headers=H).json()["session_id"]
        r = c.post("/chat", json={"session_id": sid, "message": "como faço o bot não perder o fio da meada?"}, headers=H)
        assert r.status_code == 503
        assert c.get(f"/sessions/{sid}", headers=H).json()["turn"] == 0     # nada foi salvo


def test_sim_depois_da_agenda_inicia_agendamento_com_o_dia_pedido(conversa):
    """Bug achado no teste real: o modelo pergunta 'quer que eu reserve?' e o 'sim' caía em fallback."""
    cv = conversa(objetivo="faq")
    cv.diz("tem horário de plantão na quinta?")
    r = cv.diz("sim")
    assert r["intent"] == "agendar_plantao" and r["etapa"] == "plantao:nome"
    cv.diz("Marina Alves"); cv.diz("562358")
    r = cv.diz("marina.alves@fiap.com.br")
    assert r["etapa"] == "plantao:horario"
    assert r["reply"].index("quinta") < r["reply"].index("terça")     # o dia pedido vem primeiro


def test_nao_depois_da_agenda(conversa):
    cv = conversa()
    cv.diz("quais horários tem?")
    r = cv.diz("não")
    assert r["intent"] == "recusou_agendamento" and not r["fallback"]


def test_retomada_do_fluxo_sem_agradecimento_fora_de_lugar(conversa):
    """Achado do juiz (E9): 'Voltando ao agendamento: Obrigada, Carla. Qual é o seu RM?'."""
    cv = conversa(objetivo="faq")
    cv.diz("quero agendar um plantão")
    cv.diz("Carla Mendes")
    r = cv.diz("o que é um slot?")
    assert r["reply"].endswith("Voltando ao agendamento: Qual é o seu RM?")
