"""Validated local settings and a stable embedding index profile."""

import hashlib
import json
import pathlib
import typing

import pydantic
import pydantic_settings


minimum_secret_bytes: typing.Final = 32


@typing.final
class Settings(pydantic_settings.BaseSettings):
    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="STUDY_RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: str
    jwt_secret: pydantic.SecretStr
    jwt_issuer: str = "study-rag"
    jwt_audience: str = "study-rag-api"
    jwt_expiry_seconds: int = pydantic.Field(default=3600, ge=60, le=86400)
    upload_directory: pathlib.Path = pathlib.Path(".data/uploads")
    maximum_upload_bytes: int = pydantic.Field(default=104857600, ge=1024)
    maximum_pdf_pages: int = pydantic.Field(default=5000, ge=1)
    maximum_extracted_characters: int = pydantic.Field(default=20000000, ge=1)
    maximum_document_chunks: int = pydantic.Field(default=100000, ge=1)
    qdrant_url: str | None = None
    qdrant_directory: pathlib.Path = pathlib.Path(".data/qdrant")
    model_cache_directory: pathlib.Path = pathlib.Path(".data/models")
    embedding_backend: typing.Literal["huggingface", "ollama"] = "ollama"
    embedding_model: str = "google/embeddinggemma-300m"
    embedding_device: typing.Literal["auto", "cpu", "mps", "cuda"] = "auto"
    tokenizer_model: str = "Qwen/Qwen3.5-4B"
    chunk_byte_limit: int = pydantic.Field(default=1536, ge=64, le=1536)
    ollama_embedding_model: str = "embeddinggemma:300m"
    ollama_url: str = "http://127.0.0.1:11434"
    generation_model: str = "qwen3.5:4b"
    chunk_token_limit: int = pydantic.Field(default=512, ge=16, le=1536)
    chunk_overlap_tokens: int = pydantic.Field(default=64, ge=0)
    retrieval_limit: int = pydantic.Field(default=6, ge=1, le=24)
    retrieval_candidates: int = pydantic.Field(default=24, ge=1, le=100)
    overview_chunk_limit: int = pydantic.Field(default=40, ge=8, le=48)
    scan_chunk_limit: int = pydantic.Field(default=20000, ge=256, le=100000)
    maximum_search_passes: int = pydantic.Field(default=3, ge=1, le=4)
    minimum_similarity: float = pydantic.Field(default=0.35, ge=-1.0, le=1.0)
    worker_poll_seconds: float = pydantic.Field(default=1.0, gt=0.0)
    document_lease_seconds: int = pydantic.Field(default=120, ge=10)
    heartbeat_seconds: int = pydantic.Field(default=30, ge=1)
    turn_lease_seconds: int = pydantic.Field(default=300, ge=30)
    generation_timeout_seconds: float = pydantic.Field(default=120.0, gt=0.0)
    answer_timeout_seconds: float = pydantic.Field(default=240.0, gt=0.0)
    generation_context_tokens: int = pydantic.Field(default=16384, ge=2048)
    generation_output_tokens: int = pydantic.Field(default=2048, ge=16)
    history_message_limit: int = pydantic.Field(default=8, ge=0, le=40)

    @pydantic.field_validator("jwt_secret")
    @classmethod
    def _validate_secret(cls, secret_value: pydantic.SecretStr) -> pydantic.SecretStr:
        if len(secret_value.get_secret_value().encode("utf-8")) < minimum_secret_bytes:
            raise ValueError("JWT secret must contain at least 32 UTF-8 bytes")
        return secret_value

    @pydantic.field_validator("database_url")
    @classmethod
    def _validate_database(cls, database_value: str) -> str:
        if not database_value.startswith("postgresql+asyncpg://"):
            raise ValueError("Database URL must use postgresql+asyncpg")
        return database_value

    @pydantic.model_validator(mode="after")
    def _validate_invariants(self) -> typing.Self:
        if self.chunk_overlap_tokens >= self.chunk_token_limit:
            raise ValueError("Chunk overlap must be smaller than the chunk token limit")
        if self.heartbeat_seconds >= self.document_lease_seconds:
            raise ValueError("Heartbeat interval must be smaller than the document lease")
        if self.retrieval_limit > self.retrieval_candidates:
            raise ValueError("Retrieval limit must not exceed candidate count")
        if self.answer_timeout_seconds + 10 >= self.turn_lease_seconds:
            raise ValueError("Answer timeout must leave time to complete the turn lease")
        if self.generation_timeout_seconds > self.answer_timeout_seconds:
            raise ValueError("Model timeout must not exceed the whole answer timeout")
        if self.generation_output_tokens + 512 >= self.generation_context_tokens:
            raise ValueError("Generation context must leave room for input and chat template")
        return self

    @property
    def vector_dimensions(self) -> int:
        return 768

    @property
    def effective_tokenizer_model(self) -> str:
        if self.embedding_backend == "huggingface":
            return self.embedding_model
        return self.tokenizer_model

    @property
    def embedding_profile(self) -> str:
        model_identifier: typing.Final = (
            self.embedding_model if self.embedding_backend == "huggingface" else self.ollama_embedding_model
        )
        profile_payload: typing.Final = {
            "backend": self.embedding_backend,
            "model": model_identifier,
            "dimensions": self.vector_dimensions,
            "tokenizer": self.effective_tokenizer_model,
            "chunk_tokens": self.chunk_token_limit,
            "overlap_tokens": self.chunk_overlap_tokens,
            "chunk_bytes": self.chunk_byte_limit if self.embedding_backend == "ollama" else None,
            "formatted_bytes": 1900 if self.embedding_backend == "ollama" else None,
            "prefix_policy": "retrieval-query-document-v1",
        }
        serialized_profile: typing.Final = json.dumps(profile_payload, sort_keys=True, separators=(",", ":"))
        profile_digest: typing.Final = hashlib.sha256(serialized_profile.encode("utf-8")).hexdigest()[:16]
        return f"embeddinggemma-300m-{self.embedding_backend}-{profile_digest}"
