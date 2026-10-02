"""Variações de linguagem por categoria, carregadas de data/variacoes.json.

Regra determinística alimentada por dados: cada expressão é normalizada (acentos,
pontuação) e tem as abreviações expandidas, do mesmo jeito que a mensagem do aluno.
A comparação é com a mensagem INTEIRA, para "não" não casar com "não sei o que é slot".
"""
import json
from functools import lru_cache

from app.config import BASE_DIR
from app.nlp.text import expandir_abreviacoes, normalize_text

ARQUIVO = BASE_DIR / "data" / "variacoes.json"
SUFIXOS = (" lia", " por favor", " ai", " entao", " mesmo")


def _canon(texto: str) -> str:
    t = expandir_abreviacoes(normalize_text(texto))
    for s in SUFIXOS:                      # "valeu lia", "sim por favor" → mesma forma
        if t.endswith(s) and len(t) > len(s):
            t = t[: -len(s)]
    return t.strip()


@lru_cache
def carregar() -> dict[str, frozenset[str]]:
    dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    return {cat: frozenset(_canon(x) for x in itens) for cat, itens in dados.items() if not cat.startswith("_")}


def categoria(texto: str) -> str | None:
    """Devolve a categoria da mensagem (ver data/variacoes.json), ou None."""
    t = _canon(texto)
    for cat in ("pedido_professor", "ofensa", "confusao", "identidade", "capacidades", "repetir",
                "despedida", "recusa", "elogio", "saudacao", "aceite"):
        if t in carregar().get(cat, ()):
            return cat
    return None
