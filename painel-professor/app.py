"""Painel do professor — segunda lente da Lia, em Gradio.

Consome a MESMA API que o chat em Streamlit (GET /handoffs, GET /sessions/{id},
PATCH /handoffs/{id}). Nenhuma lógica do bot foi duplicada: é a prova de que
o cérebro é um serviço e cada tela é só uma lente.

Executar:  python app.py   (abre em http://localhost:7860)
"""
import os
from pathlib import Path

import gradio as gr
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
HEADERS = {"X-API-Key": os.getenv("API_KEY", "")}

MOTIVOS = {"pedido_explicito": "Pediu o professor", "falha_repetida": "A Lia não conseguiu ajudar",
           "frustracao": "Aluno frustrado", "tema_sensivel": "Tema sensível"}
STATUS = {"pendente": "Aguardando", "em_atendimento": "Em atendimento", "resolvido": "Resolvido"}


def _api(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=HEADERS, timeout=15, **kw)
    except httpx.HTTPError:
        raise gr.Error(f"A API não respondeu em {API_URL}. Confira se o backend está rodando.")
    if r.status_code == 401:
        raise gr.Error("A API recusou a chave. Confira API_KEY no .env do painel.")
    if r.status_code >= 400:
        raise gr.Error(r.json().get("detail", "Erro na API."))
    return r.json()


def carregar_fila(filtro: str):
    status = {"Aguardando": "pendente", "Em atendimento": "em_atendimento", "Resolvidos": "resolvido"}.get(filtro)
    itens = _api("GET", "/handoffs", params={"status": status} if status else None)
    linhas = [[("Alta" if i["urgencia"] == "alta" else "Normal"), MOTIVOS.get(i["motivo"], i["motivo"]),
               STATUS.get(i["status"], i["status"]), i["criado_em"][11:16], i["session_id"]] for i in itens]
    escolhas = [(f"{MOTIVOS.get(i['motivo'], i['motivo'])} ({i['session_id']})", i["session_id"]) for i in itens]
    aviso = f"{len(itens)} conversa(s) na fila." if itens else "Nenhuma conversa na fila. Quando a Lia transferir alguém, aparece aqui."
    return linhas, gr.update(choices=escolhas, value=escolhas[0][1] if escolhas else None), aviso


def abrir(session_id: str | None):
    if not session_id:
        return "Escolha uma conversa da fila.", []
    s = _api("GET", f"/sessions/{session_id}")
    r = s["handoff"]["summary"] or {}
    dados = r.get("dados_coletados", {})
    relato = "\n".join(f"> {t}" for t in r.get("relato", []))
    acoes = "\n".join(f"- {a}" for a in r.get("acoes_do_bot", []))
    ficha = (f"### {MOTIVOS.get(r.get('motivo'), r.get('motivo', ''))}\n"
             f"**Urgência:** {r.get('urgencia', '—')}. **Sentimento:** {r.get('sentimento', '—')}. "
             f"**Objetivo:** {r.get('objetivo', '—')}. **Status:** {STATUS.get(s['handoff_status'], '—')}.\n\n"
             f"**Aluno:** {dados.get('nome', 'não informado')}"
             f"{', RM ' + dados['rm'] if dados.get('rm') else ''}"
             f"{', ' + dados['email'] if dados.get('email') else ''}\n\n"
             f"**O que o aluno disse por último**\n\n{relato}\n\n**O que a Lia já fez**\n\n{acoes}\n\n"
             f"_{r.get('observacao', '')}_")
    historico = [{"role": m["role"], "content": m["content"]} for m in s["history"]]
    return ficha, historico


def mudar_status(session_id: str | None, status: str, filtro: str):
    if not session_id:
        raise gr.Error("Escolha uma conversa da fila primeiro.")
    _api("PATCH", f"/handoffs/{session_id}", json={"status": status})
    gr.Info("Resolvido. A Lia volta a atender esse aluno." if status == "resolvido" else "Você assumiu o atendimento.")
    linhas, _, aviso = carregar_fila(filtro)
    ficha, hist = abrir(session_id)
    return linhas, aviso, ficha, hist


CSS = """
.gradio-container { max-width: 1200px !important; }
#titulo h1 { margin-bottom: 0; }
"""

with gr.Blocks(title="Lia — painel do professor") as demo:
    gr.Markdown("# Painel do professor\nConversas que a Lia transferiu para você, com o resumo do que já aconteceu. "
                "Urgentes aparecem primeiro.", elem_id="titulo")
    with gr.Row():
        filtro = gr.Radio(["Todas", "Aguardando", "Em atendimento", "Resolvidos"], value="Aguardando",
                          label="Mostrar")
        atualizar = gr.Button("Atualizar fila", variant="secondary", scale=0)
    aviso = gr.Markdown()
    fila = gr.Dataframe(headers=["Urgência", "Motivo", "Status", "Hora (UTC)", "Sessão"], interactive=False,
                        wrap=True)
    with gr.Row():
        with gr.Column(scale=2):
            escolha = gr.Dropdown(label="Conversa", choices=[])
            ficha = gr.Markdown()
            with gr.Row():
                assumir = gr.Button("Assumir atendimento")
                resolver = gr.Button("Marcar como resolvido", variant="primary")
        with gr.Column(scale=3):
            historico = gr.Chatbot(label="Conversa completa", height=460)

    demo.load(carregar_fila, filtro, [fila, escolha, aviso])
    atualizar.click(carregar_fila, filtro, [fila, escolha, aviso])
    filtro.change(carregar_fila, filtro, [fila, escolha, aviso])
    escolha.change(abrir, escolha, [ficha, historico])
    assumir.click(lambda s, f: mudar_status(s, "em_atendimento", f), [escolha, filtro], [fila, aviso, ficha, historico])
    resolver.click(lambda s, f: mudar_status(s, "resolvido", f), [escolha, filtro], [fila, aviso, ficha, historico])
    # atualização automática só da tabela (não troca a conversa aberta)
    gr.Timer(15).tick(lambda f: (lambda r: (r[0], r[2]))(carregar_fila(f)), filtro, [fila, aviso])

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft(primary_hue="pink", neutral_hue="slate",
                                     font=gr.themes.GoogleFont("Atkinson Hyperlegible")), css=CSS)
