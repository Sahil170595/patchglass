"""The single typed config/secret boundary. Leaf modules receive a Settings object — they never
read os.environ directly. Env vars are prefixed TASKBUNDLE_ (e.g. TASKBUNDLE_OLLAMA_HOST)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration. Read once, injected where needed."""

    model_config = SettingsConfigDict(env_prefix="TASKBUNDLE_", env_file=".env", extra="ignore")

    # LLM providers. Ollama is the free/offline default for base testing; OpenAI / HF are opt-in.
    ollama_host: str = Field(default="http://localhost:11434", description="Local Ollama base URL.")
    openai_api_key: SecretStr | None = Field(default=None, description="Optional; enables llm:openai/<model>.")
    hf_api_key: SecretStr | None = Field(default=None, description="Optional; enables llm:huggingface/<model>.")
    hf_base_url: str = Field(
        default="https://router.huggingface.co/v1",
        description="HF Inference Providers OpenAI-compatible router base URL.",
    )

    # Docker. None => default host (npipe/unix socket as configured by Docker Desktop).
    docker_host: str | None = Field(default=None, description="Override DOCKER_HOST if set.")

    log_level: str = Field(default="INFO", description="Logging verbosity.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings (constructed once)."""
    return Settings()
