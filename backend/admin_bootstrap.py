"""Create the first administrator after `alembic upgrade head`.

Run interactively: uv run python -m backend.admin_bootstrap --username admin
"""

import argparse
import getpass

from backend.auth import get_password_hash
from backend.database import SessionLocal
from backend.models import User


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Admin password: ")
    if len(password) < 12:
        raise SystemExit("管理员密码至少 12 个字符")
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == args.username).first():
            raise SystemExit("用户名已存在；不会覆盖现有账户")
        if db.query(User).filter(User.role == "admin").first():
            raise SystemExit("管理员已存在；请使用现有账户发放邀请码")
        db.add(User(username=args.username, password_hash=get_password_hash(password), role="admin"))
        db.commit()
        print("管理员已创建")
    finally:
        db.close()


if __name__ == "__main__":
    main()
