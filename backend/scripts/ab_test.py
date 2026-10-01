"""Teste A/B da Lia: roda as mesmas conversas em duas variantes, pela API.

Uso (com o backend rodando):
    python scripts/ab_test.py                          # v1 × v2, provedor do .env
    python scripts/ab_test.py --variantes v2 --provedores groq,gemini   # compara modelos
    python scripts/ab_test.py --julgar                 # + LLM-as-judge em cada conversa

Cada variante é uma combinação (versão do prompt, provedor). O script fala com
a API como qualquer cliente (HTTP + X-API-Key), salva as transcrições, calcula
as métricas pelas mesmas definições do /metrics e grava um relatório em
scripts/resultados/. Por padrão cada sessão é apagada ao final (DELETE), o que
libera o horário da agenda para a próxima conversa; use --manter para não apagar.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
load_dotenv(RAIZ / ".env")

from scripts.conversas import CONVERSAS  # noqa: E402

SAIDA = RAIZ / "scripts" / "resultados"


def pegar(d, caminho: str):
    for parte in caminho.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(parte)
    return d


def verificar(conv: dict, turnos: list[dict]) -> list[dict]:
    resultados = []
    for regra in conv["espera"]:
        if regra[0] == "final":
            _, campo, valor = regra
            ok = bool(turnos) and pegar(turnos[-1], campo) == valor
        elif regra[0] == "algum":
            _, campo, valor = regra
            ok = any(pegar(t, campo) == valor for t in turnos)
        else:
            _, i, campo, valor = regra
            ok = i < len(turnos) and pegar(turnos[i], campo) == valor
        resultados.append({"regra": list(regra), "ok": ok})
    return resultados


class Api:
    def __init__(self, url: str, chave: str, pausa: float):
        self.url, self.h, self.pausa = url.rstrip("/"), {"X-API-Key": chave}, pausa

    def post(self, path, body, tentativas=4):
        for n in range(tentativas):
            r = httpx.post(f"{self.url}{path}", json=body, headers=self.h, timeout=60)
            if r.status_code == 503 and n < tentativas - 1:      # limite do plano gratuito: espera e tenta
                espera = 10 * (n + 1)
                print(f"    503 (modelo indisponível), nova tentativa em {espera}s")
                time.sleep(espera)
                continue
            r.raise_for_status()
            return r.json()

    def get(self, path, **params):
        r = httpx.get(f"{self.url}{path}", headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def delete(self, path):
        httpx.delete(f"{self.url}{path}", headers=self.h, timeout=30)


def rodar_conversa(api: Api, conv: dict, versao: str, provedor: str | None) -> dict:
    body = {"objetivo": conv["objetivo"], "prompt_version": versao}
    if provedor:
        body["provider"] = provedor
    sessao = api.post("/sessions", body)
    sid, turnos, erros = sessao["session_id"], [], []
    for msg in conv["mensagens"]:
        try:
            turnos.append(api.post("/chat", {"session_id": sid, "message": msg}))
        except httpx.HTTPStatusError as exc:
            erros.append(f"{msg!r}: HTTP {exc.response.status_code}")
            break
        time.sleep(api.pausa)
    if conv.get("continuar_em_outro_cliente"):
        # T8: um cliente HTTP novo (outra "tela") lê o estado da mesma sessão
        estado = httpx.get(f"{api.url}/sessions/{sid}", headers=api.h, timeout=30).json()
        turnos[-1]["t8_slots_lidos_por_outro_cliente"] = estado["slots"]
    checagens = verificar(conv, turnos)
    return {"id": conv["id"], "titulo": conv["titulo"], "session_id": sid, "saudacao": sessao["greeting"],
            "informativa": conv.get("informativa", False), "erros": erros, "checagens": checagens,
            "transcricao": [{"usuario": m, "lia": t["reply"], "intent": t["intent"], "route": t["route"],
                             "fallback": t["fallback"], "handoff": t["handoff"]["active"],
                             "sentimento": t["sentiment"]["label"], "latency_ms": t["latency_ms"],
                             "nlu_origem": t["nlu_origem"], "guardrail": (t.get("guardrail") or {}).get("tipo")}
                            for m, t in zip(conv["mensagens"], turnos)]}


def resumir(resultados: list[dict]) -> dict:
    turnos = [t for r in resultados for t in r["transcricao"]]
    conversas = len(resultados)
    com_handoff = sum(1 for r in resultados if any(t["handoff"] for t in r["transcricao"]))
    obrig = [c for r in resultados if not r["informativa"] for c in r["checagens"]]
    info = [c for r in resultados if r["informativa"] for c in r["checagens"]]
    lat = sorted(t["latency_ms"] for t in turnos) or [0]
    juiz = [r["juiz"] for r in resultados if r.get("juiz")]

    def media_juiz(campo):
        vals = [j[campo] for j in juiz if isinstance(j.get(campo), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "conversas": conversas, "turnos": len(turnos),
        "taxa_contencao": round((conversas - com_handoff) / conversas, 4) if conversas else None,
        "taxa_fallback": round(sum(t["fallback"] for t in turnos) / len(turnos), 4) if turnos else None,
        "taxa_handoff": round(com_handoff / conversas, 4) if conversas else None,
        "mensagens_por_conversa": round(len(turnos) / conversas, 2) if conversas else None,
        "acerto_roteiro": round(sum(c["ok"] for c in obrig) / len(obrig), 4) if obrig else None,
        "acerto_parafrases": round(sum(c["ok"] for c in info) / len(info), 4) if info else None,
        "nlu_pelo_llm": sum(t["nlu_origem"] == "llm" for t in turnos),
        "latencia_media_ms": round(sum(lat) / len(lat)), "latencia_p95_ms": lat[int(0.95 * (len(lat) - 1))],
        "erros_http": sum(len(r["erros"]) for r in resultados),
        "juiz": {c: media_juiz(c) for c in ["relevancia", "aderencia_ao_papel", "retencao_de_contexto", "clareza",
                                            "fidelidade_a_base"]} if juiz else None,
    }


def relatorio_md(execucao: dict) -> str:
    linhas = [f"# Resultado do teste A/B — {execucao['data']}", "",
              f"Conversas por variante: {execucao['n_conversas']} ({execucao['conversas_ids']}). "
              f"Juiz: {execucao.get('juiz') or 'não executado'}.", "",
              "| Variante | Contenção | Fallback | Handoff | Msgs/conversa | Acerto do roteiro | Acerto em paráfrases "
              "| Latência média | Juiz: relevância | Juiz: papel | Juiz: contexto | Juiz: clareza | Juiz: fidelidade |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def p(x):
        return "—" if x is None else f"{x * 100:.0f}%"

    for nome, v in execucao["variantes"].items():
        r = v["resumo"]
        j = {k: ("—" if x is None else x) for k, x in (r["juiz"] or {}).items()}
        linhas.append(f"| {nome} | {p(r['taxa_contencao'])} | {p(r['taxa_fallback'])} | {p(r['taxa_handoff'])} | "
                      f"{r['mensagens_por_conversa']} | {p(r['acerto_roteiro'])} | {p(r['acerto_parafrases'])} | "
                      f"{r['latencia_media_ms']} ms | {j.get('relevancia', '—')} | {j.get('aderencia_ao_papel', '—')} | "
                      f"{j.get('retencao_de_contexto', '—')} | {j.get('clareza', '—')} | "
                      f"{j.get('fidelidade_a_base', '—')} |")
    linhas += ["", "## Verificações que falharam", ""]
    falhas = False
    for nome, v in execucao["variantes"].items():
        for r in v["conversas"]:
            for c in r["checagens"]:
                if not c["ok"]:
                    falhas = True
                    tag = " (paráfrase, informativa)" if r["informativa"] else ""
                    linhas.append(f"- **{nome} / {r['id']}**{tag}: esperado `{c['regra']}`")
            for e in r["erros"]:
                falhas = True
                linhas.append(f"- **{nome} / {r['id']}**: erro {e}")
    if not falhas:
        linhas.append("Nenhuma.")
    criterios = ["relevancia", "aderencia_ao_papel", "retencao_de_contexto", "clareza", "fidelidade_a_base"]
    baixas = [(nome, r) for nome, v in execucao["variantes"].items() for r in v["conversas"]
              if r.get("juiz") and any(isinstance(r["juiz"].get(c), int) and r["juiz"][c] <= 3 for c in criterios)]
    if any(r.get("juiz") for v in execucao["variantes"].values() for r in v["conversas"]):
        linhas += ["", "## Conversas com alguma nota do juiz ≤ 3", ""]
        if not baixas:
            linhas.append("Nenhuma.")
        for nome, r in baixas:
            j = r["juiz"]
            notas = ", ".join(f"{c} {j[c]}" for c in criterios if isinstance(j.get(c), int) and j[c] <= 3)
            linhas.append(f"- **{nome} / {r['id']}** ({notas}): {j.get('justificativa', '')}"
                          + (f" Trecho: \"{j['pior_trecho']}\"" if j.get("pior_trecho") else ""))
        erros = sum(1 for v in execucao["variantes"].values() for r in v["conversas"] if (r.get("juiz") or {}).get("erro"))
        if erros:
            linhas.append(f"\nAvaliações que falharam (sem nota): {erros}.")
    return "\n".join(linhas) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("API_URL", "http://localhost:8000"))
    ap.add_argument("--chave", default=os.getenv("API_KEY", ""))
    ap.add_argument("--variantes", default="v1,v2", help="versões do prompt, separadas por vírgula")
    ap.add_argument("--provedores", default="", help="ex.: gemini,groq (vazio = o do .env)")
    ap.add_argument("--pausa", type=float, default=2.0, help="segundos entre mensagens (limite do plano gratuito)")
    ap.add_argument("--so", default="", help="rodar só estas conversas, ex.: T1,T5,E2")
    ap.add_argument("--julgar", action="store_true", help="avaliar cada conversa com o LLM-as-judge")
    ap.add_argument("--juiz", default="auto", help="auto = provedor oposto ao do bot; ou gemini, groq, provedor:modelo")
    ap.add_argument("--pausa-juiz", type=float, default=12.0, help="segundos entre avaliações do juiz")
    ap.add_argument("--manter", action="store_true", help="não apagar as sessões ao final")
    a = ap.parse_args()

    api = Api(a.url, a.chave, a.pausa)
    conversas = [c for c in CONVERSAS if not a.so or c["id"] in a.so.split(",")]
    provedores = [p for p in a.provedores.split(",") if p] or [None]
    juiz = None
    if a.julgar:
        from scripts.llm_judge import Juiz
        juiz = Juiz(a.juiz)
        print(f"Juiz: {juiz.descricao}")

    execucao = {"data": datetime.now().strftime("%Y-%m-%d %H:%M"), "api": a.url, "n_conversas": len(conversas),
                "conversas_ids": ", ".join(c["id"] for c in conversas),
                "juiz": juiz.descricao if juiz else None, "variantes": {}}
    for versao in a.variantes.split(","):
        for provedor in provedores:
            nome = versao + (f"+{provedor}" if provedor else "")
            print(f"\n=== Variante {nome} ===")
            resultados = []
            for conv in conversas:
                r = rodar_conversa(api, conv, versao, provedor)
                if juiz:
                    r["juiz"] = juiz.avaliar(r)
                    time.sleep(a.pausa_juiz)
                ok = sum(c["ok"] for c in r["checagens"])
                print(f"  {r['id']:<4} {ok}/{len(r['checagens'])} verificações"
                      + (f" | juiz {r['juiz']}" if juiz else "") + (f" | ERROS {r['erros']}" if r["erros"] else ""))
                if not a.manter:
                    api.delete(f"/sessions/{r['session_id']}")
                resultados.append(r)
            execucao["variantes"][nome] = {"resumo": resumir(resultados), "conversas": resultados}

    SAIDA.mkdir(exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M")
    (SAIDA / f"ab_{carimbo}.json").write_text(json.dumps(execucao, ensure_ascii=False, indent=2), encoding="utf-8")
    (SAIDA / f"ab_{carimbo}.md").write_text(relatorio_md(execucao), encoding="utf-8")
    print("\n" + relatorio_md(execucao))
    print(f"Arquivos: scripts/resultados/ab_{carimbo}.json e .md")


if __name__ == "__main__":
    main()
