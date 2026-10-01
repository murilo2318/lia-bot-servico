"""Cliente HTTP real (Gemini/Groq) testado com respostas simuladas do provedor."""
import httpx
import pytest

from app.config import Settings
from app.llm.client import LLMClient, LLMUnavailable


def cfg(**kw):
    base = dict(llm_provider="gemini", gemini_api_key="k", groq_api_key="k", llm_retry_wait_s=0, llm_retries=1,
                llm_fallback_provider="", gemini_model="gemini-teste", groq_model="groq-teste", _env_file=None)
    base.update(kw)
    return Settings(**base)


def fake_post(status=200, body=None, capture=None, exc=None, roteiro=None):
    """roteiro: lista de (status, body) devolvidos em sequência, um por chamada."""
    chamadas = []

    def _post(url, json, timeout, headers):
        chamadas.append({"url": url, "json": json})
        if capture is not None:
            capture.update(url=url, json=json, headers=headers, chamadas=chamadas)
        if exc:
            raise exc
        if roteiro:
            st, bd = roteiro[min(len(chamadas) - 1, len(roteiro) - 1)]
            return httpx.Response(st, json=bd)
        return httpx.Response(status, json=body or {})
    return _post


OK = {"choices": [{"message": {"content": "ok"}}], "model": "modelo-x"}
SOBRECARGA = [{"error": {"code": 503, "message": "This model is currently experiencing high demand."}}]


def test_payload_e_parse_com_ferramenta(monkeypatch):
    cap = {}
    body = {"model": "gemini-x", "usage": {"prompt_tokens": 50, "completion_tokens": 7},
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "consultar_agenda", "arguments": "{\"dia\": \"quinta\"}"}}]}}]}
    monkeypatch.setattr(httpx, "post", fake_post(body=body, capture=cap))
    r = LLMClient(cfg()).chat([{"role": "user", "content": "oi"}], tools=[{"type": "function"}], json_mode=True)
    assert "generativelanguage.googleapis.com" in cap["url"]
    assert cap["headers"]["Authorization"] == "Bearer k"
    assert cap["json"]["tool_choice"] == "auto"
    assert "response_format" not in cap["json"]          # Gemini: JSON é extraído do texto
    assert r.tool_calls[0].arguments == {"dia": "quinta"} and r.tool_calls[0].id == "c1"
    assert (r.prompt_tokens, r.completion_tokens) == (50, 7)


def test_groq_usa_endpoint_proprio(monkeypatch):
    cap = {}
    monkeypatch.setattr(httpx, "post", fake_post(body={"choices": [{"message": {"content": "ok"}}]}, capture=cap))
    assert LLMClient(cfg(), "groq").chat([{"role": "user", "content": "oi"}]).text == "ok"
    assert "api.groq.com" in cap["url"]


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_erros_do_provedor_viram_llmunavailable(monkeypatch, status):
    monkeypatch.setattr(httpx, "post", fake_post(status=status))
    with pytest.raises(LLMUnavailable):
        LLMClient(cfg()).chat([{"role": "user", "content": "oi"}])


def test_timeout_vira_llmunavailable(monkeypatch):
    monkeypatch.setattr(httpx, "post", fake_post(exc=httpx.ReadTimeout("t")))
    with pytest.raises(LLMUnavailable):
        LLMClient(cfg()).chat([{"role": "user", "content": "oi"}])


def test_sem_chave(monkeypatch):
    with pytest.raises(LLMUnavailable):
        LLMClient(cfg(gemini_api_key="")).chat([{"role": "user", "content": "oi"}])


def test_groq_recebe_response_format(monkeypatch):
    cap = {}
    monkeypatch.setattr(httpx, "post", fake_post(body=OK, capture=cap))
    LLMClient(cfg(), "groq").chat([{"role": "user", "content": "json"}], json_mode=True)
    assert cap["json"]["response_format"] == {"type": "json_object"}


def test_tenta_de_novo_apos_503(monkeypatch):
    cap = {}
    monkeypatch.setattr(httpx, "post", fake_post(capture=cap, roteiro=[(503, SOBRECARGA), (200, OK)]))
    assert LLMClient(cfg(llm_fallback_provider="")).chat([{"role": "user", "content": "oi"}]).text == "ok"
    assert len(cap["chamadas"]) == 2


def test_400_nao_tenta_de_novo(monkeypatch):
    cap = {}
    monkeypatch.setattr(httpx, "post", fake_post(capture=cap, roteiro=[(400, {"error": {"message": "x"}})]))
    with pytest.raises(LLMUnavailable):
        LLMClient(cfg(llm_fallback_provider="")).chat([{"role": "user", "content": "oi"}])
    assert len(cap["chamadas"]) == 1


def test_reserva_groq_quando_gemini_continua_sobrecarregado(monkeypatch):
    cap = {}
    monkeypatch.setattr(httpx, "post", fake_post(capture=cap, roteiro=[(503, SOBRECARGA), (503, SOBRECARGA), (200, OK)]))
    r = LLMClient(cfg(llm_fallback_provider="groq")).chat([{"role": "user", "content": "oi"}])
    assert r.reserva and r.text == "ok"
    assert "googleapis" in cap["chamadas"][0]["url"] and "groq" in cap["chamadas"][2]["url"]


def test_sem_chave_do_reserva_nao_usa_reserva():
    assert LLMClient(cfg(llm_fallback_provider="groq", groq_api_key="")).reserva is None


def test_assinatura_do_gemini_preservada_e_removida_para_o_groq(monkeypatch):
    """Formato real devolvido pelo gemini-flash-latest (diagnóstico de 01/10/2026)."""
    chamada = {"extra_content": {"google": {"thought_signature": "ErAECq0E"}},
               "function": {"arguments": '{"dia":"quarta"}', "name": "consultar_agenda"},
               "id": "call_101505", "type": "function"}
    body = {"choices": [{"message": {"content": None, "tool_calls": [chamada]}}]}
    monkeypatch.setattr(httpx, "post", fake_post(body=body))
    r = LLMClient(cfg()).chat([{"role": "user", "content": "quarta?"}], tools=[{"type": "function"}])
    assert r.tool_calls[0].raw["extra_content"]["google"]["thought_signature"] == "ErAECq0E"
    cap = {}
    monkeypatch.setattr(httpx, "post", fake_post(body=OK, capture=cap))
    hist = [{"role": "assistant", "content": "", "tool_calls": [chamada]}]
    LLMClient(cfg(), "groq").chat(hist)
    assert "extra_content" not in cap["json"]["messages"][0]["tool_calls"][0]
    LLMClient(cfg()).chat(hist)
    assert "extra_content" in cap["json"]["messages"][0]["tool_calls"][0]


def test_429_espera_o_tempo_pedido_pelo_provedor(monkeypatch):
    esperas = []
    monkeypatch.setattr("time.sleep", lambda s: esperas.append(s))
    msg = {"error": {"message": "Rate limit reached ... Please try again in 7.25s. Need more tokens?"}}
    monkeypatch.setattr(httpx, "post", fake_post(roteiro=[(429, msg), (200, OK)]))
    assert LLMClient(cfg(llm_retry_wait_s=1.5)).chat([{"role": "user", "content": "oi"}]).text == "ok"
    assert esperas == [7.75]
