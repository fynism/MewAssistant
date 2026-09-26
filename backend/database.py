from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import settings
from backend.observability import enable_sql_logging

DATABASE_URL = settings.database_url

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
if settings.log_sql:
    enable_sql_logging(engine, include_parameters=settings.log_sql_parameters)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        raise RuntimeError("数据库尚未迁移：先运行 uv run alembic upgrade head")
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if version != "0007_m4_call_audits":
        raise RuntimeError("数据库版本不是 M4：先运行 uv run alembic upgrade head")
    columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
    if "title" not in columns:
        raise RuntimeError("chat_sessions.title 缺失：先运行 uv run alembic upgrade head")
