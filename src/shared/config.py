"""GrACE-wide configuration loaded from .env at project root."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GraceSettings(BaseSettings):
    """GrACE-wide configuration. Loaded from .env at project root."""

    # PostgreSQL
    database_url: str = Field(description="SQLAlchemy connection string for PostgreSQL")

    # Ollama
    ollama_base_url: str = Field(
        default="http://localhost:11434", description="Ollama API base URL"
    )
    ollama_model: str = Field(
        default="qwen2.5:7b", description="Primary Ollama model for inference"
    )
    ollama_embed_model: str = Field(
        default="nomic-embed-text", description="Ollama embedding model"
    )
    ollama_timeout: int = Field(
        default=300, description="Ollama request timeout in seconds"
    )

    # LLM API Key (for cloud providers)
    llm_api_key: str = Field(
        default="", description="API key for cloud LLM providers (Anthropic, OpenAI, etc.)"
    )

    # FastAPI
    grace_host: str = Field(default="localhost", description="FastAPI bind host")
    grace_port: int = Field(default=8000, description="FastAPI bind port")

    # ArcadeDB
    arcade_host: str = Field(default="localhost", description="ArcadeDB server host")
    arcade_port: int = Field(default=2480, description="ArcadeDB HTTP API port")
    arcade_username: str = Field(default="root", description="ArcadeDB username")
    arcade_password: str = Field(
        default="gracedev", description="ArcadeDB root password"
    )
    arcade_database: str = Field(
        default="grace", description="ArcadeDB database name"
    )
    arcade_timeout: int = Field(
        default=30, description="ArcadeDB request timeout in seconds"
    )

    # Retrieval
    embedding_dim: int = Field(
        default=768, description="Embedding dimension (auto-detected from Ollama)"
    )
    retrieval_rrf_k: int = Field(default=60, description="RRF damping constant")
    retrieval_reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="Cross-encoder model for reranking",
    )
    retrieval_top_k: int = Field(default=10, description="Final result count")
    retrieval_serialization_format: str = Field(
        default="template", description="template|turtle|llm"
    )
    retrieval_temporal_as_strategy: bool = Field(
        default=False,
        description="True=temporal as separate RRF strategy, False=filter on graph results",
    )

    # Discovery
    discovery_source_dir: str = Field(
        default="data/discovery-sample",
        description="Default directory for Discovery document input",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> GraceSettings:
    """Return a cached singleton GraceSettings instance."""
    return GraceSettings()
