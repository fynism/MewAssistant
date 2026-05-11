import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/langchain_app",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    # Delayed import to avoid circular dependency.
    from backend import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS reasoning_content TEXT"))
        conn.commit()
