"""Agenda simulada de plantões com o professor (JSON + reservas no SQLite).

É a parte do agendamento que não pode errar, então é regra: o LLM nunca
inventa horário; ele só pode consultar esta função (function calling).
"""
import json
import re
from functools import lru_cache

from app.config import BASE_DIR
from app.nlp.text import normalize_text

AGENDA_PATH = BASE_DIR / "data" / "agenda_plantao.json"

DIAS = {"segunda": "segunda", "terca": "terça", "quarta": "quarta", "quinta": "quinta", "sexta": "sexta"}


@lru_cache
def load_agenda() -> dict:
    return json.loads(AGENDA_PATH.read_text(encoding="utf-8"))


def horarios_livres(reservados: set[str], dia: str | None = None) -> list[dict]:
    livres = [h for h in load_agenda()["horarios"] if h["id"] not in reservados]
    if dia:
        alvo = normalize_text(dia)
        livres = [h for h in livres if alvo in normalize_text(h["rotulo"]) or alvo in h["data"]]
    return livres


def get_horario(horario_id: str) -> dict | None:
    return next((h for h in load_agenda()["horarios"] if h["id"] == horario_id), None)


def _hora_rotulo(rotulo: str) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})h(\d{2})?", rotulo)
    return int(m.group(1)), int(m.group(2) or 0)


def escolher_opcao(texto: str, opcoes: list[dict]) -> dict | None:
    """Casa a resposta do usuário com uma das opções oferecidas.

    Aceita o número da opção ("2", "a segunda"), a hora ("19h", "19:30")
    e/ou o dia ("quinta"). Se sobrar mais de uma candidata, devolve None
    e o bot pergunta de novo (nunca chuta um horário).
    """
    t = normalize_text(texto)
    ordinais = [("1", 0), ("primeira", 0), ("primeiro", 0), ("2", 1), ("segunda", 1),
                ("segundo", 1), ("3", 2), ("terceira", 2), ("terceiro", 2)]
    for chave, idx in ordinais:
        if t in (chave, f"a {chave}", f"o {chave}", f"opcao {chave}") and idx < len(opcoes):
            return opcoes[idx]
    horas = {(int(h), int(m or 0)) for h, m in re.findall(r"(\d{1,2})\s*(?:h|:)\s*(\d{2})?", texto.lower())}
    candidatas = opcoes
    if horas:
        candidatas = [o for o in candidatas if _hora_rotulo(o["rotulo"]) in horas]
    dias = [d for d in DIAS if f" {d} " in f" {t} "]
    if dias:
        candidatas = [o for o in candidatas if normalize_text(o["rotulo"]).split()[0] in dias]
    if not horas and not dias:
        return None
    return candidatas[0] if len(candidatas) == 1 else None
