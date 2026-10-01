"""Diagnóstico do provedor: testa texto, JSON e function calling e mostra a resposta crua.

Uso:
    python scripts/diagnostico_llm.py                              # Gemini, modelo do .env
    python scripts/diagnostico_llm.py gemini gemini-3.5-flash-lite # Gemini, outro modelo
    python scripts/diagnostico_llm.py groq                         # Groq, modelo do .env
    python scripts/diagnostico_llm.py modelos                      # lista os modelos que a chave enxerga
Útil antes de gravar a demo: 503 "high demand" = provedor sobrecarregado; 429 = cota estourada.
"""
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.llm.client import ENDPOINTS  # noqa: E402

s = get_settings()
args = sys.argv[1:]

if args[:1] == ["modelos"]:
    g = httpx.get("https://generativelanguage.googleapis.com/v1beta/openai/models",
                  headers={"Authorization": f"Bearer {s.gemini_api_key}"}, timeout=30).json()
    print("GEMINI:", sorted(m["id"].split("/")[-1] for m in g.get("data", []) if "flash" in m["id"]))
    q = httpx.get("https://api.groq.com/openai/v1/models",
                  headers={"Authorization": f"Bearer {s.groq_api_key}"}, timeout=30).json()
    print("GROQ:", sorted(m["id"] for m in q.get("data", [])))
    sys.exit()

PROV = args[0] if args else "gemini"
CHAVE = s.gemini_api_key if PROV == "gemini" else s.groq_api_key
MODELO = args[1] if len(args) > 1 else (s.gemini_model if PROV == "gemini" else s.groq_model)
TOOLS = [{"type": "function", "function": {
    "name": "consultar_agenda", "description": "Lista horários livres de plantão",
    "parameters": {"type": "object", "properties": {"dia": {"type": "string"}}}}}]
resultado = {}


def chamar(nome, payload):
    t0 = time.perf_counter()
    r = httpx.post(ENDPOINTS[PROV], json={"model": MODELO, **payload}, timeout=40,
                   headers={"Authorization": f"Bearer {CHAVE}"})
    ms = int((time.perf_counter() - t0) * 1000)
    resultado[nome] = r.status_code
    print(f"\n== {nome}: HTTP {r.status_code} em {ms} ms")
    if r.status_code == 200:
        m = r.json()["choices"][0]["message"]
        print("content:", repr(m.get("content"))[:200])
        print("tool_calls:", "sim" if m.get("tool_calls") else "não")
        return m
    texto = r.text
    cota = re.search(r"limit: (\d+)", texto)
    print(f"COTA ESTOURADA: limite de {cota.group(1)} requisições" if cota else texto[:400])


print(f"provedor: {PROV} | modelo: {MODELO} | chave: {'ok' if CHAVE else 'AUSENTE'}")
chamar("1_texto", {"messages": [{"role": "user", "content": "Responda só: oi"}]})
chamar("2_json_no_texto", {"messages": [{"role": "user", "content": 'Responda SOMENTE com o JSON {"intent": "faq"}'}]})
m = chamar("3_ferramenta", {"messages": [{"role": "user", "content": "Quais horários de plantão tem na quarta?"}],
                            "tools": TOOLS, "tool_choice": "auto"})
if m and m.get("tool_calls"):
    call = m["tool_calls"][0]
    chamar("4_resultado_ferramenta", {"messages": [
        {"role": "user", "content": "Quais horários de plantão tem na quarta?"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call.get("id", "call_0"),
         "content": '{"horarios": [{"rotulo": "quarta 07/10 às 17h30"}]}'}]})
ok = all(v == 200 for v in resultado.values()) and "4_resultado_ferramenta" in resultado
print(f"\nVEREDITO {PROV}/{MODELO}: " + ("serve para a Lia (texto, JSON e ferramentas ok)" if ok
      else "NÃO serve agora — veja os códigos acima"))
