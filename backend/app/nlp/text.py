"""Normalização de texto (porte direto de normalizeText, do Build Day)."""
import re
import unicodedata


def normalize_text(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação e com espaços únicos."""
    text = unicodedata.normalize("NFD", text.strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """Casa a frase como palavra(s) inteira(s): 'regra' não casa 'regras'."""
    return f" {normalize_text(phrase)} " in f" {normalized_text} "
