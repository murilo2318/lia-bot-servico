"""Captura e validação de slots por código.

O slot não depende de o LLM "lembrar": ele é extraído por regex, validado
aqui e salvo numa estrutura no servidor (memory/store.py).
"""
import re
from dataclasses import dataclass

from app.nlp.text import normalize_text

RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RE_EMAIL_LOOSE = re.compile(r"\S+@\S*|\S+\.(?:com|br)\S*", re.IGNORECASE)
RE_RM = re.compile(r"\b(?:rm\s*:?\s*)?(\d{5,6})\b", re.IGNORECASE)
RE_DIGITS = re.compile(r"\d+")
RE_NOME_PREFIXO = re.compile(r"^(?:(?:o\s+)?meu nome (?:é|e|eh)|me chamo|eu sou|sou (?:o|a)?|nome)\s*:?\s*", re.IGNORECASE)
RE_NOME_VALIDO = re.compile(r"^[A-Za-zÀ-ÿ'´`-]{2,}(?:\s+[A-Za-zÀ-ÿ'´`-]{1,}){1,5}$")


@dataclass
class SlotResult:
    ok: bool
    valor: str | None = None
    erro: str | None = None


NAO_NOME = {"quero", "queria", "marcar", "agendar", "plantao", "horario", "quarta", "quinta", "terca", "segunda",
            "sexta", "duvida", "professor", "nao", "sim", "oi", "ola", "como", "que", "qual", "pode", "isso", "aqui",
            "entendi", "ajuda", "obrigado", "obrigada", "cancelar", "tchau", "lia"}


def validar_nome(texto: str) -> SlotResult:
    bruto = RE_NOME_PREFIXO.sub("", texto.strip()).strip(" .!,")
    if set(normalize_text(bruto).split()) & NAO_NOME:
        return SlotResult(False, erro="Isso não parece um nome. Qual é o seu nome completo (ex.: Marina Alves)?")
    if not RE_NOME_VALIDO.match(bruto):
        return SlotResult(False, erro="Preciso do seu nome e sobrenome, só com letras (ex.: Marina Alves).")
    particulas = {"da", "de", "do", "das", "dos", "e"}
    partes = bruto.split()
    return SlotResult(True, " ".join(
        p if (i and p.lower() in particulas) else (p.capitalize() if p.islower() else p) for i, p in enumerate(partes)))


def validar_rm(texto: str) -> SlotResult:
    m = RE_RM.search(texto)
    if m:
        return SlotResult(True, m.group(1))
    digitos = "".join(RE_DIGITS.findall(texto))
    if digitos:
        return SlotResult(False, erro=f"O RM precisa ter 5 ou 6 dígitos e recebi {len(digitos)}. Pode conferir?")
    return SlotResult(False, erro="Não encontrei um RM na mensagem. Ele tem 5 ou 6 dígitos (ex.: 562358).")


def validar_email(texto: str) -> SlotResult:
    m = RE_EMAIL.search(texto)
    if m and not m.group(0).endswith("."):
        return SlotResult(True, m.group(0).lower())
    return SlotResult(False, erro="Esse e-mail parece incompleto. O formato é nome@dominio.com.")


def mascarar_pii(texto: str | None) -> str | None:
    """LGPD: mascara e-mail, RM e CPF antes de gravar no log de analytics."""
    if texto is None:
        return None
    texto = RE_EMAIL.sub("[email]", texto)
    texto = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "[cpf]", texto)
    return re.sub(r"\b\d{5,6}\b", "[rm]", texto)


def e_confirmacao(texto: str) -> bool | None:
    t = normalize_text(texto)
    if re.fullmatch(r"(sim|s|isso|pode|confirmo|confirmar|confirma|ok|beleza|pode sim|sim pode|claro|isso mesmo)( pode| confirma| por favor)?", t):
        return True
    if re.fullmatch(r"(nao|n|nao quero|negativo|melhor nao|agora nao)( obrigad[oa])?", t):
        return False
    return None
