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


# Abreviações comuns de chat (achado nas conversas manuais: "n precisa", "vlw", "qnd", "oq").
# Expandidas ANTES das regras, para que todas passem a entender esse jeito de escrever.
ABREVIACOES = {
    "n": "nao", "ñ": "nao", "nn": "nao", "s": "sim", "ss": "sim", "vlw": "valeu", "vlww": "valeu",
    "obg": "obrigado", "obgd": "obrigado", "obgda": "obrigada", "brigado": "obrigado", "brigada": "obrigada",
    "qnd": "quando", "qdo": "quando", "oq": "o que", "pq": "por que", "pra": "para", "pro": "para o",
    "tb": "tambem", "tbm": "tambem", "blz": "beleza", "msm": "mesmo", "vc": "voce", "vcs": "voces",
    "td": "tudo", "tds": "todos", "mt": "muito", "mto": "muito", "pfv": "por favor", "pfvr": "por favor",
    "q": "que", "cmg": "comigo", "hj": "hoje", "agr": "agora", "dps": "depois", "ngm": "ninguem",
    "sla": "sei la", "tlg": "ta ligado", "kd": "cade", "flw": "falou", "tmj": "valeu", "ok": "ok",
}


def expandir_abreviacoes(texto_normalizado: str) -> str:
    """Recebe texto já normalizado (normalize_text) e troca abreviações por palavras inteiras."""
    return " ".join(ABREVIACOES.get(p, p) for p in texto_normalizado.split())
