import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    log_requests: bool = os.getenv("LOG_REQUESTS", "true").lower() == "true"
    log_sql: bool = os.getenv("LOG_SQL", "false").lower() == "true"
    log_sql_parameters: bool = os.getenv("LOG_SQL_PARAMETERS", "false").lower() == "true"

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/langchain_app",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_key_prefix: str = os.getenv("REDIS_KEY_PREFIX", "supermew")
    redis_cache_ttl_seconds: int = int(os.getenv("REDIS_CACHE_TTL_SECONDS", "300"))

    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "change-this-secret")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
    password_pbkdf2_rounds: int = int(os.getenv("PASSWORD_PBKDF2_ROUNDS", "310000"))

    mcp_public_base_url: str = os.getenv("MCP_PUBLIC_BASE_URL", "")
    mcp_allowed_hosts: str = os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,testserver")
    mcp_allowed_origins: str = os.getenv("MCP_ALLOWED_ORIGINS", "")
    mcp_external_enabled: bool = os.getenv("MCP_EXTERNAL_ENABLED", "false").lower() == "true"

    ark_api_key: str | None = os.getenv("ARK_API_KEY")
    model: str | None = os.getenv("MODEL")
    base_url: str | None = os.getenv("BASE_URL")
    grade_model: str = os.getenv("GRADE_MODEL", "gpt-4.1")

    amap_weather_api: str | None = os.getenv("AMAP_WEATHER_API")
    amap_api_key: str | None = os.getenv("AMAP_API_KEY")

    milvus_host: str = os.getenv("MILVUS_HOST", "localhost")
    milvus_port: str = os.getenv("MILVUS_PORT", "19530")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "embeddings_collection")
    milvus_m1_collection: str = os.getenv("MILVUS_M1_COLLECTION", "embeddings_collection_m1")
    dense_embedding_dim: int = int(os.getenv("DENSE_EMBEDDING_DIM", "1024"))

    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "cpu")
    bm25_state_path: Path = Path(os.getenv("BM25_M1_STATE_PATH", BASE_DIR / "data" / "bm25_state_m1.json"))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

    rerank_model: str | None = os.getenv("RERANK_MODEL")
    rerank_binding_host: str | None = os.getenv("RERANK_BINDING_HOST")
    rerank_api_key: str | None = os.getenv("RERANK_API_KEY")
    auto_merge_enabled: bool = os.getenv("AUTO_MERGE_ENABLED", "true").lower() != "false"
    auto_merge_threshold: int = int(os.getenv("AUTO_MERGE_THRESHOLD", "2"))
    leaf_retrieve_level: int = int(os.getenv("LEAF_RETRIEVE_LEVEL", "3"))


settings = Settings()
