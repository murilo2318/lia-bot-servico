"""Mede, com o modelo REAL, quanto a Lia entende mensagens com erros de digitação e paráfrases.

Uso (backend rodando):  python scripts/robustez.py
Cada frase vira uma sessão nova; o script compara a intenção (e a FAQ) que a API devolveu com a
esperada e grava o relatório em scripts/resultados/robustez_<data>.md. Gasta cerca de uma chamada
de LLM por frase que as regras não resolvem.
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
load_dotenv(RAIZ / ".env")

# (frase, intenção esperada, faq esperada ou None, grupo)
CASOS = [
    # erros de digitação (as regras com correção devem resolver; o LLM cobre o resto)
    ("oq e slto msm", "faq", "slot-state", "digitação"),
    ("como evitr q o bot invete resposta", "faq", "knowledge-base", "digitação"),
    ("quero fala cm o profssor", "falar_professor", None, "digitação"),
    ("quero agendr um plantao", "agendar_plantao", None, "digitação"),
    ("quais horaios tem quarta", "disponibilidade", None, "digitação"),
    ("nao to entedendo essa parte", "confusao", None, "digitação"),
    ("o que é guardrial", "faq", "guardrails-handoff", "digitação"),
    ("quero cancleat meu plantao", "cancelar", None, "digitação"),
    ("como funsiona a memorya do bot", "faq", "memory-window", "digitação"),
    ("quais metricas devu acompanha", "faq", "future-metrics", "digitação"),
    # paráfrases que nenhuma regra conhece (dependem do LLM)
    ("como faço o bot lembrar o que o aluno falou lá atrás?", "faq", "memory-window", "paráfrase"),
    ("tem como saber se meu bot tá indo bem?", "faq", "future-metrics", "paráfrase"),
    ("qual a parte mais importante pra começar a montar o bot?", "faq", "happy-path", "paráfrase"),
    ("o bot pode responder qualquer coisa que o modelo quiser?", "faq", "rule-versus-llm", "paráfrase"),
    ("como deixo meu bot mais seguro contra usuário mal intencionado?", "faq", "guardrails-handoff", "paráfrase"),
    ("posso tirar dúvida pessoalmente com alguém da oficina?", "agendar_plantao", None, "paráfrase"),
    ("me tira dessa conversa com robô, quero gente", "falar_professor", None, "paráfrase"),
    ("tô me sentindo muito mal com essa entrega, tá pesado demais", "tema_sensivel", None, "paráfrase"),
    ("que dias o professor atende?", "disponibilidade", None, "paráfrase"),
    ("você consegue me mostrar o que sabe responder?", "capacidades", None, "paráfrase"),
    # mistura de saudação/agradecimento com pergunta
    ("vlw mas e a memoria", "faq", "memory-window", "mistura"),
    ("oi me explica guardrail", "faq", "guardrails-handoff", "mistura"),
    ("bom dia, o que é rag?", "faq", "knowledge-base", "mistura"),
]


def main():
    url = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
    h = {"X-API-Key": os.getenv("API_KEY", "")}
    linhas, por_grupo = [], {}
    for frase, intent, faq, grupo in CASOS:
        sid = httpx.post(f"{url}/sessions", json={}, headers=h, timeout=30).json()["session_id"]
        r = httpx.post(f"{url}/chat", json={"session_id": sid, "message": frase}, headers=h, timeout=60).json()
        ok = r["intent"] == intent or (intent == "falar_professor" and r["handoff"]["active"]) \
            or (intent == "tema_sensivel" and r["handoff"]["active"]) \
            or (intent == "faq" and r.get("intent") == "esclarecimento" and r.get("faq_id") == faq)
        if faq and r["intent"] in {"faq", "continuacao"}:
            ok = ok and r.get("faq_id") == faq
        por_grupo.setdefault(grupo, []).append(ok)
        linhas.append(f"| {'✅' if ok else '❌'} | {grupo} | {frase} | {intent}{' / ' + faq if faq else ''} | "
                      f"{r['intent']}{' / ' + r['faq_id'] if r.get('faq_id') else ''} | {r['nlu_origem']} | "
                      f"{r.get('texto_interpretado') or ''} |")
        print(linhas[-1])
        httpx.delete(f"{url}/sessions/{sid}", headers=h, timeout=30)
        time.sleep(1.0)
    resumo = [f"- **{g}**: {sum(v)}/{len(v)} ({sum(v) / len(v):.0%})" for g, v in por_grupo.items()]
    total = [x for v in por_grupo.values() for x in v]
    md = "\n".join([f"# Robustez — {datetime.now():%Y-%m-%d %H:%M}", "",
                    f"**Total: {sum(total)}/{len(total)} ({sum(total) / len(total):.0%})**", "", *resumo, "",
                    "| | Grupo | Mensagem | Esperado | Obtido | Origem | Entendido como |",
                    "|---|---|---|---|---|---|---|", *linhas, ""])
    saida = RAIZ / "scripts" / "resultados"
    saida.mkdir(exist_ok=True)
    arq = saida / f"robustez_{datetime.now():%Y%m%d_%H%M}.md"
    arq.write_text(md, encoding="utf-8")
    print("\n" + "\n".join(md.splitlines()[:8]) + f"\n\nRelatório: scripts/resultados/{arq.name}")


if __name__ == "__main__":
    sys.exit(main())
