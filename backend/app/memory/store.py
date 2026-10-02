"""Memória e estado no servidor, indexados por session_id (SQLite).

- messages: a MEMÓRIA (a conversa, verbatim).
- sessions.state_json: o ESTADO (slots, etapa do fluxo, contadores).
O frontend guarda apenas o session_id; tudo o que o bot sabe mora aqui.
"""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT,
    prompt_version TEXT, provider TEXT, model TEXT,
    state_json TEXT, handoff_json TEXT, handoff_status TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
    content TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS turn_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, turn INTEGER,
    prompt_version TEXT, provider TEXT, model TEXT, intent TEXT, nlu_origem TEXT,
    route TEXT, faq_id TEXT, fallback INTEGER, fallback_reason TEXT,
    handoff INTEGER, handoff_reason TEXT, sentiment_label TEXT, sentiment_score REAL,
    guardrail TEXT, latency_ms INTEGER, llm_latency_ms INTEGER,
    prompt_tokens INTEGER, completion_tokens INTEGER, user_text_masked TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    session_id TEXT PRIMARY KEY, score INTEGER, comment TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS reservas (
    horario_id TEXT PRIMARY KEY, session_id TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_log_session ON turn_log(session_id);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SessionState:
    slots: dict = field(default_factory=lambda: {
        "objetivo": None, "nome": None, "rm": None, "email": None, "horario": None, "protocolo": None})
    etapa: str | None = None             # None | plantao:nome | plantao:rm | ... | plantao:confirmar
    opcoes_horario: list = field(default_factory=list)
    turn: int = 0
    fallback_streak: int = 0
    invalido_streak: int = 0
    frustracao_streak: int = 0
    oferta_handoff: bool = False
    oferta_agendamento: bool = False     # a Lia acabou de mostrar a agenda
    pediu_avaliacao: bool = False        # a Lia acabou de pedir a nota de 1 a 5
    apelido: str | None = None           # como o aluno se apresentou ("oi, sou Eduarda")
    dia_preferido: str | None = None     # dia que o aluno perguntou na agenda
    ultima_faq: str | None = None
    resumo: str = ""
    resumido_ate: int = 0                # id da última mensagem já coberta pelo resumo
    acoes: list = field(default_factory=list)


@dataclass
class Session:
    id: str
    created_at: str
    prompt_version: str
    provider: str
    model: str
    state: SessionState
    handoff: dict | None
    handoff_status: str | None


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # --- sessões -------------------------------------------------------
    def create_session(self, prompt_version: str, provider: str, model: str, objetivo: str | None) -> Session:
        state = SessionState()
        state.slots["objetivo"] = objetivo
        sid = uuid.uuid4().hex[:12]
        with self.tx() as c:
            c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                      (sid, agora(), agora(), prompt_version, provider, model,
                       json.dumps(asdict(state), ensure_ascii=False), None, None))
        return self.get_session(sid)

    def get_session(self, sid: str) -> Session | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        return Session(row["id"], row["created_at"], row["prompt_version"], row["provider"], row["model"],
                       SessionState(**json.loads(row["state_json"])),
                       json.loads(row["handoff_json"]) if row["handoff_json"] else None, row["handoff_status"])

    def save_state(self, s: Session) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET state_json=?, handoff_json=?, handoff_status=?, updated_at=? WHERE id=?",
                      (json.dumps(asdict(s.state), ensure_ascii=False),
                       json.dumps(s.handoff, ensure_ascii=False) if s.handoff else None,
                       s.handoff_status, agora(), s.id))

    def delete_session(self, sid: str) -> bool:
        """Direito ao esquecimento: apaga conversa, estado, feedback e reservas.
        O log de analytics perde o texto (métricas agregadas continuam válidas)."""
        with self.tx() as c:
            n = c.execute("DELETE FROM sessions WHERE id=?", (sid,)).rowcount
            c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            c.execute("DELETE FROM feedback WHERE session_id=?", (sid,))
            c.execute("DELETE FROM reservas WHERE session_id=?", (sid,))
            c.execute("UPDATE turn_log SET user_text_masked=NULL WHERE session_id=?", (sid,))
        return n > 0

    def list_handoffs(self, status: str | None = None) -> list[Session]:
        q = "SELECT id FROM sessions WHERE handoff_json IS NOT NULL"
        args: tuple = ()
        if status:
            q, args = q + " AND handoff_status=?", (status,)
        with self._lock:
            ids = [r["id"] for r in self._conn.execute(q + " ORDER BY updated_at DESC", args)]
        return [s for s in (self.get_session(i) for i in ids) if s]

    # --- mensagens -----------------------------------------------------
    def add_message(self, sid: str, role: str, content: str) -> int:
        with self.tx() as c:
            return c.execute("INSERT INTO messages(session_id, role, content, created_at) VALUES (?,?,?,?)",
                             (sid, role, content, agora())).lastrowid

    def get_messages(self, sid: str, after_id: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, created_at FROM messages WHERE session_id=? AND id>? ORDER BY id",
                (sid, after_id)).fetchall()
        return [dict(r) for r in rows]

    # --- reservas ------------------------------------------------------
    def reservados(self) -> set[str]:
        with self._lock:
            return {r["horario_id"] for r in self._conn.execute("SELECT horario_id FROM reservas")}

    def reservar(self, horario_id: str, sid: str) -> bool:
        try:
            with self.tx() as c:
                c.execute("INSERT INTO reservas VALUES (?,?,?)", (horario_id, sid, agora()))
            return True
        except sqlite3.IntegrityError:
            return False

    def liberar(self, sid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM reservas WHERE session_id=?", (sid,))

    # --- feedback e log ------------------------------------------------
    def save_feedback(self, sid: str, score: int, comment: str | None) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?,?)", (sid, score, comment, agora()))

    def log_turn(self, row: dict) -> None:
        cols = ", ".join(row)
        with self.tx() as c:
            c.execute(f"INSERT INTO turn_log({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]
