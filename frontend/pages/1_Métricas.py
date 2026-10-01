"""Métricas conversacionais — tudo calculado pelo backend (GET /metrics)."""
import pandas as pd
import plotly.express as px
import streamlit as st

from services import api_client as api
from components.ui import aplicar_estilo

st.set_page_config(page_title="Lia — métricas", page_icon="📊", layout="wide")
aplicar_estilo()

MAGENTA, AMEIXA, CINZA = "#D6135A", "#2B1A3A", "#B7A9C4"


def pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def barras(dados: dict, titulo: str, cor=MAGENTA):
    if not dados:
        st.caption(f"{titulo}: ainda sem dados.")
        return
    df = pd.DataFrame({"categoria": list(dados), "quantidade": list(dados.values())})
    fig = px.bar(df, x="quantidade", y="categoria", orientation="h", title=titulo,
                 color_discrete_sequence=[cor])
    fig.update_layout(height=60 + 34 * len(df), margin=dict(l=0, r=10, t=40, b=0), yaxis_title=None,
                      xaxis_title=None, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=AMEIXA), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch")


st.title("Métricas da Lia")
st.markdown('<p class="lia-sub">Calculadas pelo servidor a partir do log de cada turno. '
            'Um "—" significa que ainda não há dados para a taxa, não que ela é zero.</p>',
            unsafe_allow_html=True)

f1, f2, _ = st.columns([1, 1, 2])
versao = f1.selectbox("Versão do prompt", ["todas", "v1", "v2"])
provedor = f2.selectbox("Provedor", ["todos", "gemini", "groq", "mock"])

try:
    m = api.metricas(None if versao == "todas" else versao, None if provedor == "todos" else provedor)
except api.ApiError as e:
    st.error(e.mensagem)
    st.stop()

if not m["conversas"]:
    st.info("Nenhuma conversa registrada com esses filtros. Converse com a Lia e volte aqui.")
    st.stop()

st.markdown("#### Métricas obrigatórias")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Contenção", pct(m["taxa_contencao"]), help="Conversas resolvidas sem handoff ÷ conversas")
c2.metric("Fallback", pct(m["taxa_fallback"]), help="Turnos em fallback ÷ turnos do usuário",
          delta_color="inverse")
c3.metric("Handoff", pct(m["taxa_handoff"]), help="Conversas transferidas ao professor ÷ conversas")
c4.metric("Mensagens por conversa", m["mensagens_por_conversa"], help="Média de turnos do usuário por sessão")
st.caption(f"{m['conversas']} conversas e {m['turnos_usuario']} turnos do usuário. "
           f"{m['agendamentos_concluidos']} plantões agendados.")

st.markdown("#### Satisfação cruzada com contenção")
csat = m["csat"]
s1, s2, s3 = st.columns(3)
s1.metric("CSAT geral", csat["media_geral"] or "—", help=f"{csat['respostas']} avaliações de 1 a 5")
s2.metric("CSAT sem handoff", csat["media_conversas_contidas"] or "—")
s3.metric("CSAT com handoff", csat["media_conversas_com_handoff"] or "—")
st.caption("Se as conversas contidas tiverem CSAT baixo, a contenção alta pode estar escondendo pessoas que "
           "desistiram sem resolver.")

g1, g2 = st.columns(2)
with g1:
    barras(m["intencoes"], "Intenções detectadas")
    barras(m["fallback_por_motivo"], "Fallback por motivo", AMEIXA)
    barras(m["eventos_guardrail"], "Eventos de guardrail", AMEIXA)
with g2:
    barras(m["nlu_por_origem"], "Quem classificou a intenção", CINZA)
    barras(m["handoff_por_motivo"], "Handoff por motivo")
    barras(m["sentimento"], "Sentimento das mensagens", CINZA)

st.markdown("#### Custo e desempenho")
d1, d2, d3, d4 = st.columns(4)
d1.metric("Latência média", f"{m['latencia_ms']['media']} ms")
d2.metric("Latência p95", f"{m['latencia_ms']['p95']} ms")
d3.metric("Tokens de entrada", m["tokens"]["entrada"])
d4.metric("Tokens de saída", m["tokens"]["saida"])

st.markdown("#### Onde a Lia mais cai em fallback")
if m["ultimos_fallbacks"]:
    st.dataframe(pd.DataFrame({"mensagem (dados pessoais mascarados)": m["ultimos_fallbacks"]}),
                 hide_index=True, width="stretch")
else:
    st.caption("Nenhum fallback registrado.")

st.markdown("#### Comparação A/B por versão do prompt")
linhas = []
for v in ["v1", "v2"]:
    try:
        mv = api.metricas(v, None if provedor == "todos" else provedor)
    except api.ApiError:
        continue
    if mv["conversas"]:
        linhas.append({"versão": v, "conversas": mv["conversas"], "contenção": pct(mv["taxa_contencao"]),
                       "fallback": pct(mv["taxa_fallback"]), "handoff": pct(mv["taxa_handoff"]),
                       "msgs/conversa": mv["mensagens_por_conversa"], "CSAT": mv["csat"]["media_geral"] or "—",
                       "latência média (ms)": mv["latencia_ms"]["media"]})
if linhas:
    st.dataframe(pd.DataFrame(linhas), hide_index=True, width="stretch")
else:
    st.caption("Rode o script scripts/ab_test.py no backend para gerar as duas variantes.")
