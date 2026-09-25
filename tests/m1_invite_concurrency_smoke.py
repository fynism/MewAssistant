"""Opt-in PostgreSQL row-lock check for a one-use invitation."""

import asyncio
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg2
from psycopg2 import sql

name = "m1_invite_" + uuid4().hex[:10]
admin_connection = psycopg2.connect(host="127.0.0.1", user="postgres", password="postgres", dbname="postgres")
admin_connection.autocommit = True
try:
    with admin_connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    os.environ["DATABASE_URL"] = f"postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/{name}"

    from fastapi import HTTPException
    from backend.database import Base, SessionLocal, engine
    from backend.models import Invitation, User
    from backend.routers.auth import register
    from backend.schemas import RegisterRequest

    Base.metadata.create_all(engine)
    code = "one-use-" + uuid4().hex
    with SessionLocal() as db:
        admin = User(username="admin", password_hash="unused", role="admin")
        db.add(admin)
        db.flush()
        db.add(Invitation(id=str(uuid4()), code_hash=hashlib.sha256(code.encode()).hexdigest(),
                          created_by=admin.id, max_uses=1, used_count=0))
        db.commit()

    def redeem(username):
        with SessionLocal() as db:
            try:
                asyncio.run(register(RegisterRequest(
                    username=username, password="secret", invite_code=code), db=db))
                return "success"
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, ["alice", "bob"]))
    with SessionLocal() as db:
        invitation = db.query(Invitation).one()
        users = db.query(User).filter(User.role == "user").count()
        assert sorted(map(str, results)) == ["403", "success"], results
        assert invitation.used_count == 1 and users == 1, (invitation.used_count, users)
    print("M1 concurrent invitation redemption smoke passed")
finally:
    if "engine" in globals():
        engine.dispose()
    with admin_connection.cursor() as cursor:
        cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    admin_connection.close()
