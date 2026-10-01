"""Lia — lente em Streamlit.

Esta tela só guarda o session_id (na URL e no session_state). O histórico,
os slots e o handoff vêm SEMPRE do backend (GET /sessions/{id}): recarregar a
página ou abrir o link em outro navegador continua a mesma conversa.

Executar:  streamlit run app.py
"""
import streamlit as st

from services import api_client as api
from components.ui import OBJETIVOS, aplicar_estilo, raio_x

st.set_page_config(page_title="Lia — oficina de chatbots", page_icon="💬", layout="wide")
aplicar_estilo()


# --- sessão: a tela só conhece o id ------------------------------------
def sid_atual() -> str | None:
    return st.query_params.get("sid") or st.session_state.get("sid")


def definir_sid(sid: str | None) -> None:
    st.session_state.pop("turno", None)
    st.session_state.pop("csat_enviado", None)
    if sid:
        st.session_state["sid"] = sid
        st.query_params["sid"] = sid
    else:
        st.session_state.pop("sid", None)
        st.query_params.clear()


def mostrar_erro(e: api.ApiError) -> None:
    st.error(e.mensagem)
    if e.detalhe:
        with st.expander("Detalhe técnico"):
            st.code(e.detalhe)


# --- barra lateral --------------------------------------------------------
with st.sidebar:
    st.markdown("### Conexão com a API")
    try:
        h = api.health()
        st.success(f"Online. Modelo {h['model']} ({h['provider']}), prompt {h['prompt_version']}.")
    except api.ApiError as e:
        st.error(e.mensagem)
    st.toggle("Resposta em streaming", value=True, key="streaming",
              help="Recebe a resposta em pedaços pela rota /chat/stream.")
    sid = sid_atual()
    if sid:
        st.caption(f"Sessão `{sid}`. Abra este mesmo endereço em outra aba para continuar a conversa.")
        if st.button("Nova conversa", width="stretch"):
            definir_sid(None)
            st.rerun()
        if st.button("Apagar meus dados", width="stretch",
                     help="Remove esta conversa do servidor (direito ao esquecimento, LGPD)."):
            try:
                api.apagar_sessao(sid)
                definir_sid(None)
                st.toast("Conversa apagada do servidor.")
                st.rerun()
            except api.ApiError as e:
                mostrar_erro(e)

st.title("Lia")
st.markdown('<p class="lia-sub">Assistente virtual da oficina de chatbots. Tira dúvidas sobre os temas da aula, '
            'agenda plantão com o professor e chama o professor quando você precisar.</p>',
            unsafe_allow_html=True)

# --- tela inicial: escolher o objetivo ----------------------------------
sid = sid_atual()
if not sid:
    st.markdown("#### O que você quer aprender hoje?")
    escolha = st.pills("Objetivo de aprendizagem", options=list(OBJETIVOS), format_func=OBJETIVOS.get,
                       label_visibility="collapsed")
    st.caption("Escolher um objetivo é opcional. A Lia usa esse dado como um slot da conversa.")
    if st.button("Começar conversa", type="primary"):
        try:
            nova = api.criar_sessao(escolha)
            definir_sid(nova["session_id"])
            st.rerun()
        except api.ApiError as e:
            mostrar_erro(e)
    st.stop()

# --- conversa: tudo vem do servidor -------------------------------------
try:
    sessao = api.obter_sessao(sid)
except api.ApiError as e:
    mostrar_erro(e)
    if st.button("Começar nova conversa", type="primary"):
        definir_sid(None)
        st.rerun()
    st.stop()

col_chat, col_raio = st.columns([3, 2], gap="large")

with col_raio:
    raio_x(st.session_state.get("turno"), sessao)

with col_chat:
    for m in sessao["history"]:
        with st.chat_message("assistant" if m["role"] == "assistant" else "user",
                             avatar="💬" if m["role"] == "assistant" else None):
            st.markdown(m["content"])

    em_handoff = sessao["handoff"]["active"]
    if em_handoff:
        st.markdown('<div class="nota-handoff">Sua conversa está com o professor. Ele recebeu o resumo ao lado '
                    'e vai continuar o atendimento. Para falar de outro assunto, comece uma nova conversa.</div>',
                    unsafe_allow_html=True)

    mensagem = st.chat_input("Escreva sua mensagem (até 500 caracteres)", max_chars=500, disabled=em_handoff)
    if mensagem:
        with st.chat_message("user"):
            st.markdown(mensagem)
        with st.chat_message("assistant", avatar="💬"):
            try:
                if st.session_state.get("streaming"):
                    destino: dict = {}
                    st.write_stream(api.enviar_stream(sid, mensagem, destino))
                    st.session_state["turno"] = destino.get("turno")
                else:
                    with st.spinner("A Lia está pensando…"):
                        turno = api.enviar(sid, mensagem)
                    st.markdown(turno["reply"])
                    st.session_state["turno"] = turno
            except api.ApiError as e:
                mostrar_erro(e)
                st.stop()
        st.rerun()   # recarrega histórico e estado a partir do servidor

    if sessao["turn"] >= 1:
        st.divider()
        st.markdown("**Como foi a conversa até aqui?**")
        nota = st.feedback("stars", key=f"csat_{sid}")
        if nota is not None and st.session_state.get("csat_enviado") != nota:
            try:
                api.feedback(sid, nota + 1)
                st.session_state["csat_enviado"] = nota
                st.toast("Avaliação registrada. Obrigada!")
            except api.ApiError as e:
                mostrar_erro(e)
