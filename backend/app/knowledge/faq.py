"""Consulta à FAQ curada.

Porte de retrieveFaq (Build Day): pontua cada FAQ pela quantidade de
palavras-chave presentes na pergunta; empates seguem a ordem do arquivo.
Quando o RAG chegar (próximo módulo), ele substitui apenas buscar_faq(),
sem mudar o orquestrador nem o contrato da API.
"""
import json
from dataclasses import dataclass
from functools import lru_cache

from app.config import BASE_DIR
from app.nlp.text import contains_phrase, normalize_text

FAQ_PATH = BASE_DIR / "data" / "faq.json"


@dataclass(frozen=True)
class FaqEntry:
    id: str
    question: str
    answer: str
    keywords: tuple[str, ...]


@lru_cache
def load_faq() -> tuple[FaqEntry, ...]:
    raw = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
    return tuple(
        FaqEntry(item["id"], item["question"], item["answer"], tuple(item["keywords"]))
        for item in raw
    )


def get_faq(faq_id: str | None) -> FaqEntry | None:
    return next((f for f in load_faq() if f.id == faq_id), None)


def buscar_faq(pergunta: str) -> tuple[FaqEntry | None, int]:
    """Devolve a FAQ de maior pontuação e a pontuação (0 = nada encontrado)."""
    texto = normalize_text(pergunta)
    melhor, melhor_score = None, 0
    for entry in load_faq():
        keywords = {normalize_text(k) for k in entry.keywords}
        score = sum(1 for k in keywords if contains_phrase(texto, k))
        if score > melhor_score:
            melhor, melhor_score = entry, score
    return melhor, melhor_score


def temas_disponiveis() -> str:
    return "; ".join(f.question for f in load_faq())
