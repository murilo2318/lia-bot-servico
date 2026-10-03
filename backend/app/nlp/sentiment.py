"""Análise de sentimento por léxico (abordagem vista no 1º semestre).

Escolhemos léxico e não um modelo do Hugging Face porque: roda em < 1 ms,
não adiciona dependência pesada ao backend, é auditável e cobre bem o
vocabulário de frustração de uma oficina. O rótulo muda o comportamento
do bot no orquestrador (tom de acolhimento e antecipação do handoff).
"""
from dataclasses import dataclass

from app.nlp.text import contains_phrase, expandir_abreviacoes, normalize_text

NEGATIVOS = {
    "nao entendi": 1.0, "nao entendo": 1.0, "nao consigo": 1.0, "nao funciona": 1.0,
    "confuso": 1.0, "confusa": 1.0, "dificil": 0.8, "perdido": 1.0, "perdida": 1.0,
    "complicado": 0.8, "travado": 1.0, "travei": 1.0, "ruim": 1.0, "pessimo": 1.5,
    "horrivel": 1.5, "chato": 0.8, "odeio": 1.5, "inutil": 1.5, "irritado": 1.5,
    "irritada": 1.5, "frustrado": 1.5, "frustrada": 1.5, "cansado": 0.8, "cansada": 0.8,
    "nao aguento": 1.5, "de novo": 0.5, "ja tentei": 1.0, "nada funciona": 1.5,
    "nao ajuda": 1.5, "nao ajudou": 1.5, "voce nao entende": 1.5, "que droga": 1.5,
    "nao to entendendo": 1.0, "nao estou entendendo": 1.0, "boiando": 1.0, "boiei": 1.0, "me perdi": 1.0,
    "nao sei o que fazer": 1.0, "nao faco ideia": 0.8, "socorro": 1.0, "nao consigo acompanhar": 1.0,
    "burra": 1.5, "burro": 1.5, "lixo": 1.5, "porcaria": 1.5, "nao serve pra nada": 1.5, "nao serve para nada": 1.5,
    "chata": 1.0, "que saco": 1.0, "merda": 1.5, "nao ajuda em nada": 1.5, "travado": 1.0, "travada": 1.0,
    "empacado": 1.0, "empacada": 1.0, "dificil demais": 1.0, "complicado demais": 1.0, "muita informacao": 0.8,
}
FRUSTRACAO_FORTE = {"desisto", "desistir", "cansei", "to desistindo", "nao aguento mais", "chega"}
POSITIVOS = {
    "obrigado": 1.0, "obrigada": 1.0, "valeu": 1.0, "otimo": 1.0, "otima": 1.0,
    "perfeito": 1.0, "legal": 0.8, "show": 0.8, "entendi": 0.8, "ajudou": 1.0,
    "massa": 0.8, "top": 0.8, "excelente": 1.2, "muito bom": 1.0, "gostei": 1.0, "adorei": 1.2, "amei": 1.2,
    "mandou bem": 1.0, "parabens": 1.0, "incrivel": 1.0, "sensacional": 1.2, "otima": 1.0, "demais": 0.5,
}


@dataclass
class Sentimento:
    label: str        # negativo | neutro | positivo
    score: float      # confiança 0..1
    frustracao_forte: bool = False


def analisar_sentimento(texto: str) -> Sentimento:
    t = expandir_abreviacoes(normalize_text(texto))
    neg = sum(p for termo, p in NEGATIVOS.items() if contains_phrase(t, termo))
    pos = sum(p for termo, p in POSITIVOS.items() if contains_phrase(t, termo))
    # "entendi" dentro de "nao entendi" não é positivo
    if contains_phrase(t, "nao entendi"):
        pos -= POSITIVOS["entendi"]
    forte = any(contains_phrase(t, termo) for termo in FRUSTRACAO_FORTE)
    if forte:
        neg += 2.0
    # sinais de intensidade no texto bruto
    if texto.count("!") >= 2 or texto.count("?") >= 3:
        neg += 0.5 if neg > 0 else 0
    letras = [c for c in texto if c.isalpha()]
    # caixa alta só REFORÇA uma negatividade que já existe ("NÃO ENTENDI NADA"); sozinha não é grito
    # (achado no print 02b, 03/10: "QUERO AGENDAR UM PLANTAO" era lido como frustração)
    if neg > 0 and len(letras) >= 8 and sum(c.isupper() for c in letras) / len(letras) > 0.7:
        neg += 1.0

    saldo = pos - neg
    if saldo <= -0.8:
        return Sentimento("negativo", round(min(0.55 + neg / 5, 0.99), 2), forte)
    if saldo >= 0.8:
        return Sentimento("positivo", round(min(0.55 + pos / 5, 0.99), 2), False)
    return Sentimento("neutro", round(0.6 + min(abs(saldo), 0.5) * 0.2, 2), False)
