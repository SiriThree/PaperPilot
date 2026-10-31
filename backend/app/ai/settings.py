import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

TOKEN_CHUNK_MODEL = "BAAI/bge-small-zh-v1.5"


@dataclass(frozen=True)
class AISettings:
    base_url: str
    api_key: str = field(repr=False)
    chat_model: str
    embedding_model: str
    embedding_dimensions: int
    timeout_seconds: float = 45
    chunk_chars: int = 1200
    chunk_overlap: int = 120
    max_candidates: int = 1000
    max_context_chars: int = 60000
    min_similarity: float = 0.2
    embedding_backend: str = "remote"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = field(default=None, repr=False)
    local_model_dir: Path | None = None
    token_limit_parameter: str = "max_tokens"
    response_format: str = "json_schema"
    embedding_revision: str | None = None
    chunk_strategy: str = "auto"
    chunk_tokens: int = 384
    chunk_token_overlap: int = 48
    lexical_backend: str = "inverted"
    lexical_cache_mb: int = 256
    query_embedding_worker: str = "resident"
    embedding_queue_size: int = 16

    @property
    def active_chunk_strategy(self):
        if self.chunk_strategy == "auto":
            return (
                "sentence-token-v2"
                if self.embedding_backend == "fastembed" and self.embedding_model == TOKEN_CHUNK_MODEL
                else "character-v1"
            )
        return self.chunk_strategy

    def __post_init__(self):
        if self.lexical_backend not in {"streaming", "inverted"}:
            raise ValueError("Unsupported lexical backend")
        if type(self.lexical_cache_mb) is not int or not 1 <= self.lexical_cache_mb <= 2048:
            raise ValueError("Invalid lexical cache budget")
        if self.query_embedding_worker not in {"oneshot", "resident"}:
            raise ValueError("Unsupported query embedding worker")
        if type(self.embedding_queue_size) is not int or not 1 <= self.embedding_queue_size <= 256:
            raise ValueError("Invalid embedding queue budget")
        if self.embedding_revision is not None and (
            not self.embedding_revision.strip() or len(self.embedding_revision) > 200
        ):
            raise ValueError("Invalid embedding revision")
        for address in (self.base_url, self.embedding_base_url):
            if address is None:
                continue
            url = urlsplit(address)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
            ):
                raise ValueError("AI base URL must be HTTPS, or loopback HTTP, without URL credentials")
        if any(
            not value.strip() or any(c in value for c in "\r\n")
            for value in (self.api_key, self.chat_model, self.embedding_model)
        ):
            raise ValueError("AI credentials and both model names must be explicitly configured")
        if not 1 <= self.embedding_dimensions <= 16384:
            raise ValueError("AI embedding dimensions must be between 1 and 16384")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 90:
            raise ValueError("AI operation timeout must be between 0 and 90 seconds")
        if not 200 <= self.chunk_chars <= 4000 or not 0 <= self.chunk_overlap < self.chunk_chars:
            raise ValueError("Invalid AI chunk size/overlap")
        if self.chunk_strategy not in {"auto", "character-v1", "sentence-token-v2"}:
            raise ValueError("Unsupported AI chunk strategy")
        if (
            type(self.chunk_tokens) is not int
            or type(self.chunk_token_overlap) is not int
            or not (32 <= self.chunk_tokens <= 16384 and 0 <= self.chunk_token_overlap < self.chunk_tokens)
        ):
            raise ValueError("Invalid AI chunk token budget/overlap")
        if self.active_chunk_strategy == "sentence-token-v2" and self.embedding_backend != "fastembed":
            raise ValueError("Sentence token chunking requires the prepared local embedding tokenizer")
        if not 1 <= self.max_candidates <= 10000 or not 4000 <= self.max_context_chars <= 200000:
            raise ValueError("Invalid AI corpus/context budget")
        if not math.isfinite(self.min_similarity) or not -1 <= self.min_similarity <= 1:
            raise ValueError("Invalid AI cosine threshold")
        if self.embedding_backend not in {"remote", "fastembed"}:
            raise ValueError("Unsupported embedding backend")
        if self.embedding_backend == "fastembed" and self.local_model_dir is None:
            raise ValueError("Local embedding backend requires a prepared model directory")
        if (self.embedding_base_url is None) != (self.embedding_api_key is None):
            raise ValueError("An independent embedding service requires both its own URL and credentials")
        if self.embedding_api_key is not None and (
            not self.embedding_api_key.strip() or any(c in self.embedding_api_key for c in "\r\n")
        ):
            raise ValueError("Invalid embedding credentials")
        if self.token_limit_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("Unsupported token limit parameter")
        if self.response_format not in {"json_schema", "json_object"}:
            raise ValueError("Unsupported model response format")

    @classmethod
    def from_env(cls):
        from app.secrets import read_secret

        try:
            return cls(
                base_url=os.environ["PAPERPILOT_AI_BASE_URL"].rstrip("/"),
                api_key=read_secret("PAPERPILOT_AI_API_KEY"),
                chat_model=os.environ["PAPERPILOT_AI_CHAT_MODEL"],
                embedding_model=os.environ["PAPERPILOT_AI_EMBEDDING_MODEL"],
                embedding_dimensions=int(os.environ["PAPERPILOT_AI_EMBEDDING_DIMENSIONS"]),
                embedding_revision=os.getenv("PAPERPILOT_AI_EMBEDDING_REVISION") or None,
                chunk_strategy=os.getenv("PAPERPILOT_AI_CHUNK_STRATEGY", "auto"),
                chunk_tokens=int(os.getenv("PAPERPILOT_AI_CHUNK_TOKENS", "384")),
                chunk_token_overlap=int(os.getenv("PAPERPILOT_AI_CHUNK_TOKEN_OVERLAP", "48")),
                lexical_backend=os.getenv("PAPERPILOT_AI_LEXICAL_BACKEND", "inverted"),
                lexical_cache_mb=int(os.getenv("PAPERPILOT_AI_LEXICAL_CACHE_MB", "256")),
                query_embedding_worker=os.getenv("PAPERPILOT_AI_QUERY_EMBEDDING_WORKER", "resident"),
                embedding_queue_size=int(os.getenv("PAPERPILOT_AI_EMBEDDING_QUEUE_SIZE", "16")),
                timeout_seconds=float(os.getenv("PAPERPILOT_AI_TIMEOUT_SECONDS", "45")),
                min_similarity=float(os.getenv("PAPERPILOT_AI_MIN_SIMILARITY", "0.2")),
                embedding_backend=os.getenv("PAPERPILOT_AI_EMBEDDING_BACKEND", "remote"),
                embedding_base_url=os.getenv("PAPERPILOT_AI_EMBEDDING_BASE_URL") or None,
                embedding_api_key=read_secret("PAPERPILOT_AI_EMBEDDING_API_KEY")
                if os.getenv("PAPERPILOT_AI_EMBEDDING_API_KEY")
                or os.getenv("PAPERPILOT_AI_EMBEDDING_API_KEY_FILE")
                else None,
                local_model_dir=Path(os.environ["PAPERPILOT_AI_LOCAL_MODEL_DIR"])
                if os.getenv("PAPERPILOT_AI_LOCAL_MODEL_DIR")
                else None,
                token_limit_parameter=os.getenv("PAPERPILOT_AI_TOKEN_LIMIT_PARAMETER", "max_tokens"),
                response_format=os.getenv("PAPERPILOT_AI_RESPONSE_FORMAT", "json_schema"),
            )
        except (KeyError, ValueError):
            raise RuntimeError("Invalid or missing PAPERPILOT_AI configuration; startup refused") from None
