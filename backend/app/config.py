"""Configuração única do backend, lida do .env (pydantic-settings)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # Segurança da API
    api_key: str = "troque-esta-chave"

    # Provedor de LLM: gemini | groq | mock
    llm_provider: str = "groq"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_s: float = 30.0
    llm_retries: int = 1                 # novas tentativas em 503/429/timeout
    llm_retry_wait_s: float = 1.5
    llm_fallback_provider: str = ""      # ex.: groq (usado se o principal continuar fora)
    llm_temperature: float = 0.3

    # Comportamento do bot
    prompt_version: str = "v2"          # v1 (Build Day) | v2 (cinco camadas)
    history_window_turns: int = 6       # N turnos (usuário + bot) enviados ao LLM
    summary_enabled: bool = True        # resumo rolante do histórico antigo
    nlu_llm_fallback: bool = True       # LLM classifica quando as regras não casam
    nlu_conf_alta: float = 0.75         # acima: o sistema age pela classificação do LLM
    nlu_conf_media: float = 0.5         # entre média e alta: pergunta "você quis dizer...?" (só FAQ)
    max_input_chars: int = 500          # guardrail de entrada (conversacional)
    max_output_chars: int = 700         # guardrail de saída

    # Persistência
    db_path: str = str(BASE_DIR / "data" / "lia.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
