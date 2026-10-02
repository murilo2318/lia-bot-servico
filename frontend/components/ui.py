"""Estilo e componentes visuais. Só apresentação: nenhuma regra do bot mora aqui."""
import html

import streamlit as st

OBJETIVOS = {
    "design": "Design conversacional",
    "system-prompt": "System prompt",
    "memory-state": "Memória e estado",
    "guardrail": "Guardrails e handoff",
    "faq": "Base de conhecimento",
}

# As etapas do orquestrador, na ordem em que o backend decide cada turno.
ETAPAS = [
    ("guardrail", "Guardrail de entrada", "bloqueia injection, pedido indevido e texto longo"),
    ("oferta", "Oferta do professor", "resposta a um 'quer que eu chame o professor?'"),
    ("handoff", "Handoff por regra", "pedido explícito, tema sensível ou frustração"),
    ("fluxo", "Fluxo de agendamento", "captura e valida um dado do plantão"),
    ("intencao", "Intenção", "FAQ, agenda, saudação — por regra ou pelo LLM"),
    ("fallback", "Fallback", "não entendeu, fora da base ou fora do escopo"),
]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
/* Fonte só nos elementos de texto. Aplicar a [class*="st-"] trocava também a fonte dos ícones do
   Streamlit (Material Symbols), que passavam a aparecer como palavras: "face", "star", "arrow_down". */
html, body, .stApp, .stMarkdown, p, li, label, input, textarea, h1, h2, h3, h4, h5, h6,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stCaptionContainer"] {
  font-family: 'Atkinson Hyperlegible', system-ui, sans-serif;
}
[data-testid="stIconMaterial"], [class*="material-symbols"], [class*="material-icons"] {
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
}
h1 { font-size: 2.1rem; letter-spacing: -0.01em; margin-bottom: 0.1rem; }
.lia-sub { color: #6B5A7B; margin-top: 0; max-width: 60ch; }
.trilha { list-style: none; padding: 0; margin: 0.4rem 0 1rem; counter-reset: etapa; }
.trilha li { display: grid; grid-template-columns: 1.8rem 1fr; gap: 0.1rem 0.5rem;
  padding: 0.35rem 0; color: #9A8CA8; border-left: 2px solid #E4DDEA; padding-left: 0.6rem; }
.trilha li::before { counter-increment: etapa; content: counter(etapa); grid-row: span 2;
  font-weight: 700; width: 1.5rem; height: 1.5rem; border-radius: 50%; display: grid;
  place-items: center; font-size: 0.8rem; background: #EFEAF3; color: #9A8CA8; }
.trilha li small { grid-column: 2; font-size: 0.78rem; }
.trilha li.passou { color: #6B5A7B; }
.trilha li.decidiu { color: #2B1A3A; border-left-color: #D6135A; font-weight: 700; }
.trilha li.decidiu::before { background: #D6135A; color: #fff; }
.trilha li.decidiu small { font-weight: 400; color: #5A4868; }
.nota-handoff { border-left: 3px solid #D6135A; background: #FFF0F5; padding: 0.7rem 0.9rem;
  border-radius: 0 6px 6px 0; margin: 0.5rem 0; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
"""


def aplicar_estilo() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def etapa_decisiva(turno: dict) -> str:
    rota, intent = turno.get("route"), turno.get("intent")
    if rota == "guardrail":
        return "guardrail"
    if intent == "recusou_handoff" or (rota == "handoff" and turno["handoff"].get("reason") == "falha_repetida"):
        return "oferta"
    if rota == "handoff":
        return "handoff"
    if rota == "fluxo" or intent in {"confirmar_agendamento", "dado_invalido"}:
        return "fluxo"
    if rota == "fallback":
        return "fallback"
    return "intencao"


def trilha_decisao(turno: dict) -> None:
    alvo = etapa_decisiva(turno)
    chaves = [e[0] for e in ETAPAS]
    idx = chaves.index(alvo)
    itens = []
    for i, (chave, nome, ajuda) in enumerate(ETAPAS):
        classe = "decidiu" if i == idx else ("passou" if i < idx else "")
        itens.append(f'<li class="{classe}">{html.escape(nome)}<small>{html.escape(ajuda)}</small></li>')
    st.markdown(f'<ol class="trilha">{"".join(itens)}</ol>', unsafe_allow_html=True)


ROTULO_SENT = {"negativo": "Negativo", "neutro": "Neutro", "positivo": "Positivo"}
ORIGEM = {"regra": "regra", "llm": "LLM", "memoria": "memória"}


def raio_x(turno: dict | None, sessao: dict | None) -> None:
    st.subheader("Raio-X")
    if turno:
        st.caption("Por que a Lia respondeu assim no último turno")
        trilha_decisao(turno)
        c1, c2 = st.columns(2)
        c1.metric("Intenção", turno["intent"], help=f"Detectada por {ORIGEM.get(turno['nlu_origem'], turno['nlu_origem'])}")
        c2.metric("Sentimento", ROTULO_SENT[turno["sentiment"]["label"]], f"{turno['sentiment']['score']:.2f}",
                  delta_color="off")
        c3, c4 = st.columns(2)
        c3.metric("Fallback", "sim" if turno["fallback"] else "não")
        c4.metric("Latência", f"{turno['latency_ms']} ms")
        detalhes = []
        if turno.get("texto_interpretado"):
            detalhes.append(f"Entendido como: “{turno['texto_interpretado']}”")
        if turno.get("faq_id"):
            detalhes.append(f"FAQ consultada: `{turno['faq_id']}`")
        if turno.get("used_memory"):
            detalhes.append("Usou a memória da conversa")
        if turno.get("guardrail") and turno["guardrail"].get("tipo"):
            detalhes.append(f"Guardrail acionado: `{turno['guardrail']['tipo']}`")
        detalhes.append(f"Modelo: `{turno['model']}`")
        st.markdown("  \n".join(detalhes))
    else:
        st.caption("Envie uma mensagem para ver como a Lia decide cada resposta.")

    if sessao:
        st.markdown("**Estado guardado no servidor**")
        slots = {k: (v["rotulo"] if isinstance(v, dict) else v) for k, v in sessao["slots"].items()}
        if slots.get("objetivo"):
            slots["objetivo"] = OBJETIVOS.get(slots["objetivo"], slots["objetivo"])
        linhas = [{"slot": k, "valor": v or "—"} for k, v in slots.items()]
        st.dataframe(linhas, hide_index=True, width="stretch")
        etapa = sessao.get("etapa")
        st.caption(f"Etapa do fluxo: {etapa.split(':')[1] if etapa else 'nenhuma'}. Turnos: {sessao['turn']}.")
        if sessao["handoff"]["active"]:
            with st.expander("Resumo entregue ao professor", expanded=True):
                st.json(sessao["handoff"]["summary"])
        if sessao.get("resumo"):
            with st.expander("Resumo rolante da conversa"):
                st.write(sessao["resumo"])
