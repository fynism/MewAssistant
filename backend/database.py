from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import settings

DATABASE_URL = settings.database_url

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if not inspector.has_table("alembic_version"):
        raise RuntimeError("数据库尚未迁移：先运行 uv run alembic upgrade head")
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if version != "0001_m1":
        raise RuntimeError("数据库版本不是 M1：先运行 uv run alembic upgrade head")
