"""Gerenciador de diálogo da Lia — o "maestro" da arquitetura de referência.

Ordem de decisão por turno (evolução da ordem do Build Day
"validação → handoff → FAQ → Gemini → fallback"):

  1. guardrail de entrada (tamanho, prompt injection, pedido indevido)
  2. resposta a uma oferta de handoff pendente
  3. handoff por regra (pedido explícito, tema sensível, frustração)
  4. fluxo transacional em andamento (agendamento de plantão)
  5. intenção (regras → LLM só se nada casar)
  6. fallback com opções (2ª falha seguida → oferta do professor)

O LLM conversa e entende; o código decide tudo o que não pode errar.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.config import BASE_DIR, Settings
from app.knowledge import agenda
from app.knowledge.faq import FaqEntry, get_faq
from app.llm.client import LLMClient, LLMUnavailable
from app.memory.store import Session, Store, agora
from app.nlp import validators as v
from app.nlp.guardrails import checar_entrada, checar_saida
from app.nlp.nlu import NLUResult, classificar_por_llm, classificar_por_regras
from app.nlp.sentiment import Sentimento, analisar_sentimento
from app.nlp.text import contains_phrase, normalize_text

OBJETIVOS = {
    "design": "design conversacional",
    "system-prompt": "system prompt",
    "memory-state": "memória e estado",
    "guardrail": "guardrails e handoff",
    "faq": "base de conhecimento",
}
OBJETIVO_KEYWORDS = {
    "design": ["design", "conversacional", "grice", "persona"],
    "system-prompt": ["system prompt", "prompt"],
    "memory-state": ["memoria", "estado", "slot", "slots", "janela"],
    "guardrail": ["guardrail", "guardrails", "handoff", "seguranca"],
    "faq": ["faq", "base de conhecimento", "base", "conhecimento", "rag"],
}

MENU = ("(1) tirar dúvidas sobre os temas da oficina, (2) agendar um plantão de dúvidas "
        "com o professor ou (3) chamar o professor")

COPY = {
    "vazio": "Escreva uma pergunta para eu poder ajudar.",
    "tamanho": "Sua mensagem passou de {max} caracteres. Resuma a pergunta e tente novamente.",
    "prompt_injection": ("Isso eu não posso fazer: minhas instruções são fixas e ficam no servidor. "
                         "Se quiser, te explico como um bot se protege contra esse tipo de ataque."),
    "pedido_indevido": ("Não faço a atividade por você, mas posso te ajudar a entender o conceito por trás "
                        "dela. Qual parte está travando?"),
    "fallback_nao_entendi": "Não tenho certeza de que entendi. Posso te ajudar a {menu}. O que você precisa?",
    "fallback_fora_da_base": ("Não tenho essa informação na base da oficina, e prefiro não chutar. "
                              "Posso te ajudar a {menu}. Qual caminho prefere?"),
    "fallback_fora_escopo": ("Esse assunto foge do que eu faço: sou a assistente da oficina de chatbots. "
                             "Posso te ajudar a {menu}."),
    "oferta_handoff": ("Ainda não consegui entender o que você precisa. Quer que eu passe sua dúvida para o "
                       "professor, com o resumo da nossa conversa? Responda sim ou não."),
    "acolhimento": "Entendo, isso pode ser frustrante. ",
    "handoff_ativo": ("Sua conversa já está na fila do professor, com o resumo do que falamos. "
                      "Assim que ele assumir, você não vai precisar repetir nada."),
}


EXPLICA_ETAPA = {
    "nome": "Preciso do seu nome e sobrenome para o professor saber com quem vai conversar. Como você se chama?",
    "rm": "O RM é o seu número de matrícula na FIAP, com 5 ou 6 dígitos (ex.: 562358). Qual é o seu?",
    "email": "Preciso de um e-mail para enviar o link da sala do plantão, no formato nome@dominio.com. Qual posso usar?",
    "objetivo": "É o assunto da sua dúvida, para o professor se preparar. Pode ser, por exemplo, memória e estado.",
    "horario": "É só escolher um dos horários que mostrei, respondendo 1, 2 ou 3.",
    "confirmar": "Estou só conferindo se os dados estão certos antes de reservar. Posso confirmar?",
    "corrigir": "Me diga qual dado você quer trocar: nome, RM, e-mail, tema ou horário.",
}

log = logging.getLogger("lia")

ATIVO = {"pendente", "em_atendimento"}   # handoff trava o bot até o professor resolver


class SessionNotFound(Exception):
    pass


@dataclass
class TurnResult:
    session_id: str
    reply: str
    intent: str
    route: str
    slots: dict
    sentiment: dict
    fallback: bool
    handoff: dict
    turn: int
    latency_ms: int
    faq_id: str | None = None
    used_memory: bool = False
    guardrail: dict | None = None
    etapa: str | None = None
    model: str = ""
    nlu_origem: str = "regra"


@dataclass
class _Turno:
    """Rascunho do turno, preenchido pelas etapas do orquestrador."""
    texto: str
    sent: Sentimento
    intent: str = "nao_entendi"
    route: str = "fallback"
    reply: str = ""
    faq_id: str | None = None
    used_memory: bool = False
    fallback_reason: str | None = None
    handoff_reason: str | None = None
    guardrail: dict | None = None
    nlu_origem: str = "regra"
    llm_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    acoes: list = field(default_factory=list)
    modelo: str | None = None       # modelo que respondeu de fato (pode ser o reserva)


def carregar_prompt(versao: str) -> str:
    arquivo = "system_prompt.md" if versao == "v2" else "system_prompt_v1_buildday.md"
    return (BASE_DIR / "prompts" / arquivo).read_text(encoding="utf-8")


class Orchestrator:
    def __init__(self, settings: Settings, store: Store):
        self.cfg = settings
        self.store = store

    def llm_for(self, s: Session) -> LLMClient:
        return LLMClient(self.cfg, s.provider)

    # ------------------------------------------------------------------
    def saudacao(self, objetivo: str | None) -> str:
        base = ("Oi! Eu sou a Lia, assistente virtual da oficina de chatbots. Posso tirar dúvidas sobre os "
                "temas da aula, agendar um plantão de dúvidas com o professor ou chamar o professor quando "
                "você precisar. Não faço a atividade por você, mas te ajudo a entender cada parte.")
        if objetivo in OBJETIVOS:
            return f"{base} Vi que seu objetivo é {OBJETIVOS[objetivo]}. Qual é a sua dúvida?"
        return f"{base} Por onde quer começar?"

    def criar_sessao(self, objetivo: str | None, prompt_version: str | None, provider: str | None):
        client = LLMClient(self.cfg, provider)
        s = self.store.create_session(prompt_version or self.cfg.prompt_version, client.provider,
                                      client.model, objetivo)
        texto = self.saudacao(objetivo)
        self.store.add_message(s.id, "assistant", texto)
        return s, texto

    # ------------------------------------------------------------------
    def processar(self, session_id: str, mensagem: str) -> TurnResult:
        inicio = time.perf_counter()
        s = self.store.get_session(session_id)
        if not s:
            raise SessionNotFound(session_id)
        st = s.state
        tr = _Turno(texto=mensagem, sent=analisar_sentimento(mensagem))

        if s.handoff_status in ATIVO:
            tr.intent, tr.route, tr.reply = "handoff_ativo", "handoff", COPY["handoff_ativo"]
        else:
            self._decidir(s, tr)   # pode levantar LLMUnavailable (nada é salvo nesse caso)

        # contadores de fallback: só falhas SEGUIDAS contam
        st.fallback_streak = st.fallback_streak + 1 if tr.route == "fallback" else 0
        if tr.route == "fallback" and st.fallback_streak >= 2 and tr.intent != "handoff_ativo":
            tr.reply = self._acolher(tr, COPY["oferta_handoff"])
            st.oferta_handoff = True
            tr.acoes.append("ofereceu o professor após 2 falhas seguidas")
        st.turn += 1
        st.acoes = (st.acoes + tr.acoes)[-12:]

        self.store.add_message(s.id, "user", mensagem)
        self.store.add_message(s.id, "assistant", tr.reply)
        self.store.save_state(s)
        latencia = int((time.perf_counter() - inicio) * 1000)
        self._log(s, tr, latencia)
        self._talvez_resumir(s)

        return TurnResult(
            session_id=s.id, reply=tr.reply, intent=tr.intent, route=tr.route, slots=dict(st.slots),
            sentiment={"label": tr.sent.label, "score": tr.sent.score},
            fallback=tr.route == "fallback",
            handoff={"active": s.handoff_status in ATIVO,
                     "reason": (s.handoff or {}).get("motivo"),
                     "summary": (s.handoff or {}).get("resumo")},
            turn=st.turn, latency_ms=latencia, faq_id=tr.faq_id, used_memory=tr.used_memory,
            guardrail=tr.guardrail, etapa=st.etapa, model=tr.modelo or s.model, nlu_origem=tr.nlu_origem)

    # ------------------------------------------------------------------
    def _decidir(self, s: Session, tr: _Turno) -> None:
        st = s.state
        # 1. guardrail de entrada
        g = checar_entrada(tr.texto, self.cfg.max_input_chars)
        if g.bloqueado:
            tr.guardrail = {"tipo": g.tipo, "motivo": g.motivo}
            tr.route = "guardrail"
            tr.intent = {"prompt_injection": "ataque_prompt", "pedido_indevido": "pedido_indevido"}.get(g.tipo, "entrada_invalida")
            tr.reply = COPY[g.tipo].format(max=self.cfg.max_input_chars)
            if g.tipo == "pedido_indevido" and any("pedido_indevido" in a for a in st.acoes[-3:]):
                tr.reply = ("Entendo a pressa! Ainda assim, não entrego pronto, porque o objetivo é você conseguir "
                            "fazer. Que tal começar pela persona: quem é o seu bot e com quem ele conversa?")
            tr.acoes.append(f"bloqueou entrada ({g.tipo})")
            return

        # 1b. nota de 1 a 5 no chat, logo depois de a Lia pedir (funciona em qualquer lente, sem estrelas)
        if st.pediu_avaliacao:
            st.pediu_avaliacao = False
            m = re.fullmatch(r"\s*(?:nota\s*)?([1-5])\s*(?:estrelas?|/\s*5)?\s*[.!]?\s*", tr.texto.lower())
            if m:
                self.store.save_feedback(s.id, int(m.group(1)), "nota dada no chat")
                tr.intent, tr.route = "avaliacao", "regra"
                tr.reply = "Obrigada pela nota! Ela ajuda a melhorar a Lia. Se precisar, é só chamar."
                tr.acoes.append(f"registrou CSAT {m.group(1)} pelo chat")
                return

        nlu = classificar_por_regras(tr.texto, st.ultima_faq)

        # 2. oferta de handoff pendente
        if st.oferta_handoff:
            st.oferta_handoff = False
            resposta = v.e_confirmacao(tr.texto)
            if resposta is True:
                return self._handoff(s, tr, "falha_repetida", nlu)
            if resposta is False:
                tr.intent, tr.route = "recusou_handoff", "regra"
                tr.reply = f"Tudo bem, seguimos por aqui. Posso te ajudar a {MENU}."
                return

        # 2b. resposta à oferta de agendamento feita depois de mostrar a agenda
        if st.oferta_agendamento:
            st.oferta_agendamento = False
            # o modelo costuma perguntar "qual horário prefere?": aceitar a escolha direto
            livres = agenda.horarios_livres(self.store.reservados())
            do_dia = [h for h in livres if st.dia_preferido and st.dia_preferido in normalize_text(h["rotulo"])]
            escolhido = agenda.escolher_opcao(tr.texto, do_dia or livres) or agenda.escolher_opcao(tr.texto, livres)
            if escolhido and nlu.intent in {"nao_entendi", "agendar_plantao", "aceite_sem_contexto"}:
                st.slots["horario"] = escolhido
                tr.acoes.append(f"escolheu {escolhido['rotulo']} depois de ver a agenda")
                return self._fluxo_iniciar(s, tr)
            resposta = v.e_confirmacao(tr.texto)
            if resposta is True and nlu.intent in {"nao_entendi", "agendar_plantao", "aceite_sem_contexto", "continuacao"}:
                tr.acoes.append("aceitou agendar depois de ver a agenda")
                return self._fluxo_iniciar(s, tr)
            if resposta is False:
                tr.intent, tr.route = "recusou_agendamento", "regra"
                tr.reply = f"Sem problema. Posso te ajudar a {MENU}."
                return

        # 3. handoff por regra — tem precedência sobre FAQ e fluxo (como no Build Day)
        st.frustracao_streak = st.frustracao_streak + 1 if tr.sent.label == "negativo" else 0
        if nlu.intent == "falar_professor":
            return self._handoff(s, tr, "pedido_explicito", nlu)
        if nlu.intent == "tema_sensivel":
            return self._handoff(s, tr, "tema_sensivel", nlu)
        if tr.sent.frustracao_forte or st.frustracao_streak >= 2:
            return self._handoff(s, tr, "frustracao", nlu)

        # 4. fluxo transacional em andamento
        if st.etapa:
            return self._fluxo(s, tr, nlu)

        # 5. intenção
        self._rotear(s, tr, nlu)

    def _rotear(self, s: Session, tr: _Turno, nlu: NLUResult) -> None:
        st = s.state
        tr.intent, tr.nlu_origem = nlu.intent, nlu.origem
        if nlu.intent == "agendar_plantao":
            tr.acoes.append("iniciou agendamento de plantão")
            return self._fluxo_iniciar(s, tr)
        if nlu.intent == "cancelar":
            return self._cancelar_agendamento(s, tr)
        if nlu.intent == "consultar_agendamento":
            return self._consultar_agendamento(s, tr)
        if nlu.intent == "disponibilidade":
            return self._disponibilidade(s, tr)
        if nlu.intent == "capacidades":
            tr.route = "regra"
            tr.reply = ("Eu sou a Lia, assistente virtual da oficina. Posso: (1) explicar os temas da oficina, como "
                        "checklist do bot, happy path, slots e estado, regra × LLM, base de conhecimento e RAG, testes, "
                        "métricas, system prompt, memória, guardrails e design conversacional; (2) agendar, consultar "
                        "ou cancelar um plantão com o professor; (3) chamar o professor. Não faço a atividade por você. "
                        "Por onde quer começar?")
            return
        if nlu.intent == "identidade":
            tr.route = "regra"
            tr.reply = ("Sou a Lia, uma assistente virtual: um programa, não uma pessoa e nem o professor. Se precisar "
                        "de alguém de verdade, é só pedir que eu chamo o professor.")
            return
        if nlu.intent == "repetir":
            tr.route, tr.used_memory = "regra", True
            anteriores = [m["content"] for m in self.store.get_messages(s.id) if m["role"] == "assistant"]
            tr.reply = anteriores[-1] if anteriores else f"Claro! Posso te ajudar a {MENU}."
            return
        if nlu.intent == "elogio":
            tr.route = "regra"
            tr.reply = "Que bom que está ajudando! Se tiver outra dúvida, é só mandar."
            return
        if nlu.intent == "ofensa":
            tr.route = "regra"
            tr.reply = ("Sinto muito que eu não esteja ajudando. Posso tentar explicar de outro jeito, ou chamar o "
                        "professor. O que prefere?")
            tr.acoes.append("aluno irritado com a Lia")
            return
        if nlu.intent == "recusa_oferta":
            tr.route = "regra"
            tr.reply = "Tudo bem! Se surgir outra dúvida, é só mandar."
            return
        if nlu.intent == "aceite_sem_contexto":
            tr.route = "regra"
            tr.reply = f"Claro! Posso te ajudar a {MENU}. O que você precisa?"
            return
        if nlu.intent == "confusao":
            tr.route = "regra"
            st.ultima_faq = "minimum-checklist"          # um "sim" em seguida continua por aqui
            tr.reply = ("Entendo, e tudo bem se sentir assim no começo. Vamos por partes: a oficina é sobre montar um "
                        "bot com persona e regras, um slot, memória, uma FAQ e um guardrail ou handoff. Quer começar "
                        "pelo checklist mínimo, ou prefere que eu chame o professor?")
            tr.acoes.append("aluno confuso; a Lia propôs um ponto de partida")
            return
        if nlu.intent == "saudacao":
            if nlu.apelido:
                st.apelido = nlu.apelido
                tr.acoes.append(f"aluno se apresentou como {nlu.apelido}")
            quem = f", {st.apelido}" if st.apelido else ""
            tr.route, tr.reply = "regra", self._acolher(tr, f"Oi{quem}! Posso te ajudar a {MENU}. O que você precisa?")
            return
        if nlu.intent == "despedida":
            nome = (st.slots.get("nome") or "").split(" ")[0] or (st.apelido or "")
            tr.route = "regra"
            tr.reply = f"Por nada{', ' + nome if nome else ''}! Bons estudos. Se puder, avalie esta conversa com uma nota de 1 a 5."
            st.pediu_avaliacao = True
            return
        if nlu.intent in {"faq", "continuacao"}:
            return self._responder_faq(s, tr, get_faq(nlu.faq_id), usou_memoria=nlu.intent == "continuacao")

        # nada casou por regra → LLM tenta entender (decisão regra × LLM nº 2)
        if self.cfg.nlu_llm_fallback:
            llm_nlu = None
            try:
                llm_nlu = classificar_por_llm(self.llm_for(s), tr.texto, self._contexto_curto(s))
            except LLMUnavailable as exc:
                log.warning("NLU pelo LLM falhou (%s); seguindo por regra", exc)
                tr.acoes.append("NLU do LLM indisponível; seguiu por regra")
            if llm_nlu:
                tr.nlu_origem = "llm"
                tr.intent = llm_nlu.intent
                if llm_nlu.intent == "faq":
                    # fora do try: se a RESPOSTA falhar, é 503 — nunca um falso "não entendi"
                    return self._responder_faq(s, tr, get_faq(llm_nlu.faq_id))
        self._fallback(tr, tr.intent if tr.intent in {"fora_da_base", "fora_escopo"} else "nao_entendi")

    # --- respostas ------------------------------------------------------
    def _acolher(self, tr: _Turno, texto: str) -> str:
        return COPY["acolhimento"] + texto if tr.sent.label == "negativo" else texto

    def _fallback(self, tr: _Turno, motivo: str) -> None:
        tr.route, tr.fallback_reason = "fallback", motivo
        if tr.intent not in {"fora_da_base", "fora_escopo"}:
            tr.intent = "nao_entendi"
        tr.reply = self._acolher(tr, COPY[f"fallback_{motivo}"].format(menu=MENU))
        tr.acoes.append(f"fallback ({motivo})")

    def _responder_faq(self, s: Session, tr: _Turno, faq: FaqEntry | None, usou_memoria: bool = False) -> None:
        if faq is None:
            return self._fallback(tr, "nao_entendi")
        st = s.state
        tr.route, tr.faq_id, tr.used_memory = "faq", faq.id, usou_memoria
        tr.intent = "continuacao" if usou_memoria else "faq"
        tom = "acolhimento" if tr.sent.label == "negativo" else "normal"
        objetivo = OBJETIVOS.get(st.slots.get("objetivo") or "", "não definido")
        contexto = (f"\n\n## CONTEXTO DA FAQ\nPergunta da FAQ: {faq.question}\nResposta da FAQ: {faq.answer}\n"
                    f"Objetivo de aprendizagem: {objetivo}\nTOM: {tom}")
        if st.resumo:
            contexto += f"\n\n## RESUMO DA CONVERSA ANTERIOR\n{st.resumo}"
        msgs = [{"role": "system", "content": carregar_prompt(s.prompt_version) + contexto}]
        msgs += self._historico(s) + [{"role": "user", "content": tr.texto}]

        resp = self.llm_for(s).chat(msgs, purpose="answer")    # LLMUnavailable → 503
        tr.llm_ms += resp.latency_ms
        tr.modelo = resp.model
        tr.tokens_in += resp.prompt_tokens
        tr.tokens_out += resp.completion_tokens
        texto, g = checar_saida(resp.text.strip(), self.cfg.max_output_chars)
        if g.bloqueado or not texto:
            # guardrail de saída: troca a geração pela resposta curada da FAQ
            tr.guardrail = {"tipo": g.tipo or "saida_vazia", "motivo": g.motivo}
            texto = faq.answer
            if g.tipo in {"saida_vazamento", "saida_promessa"}:
                # avisa o porquê, em vez de só repetir a FAQ (achado: conversa da Eduarda)
                texto = ("Não consigo entregar isso pronto, mas te ajudo a construir. " + faq.answer +
                         " Quer montar a primeira parte juntos?")
            tr.acoes.append(f"guardrail de saída substituiu a resposta ({g.tipo})")
        elif g.tipo:
            tr.guardrail = {"tipo": g.tipo, "motivo": g.motivo}
        tr.reply = texto
        st.ultima_faq = faq.id
        tr.acoes.append(f"respondeu com a FAQ '{faq.id}'" + (" usando a memória" if usou_memoria else ""))

    def _disponibilidade(self, s: Session, tr: _Turno) -> None:
        """Function calling: o LLM decide chamar consultar_agenda; o código executa."""
        tr.route, tr.intent = "ferramenta", "disponibilidade"
        ferramenta = [{"type": "function", "function": {
            "name": "consultar_agenda",
            "description": "Lista os horários livres de plantão com o professor. Nunca invente horários.",
            "parameters": {"type": "object", "properties": {
                "dia": {"type": "string", "description": "dia da semana (ex.: quinta) ou vazio para todos"}}}}}]
        msgs = [{"role": "system", "content": carregar_prompt(s.prompt_version) +
                 "\n\nPara perguntas sobre horários de plantão, use SEMPRE a ferramenta consultar_agenda. "
                 "Liste os horários e termine perguntando qual a pessoa prefere."},
                {"role": "user", "content": tr.texto}]
        dia_pedido = next((d for d in agenda.DIAS if contains_phrase(normalize_text(tr.texto), d)), None)
        livres = agenda.horarios_livres(self.store.reservados(), dia_pedido)
        s.state.oferta_agendamento = True
        s.state.dia_preferido = dia_pedido
        try:
            llm = self.llm_for(s)
            r1 = llm.chat(msgs, tools=ferramenta, purpose="tool")
            tr.llm_ms += r1.latency_ms
            tr.modelo = r1.model
            if not r1.tool_calls:
                raise LLMUnavailable("modelo não chamou a ferramenta")
            call = r1.tool_calls[0]
            dia_modelo = call.arguments.get("dia") or dia_pedido
            livres = agenda.horarios_livres(self.store.reservados(), dia_modelo)
            s.state.dia_preferido = normalize_text(dia_modelo) if dia_modelo else None
            resultado = {"horarios": livres[:5]}
            chamada = call.raw or {"id": call.id, "type": "function",
                                   "function": {"name": call.name, "arguments": json.dumps(call.arguments)}}
            msgs += [{"role": "assistant", "content": "", "tool_calls": [chamada]},
                     {"role": "tool", "tool_call_id": call.id, "content": json.dumps(resultado, ensure_ascii=False)}]
            r2 = llm.chat(msgs, purpose="tool")
            tr.llm_ms += r2.latency_ms
            texto, g = checar_saida(r2.text.strip(), self.cfg.max_output_chars)
            if g.bloqueado or not texto:
                raise LLMUnavailable("saída bloqueada")
            tr.reply = texto
            tr.acoes.append(f"consultou a agenda via function calling (dia={call.arguments.get('dia')})")
        except LLMUnavailable as exc:
            # degradação graciosa: a regra lista os horários sozinha
            log.warning("agenda por function calling falhou (%s); listando por regra", exc)
            sufixo = f" na {agenda.DIAS[dia_pedido]}" if dia_pedido else ""
            if livres:
                rotulos = ", ".join(h["rotulo"] for h in livres[:5])
                tr.reply = f"Os horários livres de plantão{sufixo} são: {rotulos}. Quer agendar um deles?"
            else:
                tr.reply = f"Não há horário livre de plantão{sufixo}. Quer ver os outros dias?"
            tr.acoes.append("listou a agenda por regra")

    # --- fluxo transacional: agendar plantão ---------------------------
    ORDEM = ["nome", "rm", "email", "objetivo", "horario"]

    def _proxima_etapa(self, st) -> str:
        for slot in self.ORDEM:
            if not st.slots.get(slot):
                return f"plantao:{slot}"
        return "plantao:confirmar"

    def _pergunta(self, s: Session) -> str:
        st = s.state
        etapa = st.etapa.split(":")[1]
        nome = (st.slots.get("nome") or "").split(" ")[0]
        if etapa == "nome":
            return "Qual é o seu nome completo?"
        if etapa == "rm":
            return f"Obrigada, {nome}. Qual é o seu RM?"
        if etapa == "email":
            return "Anotado. Qual e-mail devo usar para enviar o link do plantão?"
        if etapa == "objetivo":
            return ("Sobre qual tema é a dúvida? Pode ser design conversacional, system prompt, "
                    "memória e estado, guardrails e handoff ou base de conhecimento.")
        if etapa == "horario":
            livres = agenda.horarios_livres(self.store.reservados())
            do_dia = [h for h in livres if st.dia_preferido and st.dia_preferido in normalize_text(h["rotulo"])]
            st.opcoes_horario = (do_dia + [h for h in livres if h not in do_dia])[:3]
            if not st.opcoes_horario:
                return "No momento não há horários livres. Quer que eu passe seu pedido direto ao professor?"
            ops = ", ".join(f"({i}) {h['rotulo']}" for i, h in enumerate(st.opcoes_horario, 1))
            return f"Tenho estes horários com o professor: {ops}. Qual prefere?"
        if etapa == "corrigir":
            return "O que você quer corrigir: nome, RM, e-mail, tema ou horário?"
        sl = st.slots
        return (f"Confere: plantão em {sl['horario']['rotulo']}, para {sl['nome']} (RM {sl['rm']}), "
                f"tema {OBJETIVOS[sl['objetivo']]}, com o link enviado para {sl['email']}. Posso confirmar?")

    def _fluxo_iniciar(self, s: Session, tr: _Turno) -> None:
        st = s.state
        if st.slots.get("protocolo"):
            tr.route, tr.intent = "regra", "agendar_plantao"
            tr.reply = (f"Você já tem um plantão em {st.slots['horario']['rotulo']} (protocolo "
                        f"{st.slots['protocolo']}). Quer cancelar esse para marcar outro?")
            return
        self._capturar_oportunista(st, tr.texto)
        st.etapa = self._proxima_etapa(st)
        tr.route, tr.intent = "fluxo", "agendar_plantao"
        tr.reply = self._acolher(tr, "Vamos agendar seu plantão de dúvidas com o professor. " + self._pergunta(s))

    def _capturar_oportunista(self, st, texto: str) -> None:
        """Se a pessoa já mandar e-mail, 'RM 12345' ou 'quinta às 18h', aproveita sem perguntar de novo."""
        if not st.slots.get("horario"):
            h = agenda.escolher_opcao(texto, agenda.horarios_livres(self.store.reservados()))
            if h and re.search(r"\d\s*(h|:)", texto.lower()):      # só com hora explícita
                st.slots["horario"] = h
        if not st.slots.get("email") and (m := v.RE_EMAIL.search(texto)):
            st.slots["email"] = m.group(0).lower()
        if not st.slots.get("rm") and (m := re.search(r"\brm\s*:?\s*(\d{5,6})\b", texto, re.I)):
            st.slots["rm"] = m.group(1)

    def _fluxo(self, s: Session, tr: _Turno, nlu: NLUResult) -> None:
        st = s.state
        etapa = st.etapa.split(":")[1]
        tr.route, tr.intent = "fluxo", "informar_dado"

        if nlu.intent == "cancelar":
            st.etapa, st.opcoes_horario = None, []
            tr.intent, tr.route = "cancelar", "regra"
            tr.reply = ("Tudo bem, parei o agendamento. Os dados que você já informou ficam guardados "
                        "se quiser retomar depois.")
            tr.acoes.append("cancelou o agendamento em andamento")
            return
        if nlu.intent == "consultar_agendamento" and etapa == "horario":
            tr.intent, tr.used_memory = "consultar_agendamento", True
            tr.reply = "Os horários que sugeri foram: " + ", ".join(
                f"({i}) {h['rotulo']}" for i, h in enumerate(st.opcoes_horario, 1)) + ". Qual prefere?"
            return

        try:
            erro = self._preencher(s, etapa, tr.texto)
        except _Resposta as r:          # confirmação: resposta final montada pela regra
            tr.intent = "confirmar_agendamento"
            tr.reply = r.texto
            if r.acao:
                tr.acoes.append(r.acao)
            return
        if erro is None:
            st.invalido_streak = 0
            st.etapa = self._proxima_etapa(st)
            tr.reply = self._acolher(tr, self._pergunta(s))
            return

        # dado inválido: se era uma pergunta da FAQ, responde e retoma o fluxo
        if nlu.intent in {"faq", "continuacao"} and ("?" in tr.texto or nlu.score >= 2):
            # retomada: só a pergunta, sem o "Obrigada, Fulana." / "Anotado." do turno anterior
            pergunta = re.sub(r"^(Obrigada, [^.]+\.|Anotado\.)\s*", "", self._pergunta(s))
            self._responder_faq(s, tr, get_faq(nlu.faq_id), usou_memoria=nlu.intent == "continuacao")
            tr.reply += f"\n\nVoltando ao agendamento: {pergunta}"
            return
        if tr.sent.label == "negativo" and not re.search(r"[\d@]", tr.texto):
            tr.intent = "duvida_no_fluxo"
            tr.reply = ("Entendo, sem problema. " + EXPLICA_ETAPA.get(etapa, "") +
                        " Se preferir, diga 'cancelar' ou peça para falar com o professor.")
            tr.acoes.append(f"aluno com dificuldade na etapa {etapa}; a Lia explicou de novo")
            return
        tr.intent = "dado_invalido"
        st.invalido_streak += 1
        if st.invalido_streak >= 2:
            erro += " Se preferir, diga 'cancelar' para sair do agendamento ou peça para falar com o professor."
        tr.reply = self._acolher(tr, erro)
        tr.acoes.append(f"rejeitou {etapa} inválido")

    def _preencher(self, s: Session, etapa: str, texto: str) -> str | None:
        """Valida o dado da etapa. Devolve a mensagem de erro, ou None se deu certo."""
        st = s.state
        self._capturar_oportunista(st, texto)
        if etapa == "nome":
            r = v.validar_nome(texto)
        elif etapa == "rm":
            r = v.validar_rm(texto)
        elif etapa == "email":
            r = v.validar_email(texto)
        elif etapa == "objetivo":
            t = normalize_text(texto)
            achado = next((k for k, kws in OBJETIVO_KEYWORDS.items() if any(contains_phrase(t, w) for w in kws)), None)
            r = v.SlotResult(bool(achado), achado,
                             None if achado else "Não reconheci o tema. Escolha entre design conversacional, "
                                                 "system prompt, memória e estado, guardrails e handoff ou base de conhecimento.")
        elif etapa == "horario":
            h = agenda.escolher_opcao(texto, st.opcoes_horario) or \
                agenda.escolher_opcao(texto, agenda.horarios_livres(self.store.reservados()))
            r = v.SlotResult(bool(h), h, None if h else
                             "Não consegui identificar o horário. Responda com o número da opção (1, 2 ou 3) ou o dia e a hora.")
        elif etapa == "corrigir":
            t = normalize_text(texto)
            mapa = {"nome": "nome", "rm": "rm", "email": "email", "e mail": "email", "tema": "objetivo",
                    "objetivo": "objetivo", "horario": "horario", "hora": "horario", "dia": "horario"}
            campo = next((c for k, c in mapa.items() if contains_phrase(t, k)), None)
            if not campo:
                return "Me diga qual dado corrigir: nome, RM, e-mail, tema ou horário."
            st.slots[campo] = None
            return None
        else:  # confirmar
            ok = v.e_confirmacao(texto)
            if ok is None:
                return "Só para ter certeza: posso confirmar o agendamento? Responda sim ou não."
            if ok is False:
                st.etapa = "plantao:corrigir"
                s_tr = self._pergunta(s)
                raise _Resposta(s_tr)
            h = st.slots["horario"]
            if not self.store.reservar(h["id"], s.id):
                st.slots["horario"] = None
                st.etapa = "plantao:horario"
                raise _Resposta("Esse horário acabou de ser reservado por outra pessoa. " + self._pergunta(s))
            st.slots["protocolo"] = f"PL-{s.id[:4].upper()}-{h['id']}"
            st.etapa, st.opcoes_horario = None, []
            prof = agenda.load_agenda()["professor"]
            raise _Resposta(f"Agendado! Plantão com o {prof} em {h['rotulo']}, protocolo {st.slots['protocolo']}. "
                            f"O link vai para {st.slots['email']}. Posso ajudar em mais algo?", "agendamento concluído")
        if not r.ok:
            return r.erro
        st.slots[etapa] = r.valor
        return None

    def _cancelar_agendamento(self, s: Session, tr: _Turno) -> None:
        st = s.state
        tr.route, tr.intent = "regra", "cancelar"
        if not st.slots.get("protocolo"):
            tr.reply = "Você não tem plantão agendado comigo. Quer agendar um?"
            return
        self.store.liberar(s.id)
        rotulo = st.slots["horario"]["rotulo"]
        st.slots["horario"], st.slots["protocolo"] = None, None
        tr.reply = f"Cancelei seu plantão de {rotulo}. Se quiser, agendamos outro horário."
        tr.acoes.append("cancelou o plantão agendado")

    def _consultar_agendamento(self, s: Session, tr: _Turno) -> None:
        st = s.state
        tr.route, tr.intent, tr.used_memory = "regra", "consultar_agendamento", True
        if st.slots.get("protocolo"):
            tr.reply = (f"Seu plantão ficou para {st.slots['horario']['rotulo']}, protocolo "
                        f"{st.slots['protocolo']}, com o link enviado para {st.slots['email']}.")
        elif st.opcoes_horario:
            tr.reply = "Os horários que sugeri foram: " + ", ".join(h["rotulo"] for h in st.opcoes_horario) + "."
        else:
            tr.used_memory = False
            tr.reply = "Você ainda não tem plantão agendado. Quer agendar um?"

    # --- handoff --------------------------------------------------------
    def _handoff(self, s: Session, tr: _Turno, motivo: str, nlu: NLUResult) -> None:
        st = s.state
        urgente = nlu.urgente
        intencao = f"agendar_plantao (parou na etapa {st.etapa.split(':')[1]})" if st.etapa else nlu.intent
        tr.route, tr.handoff_reason = "handoff", motivo
        tr.intent = {"pedido_explicito": "falar_professor", "tema_sensivel": "tema_sensivel",
                     "frustracao": "frustracao", "falha_repetida": "falar_professor"}[motivo]
        textos = {
            "pedido_explicito": "Certo! Passei sua conversa para a fila do professor, com um resumo do que falamos, "
                                "para você não precisar repetir nada. Se ele estiver na sala, pode chamá-lo direto.",
            "falha_repetida": "Combinado. Passei sua dúvida para o professor, com o resumo da nossa conversa.",
            "frustracao": "Sinto muito que isso esteja sendo frustrante, e não quero te fazer perder mais tempo. "
                          "Passei sua conversa para o professor, com um resumo, para ele te ajudar diretamente.",
            "tema_sensivel": "Obrigada por me contar. Esse é um assunto que precisa de uma pessoa, e não de mim: "
                             "encaminhei sua conversa ao professor" + (" com prioridade alta." if urgente else "."),
        }
        tr.reply = textos[motivo]
        if urgente and nlu.intent == "tema_sensivel":
            tr.reply += (" Se você estiver em sofrimento agora, também pode ligar para o CVV no 188, "
                         "a qualquer hora e de graça.")
        relato = [m["content"] for m in self.store.get_messages(s.id) if m["role"] == "user"][-3:] + [tr.texto]
        s.handoff = {
            "motivo": motivo,
            "criado_em": agora(),
            "resumo": {
                "motivo": motivo,
                "urgencia": "alta" if urgente else "normal",
                "intencao": intencao,
                "objetivo": OBJETIVOS.get(st.slots.get("objetivo") or "", "não definido"),
                "dados_coletados": {k: (val["rotulo"] if isinstance(val, dict) else val)
                                    for k, val in st.slots.items() if val and k != "objetivo"},
                "relato": relato[-3:],
                "sentimento": tr.sent.label,
                "acoes_do_bot": (st.acoes + ["transferiu para o professor"])[-6:],
                "turnos": st.turn + 1,
                "observacao": "A Lia não deu nota, prazo nem resolveu atividade pelo aluno.",
            },
        }
        s.handoff_status = "pendente"
        st.etapa, st.oferta_handoff = None, False
        tr.acoes.append(f"handoff ({motivo})")

    # --- memória ----------------------------------------------------------
    def _historico(self, s: Session) -> list[dict]:
        """Janela deslizante: últimos N turnos (N*2 mensagens) após o resumo."""
        msgs = self.store.get_messages(s.id, after_id=s.state.resumido_ate)
        janela = msgs[-self.cfg.history_window_turns * 2:]
        return [{"role": m["role"], "content": m["content"]} for m in janela]

    def _contexto_curto(self, s: Session) -> str:
        msgs = self.store.get_messages(s.id)[-4:]
        return "\n".join(f"{m['role']}: {m['content'][:200]}" for m in msgs)

    def _talvez_resumir(self, s: Session) -> None:
        """Resumo rolante: o que sai da janela vira resumo (não se perde)."""
        if not self.cfg.summary_enabled:
            return
        st = s.state
        msgs = self.store.get_messages(s.id, after_id=st.resumido_ate)
        fora = msgs[: max(0, len(msgs) - self.cfg.history_window_turns * 2)]
        if len(fora) < 4:      # resume em lotes para economizar chamadas
            return
        texto = "\n".join(f"{m['role']}: {m['content']}" for m in fora)
        pedido = [{"role": "system", "content": "Resuma em até 5 linhas, em português, os fatos importantes desta "
                                                "conversa (dúvidas, dados informados, decisões). Sem inventar."},
                  {"role": "user", "content": f"RESUMO ATUAL: {st.resumo or '(vazio)'}\n\nNOVAS MENSAGENS:\n{texto}"}]
        try:
            st.resumo = self.llm_for(s).chat(pedido, temperature=0.0, purpose="summary").text.strip()[:1200]
            st.resumido_ate = fora[-1]["id"]
            self.store.save_state(s)
        except LLMUnavailable as exc:
            log.warning("resumo rolante adiado (%s)", exc)   # melhoria, não derruba a conversa

    # --- analytics ------------------------------------------------------
    def _log(self, s: Session, tr: _Turno, latencia: int) -> None:
        self.store.log_turn({
            "ts": agora(), "session_id": s.id, "turn": s.state.turn, "prompt_version": s.prompt_version,
            "provider": s.provider, "model": tr.modelo or s.model, "intent": tr.intent, "nlu_origem": tr.nlu_origem,
            "route": tr.route, "faq_id": tr.faq_id, "fallback": int(tr.route == "fallback"),
            "fallback_reason": tr.fallback_reason, "handoff": int(tr.route == "handoff" and tr.handoff_reason is not None),
            "handoff_reason": tr.handoff_reason, "sentiment_label": tr.sent.label, "sentiment_score": tr.sent.score,
            "guardrail": (tr.guardrail or {}).get("tipo"), "latency_ms": latencia, "llm_latency_ms": tr.llm_ms,
            "prompt_tokens": tr.tokens_in, "completion_tokens": tr.tokens_out,
            "user_text_masked": v.mascarar_pii(tr.texto)[:300],
        })


class _Resposta(Exception):
    """Atalho interno: a etapa de confirmação já produz a resposta final."""
    def __init__(self, texto: str, acao: str | None = None):
        self.texto, self.acao = texto, acao
