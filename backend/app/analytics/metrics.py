"""Métricas conversacionais calculadas a partir do log por turno (Aula 4).

- Contenção  = conversas sem handoff ÷ conversas
- Fallback   = turnos do usuário em fallback ÷ turnos do usuário
- Handoff    = conversas com handoff ÷ conversas
- Msgs/conv. = média de turnos do usuário por sessão
Extras: CSAT cruzado com contenção (o "erro clássico"), FAQ-hit, eventos
de guardrail, latência, tokens (FinOps) e onde o bot mais cai em fallback.
"""
from collections import Counter

from app.memory.store import Store


def _taxa(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def calcular_metricas(store: Store, prompt_version: str | None = None, provider: str | None = None) -> dict:
    filtros, args = [], []
    if prompt_version:
        filtros.append("prompt_version=?")
        args.append(prompt_version)
    if provider:
        filtros.append("provider=?")
        args.append(provider)
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    turnos = store.query(f"SELECT * FROM turn_log {where} ORDER BY id", tuple(args))

    por_sessao: dict[str, list[dict]] = {}
    for t in turnos:
        por_sessao.setdefault(t["session_id"], []).append(t)
    conversas = len(por_sessao)
    com_handoff = {sid for sid, ts in por_sessao.items() if any(t["handoff"] for t in ts)}
    total_turnos = len(turnos)
    fallbacks = [t for t in turnos if t["fallback"]]
    faq_hits = sum(1 for t in turnos if t["route"] == "faq")

    feedback = {r["session_id"]: r["score"] for r in store.query("SELECT session_id, score FROM feedback")}
    fb = {sid: sc for sid, sc in feedback.items() if sid in por_sessao}
    contidas_fb = [sc for sid, sc in fb.items() if sid not in com_handoff]
    handoff_fb = [sc for sid, sc in fb.items() if sid in com_handoff]

    latencias = sorted(t["latency_ms"] for t in turnos)
    concluidos = {t["session_id"] for t in turnos if t["intent"] == "confirmar_agendamento"}

    def media(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "filtros": {"prompt_version": prompt_version, "provider": provider},
        "conversas": conversas,
        "turnos_usuario": total_turnos,
        "taxa_contencao": _taxa(conversas - len(com_handoff), conversas),
        "taxa_fallback": _taxa(len(fallbacks), total_turnos),
        "taxa_handoff": _taxa(len(com_handoff), conversas),
        "mensagens_por_conversa": round(total_turnos / conversas, 2) if conversas else None,
        "taxa_faq_hit": _taxa(faq_hits, total_turnos),
        "agendamentos_concluidos": len(concluidos),
        "handoff_por_motivo": dict(Counter(t["handoff_reason"] for t in turnos if t["handoff"])),
        "fallback_por_motivo": dict(Counter(t["fallback_reason"] for t in fallbacks)),
        "intencoes": dict(Counter(t["intent"] for t in turnos).most_common()),
        "nlu_por_origem": dict(Counter(t["nlu_origem"] for t in turnos)),
        "eventos_guardrail": dict(Counter(t["guardrail"] for t in turnos if t["guardrail"])),
        "sentimento": dict(Counter(t["sentiment_label"] for t in turnos)),
        "latencia_ms": {"media": media(latencias),
                        "p95": latencias[int(0.95 * (len(latencias) - 1))] if latencias else None},
        "tokens": {"entrada": sum(t["prompt_tokens"] or 0 for t in turnos),
                   "saida": sum(t["completion_tokens"] or 0 for t in turnos)},
        "csat": {"respostas": len(fb), "media_geral": media(list(fb.values())),
                 "media_conversas_contidas": media(contidas_fb),
                 "media_conversas_com_handoff": media(handoff_fb)},
        "ultimos_fallbacks": [t["user_text_masked"] for t in fallbacks if t["user_text_masked"]][-10:],
    }
