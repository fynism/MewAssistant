"""Explicitly import a named legacy file into an owner-approved private KB.

Read-only listing: uv run python -m backend.legacy_import list
Dry run: uv run python -m backend.legacy_import import --admin USER --owner USER --knowledge ID --filename FILE
Apply: append --apply. No legacy data is imported implicitly.
"""

import argparse
import asyncio
import io
import json
import ntpath
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from backend.core.config import settings
from backend.database import SessionLocal
from backend.milvus_client import MilvusManager
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.services.knowledge_service import process_document, save_upload

LEGACY_FILES = Path(__file__).resolve().parents[1] / "data" / "documents"


def legacy_names() -> set[str]:
    client = MilvusManager(collection_name=settings.milvus_collection)
    if not client.has_collection():
        return set()
    return {row["filename"] for row in client.query_all(output_fields=["filename"]) if row.get("filename")}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    imp = sub.add_parser("import")
    imp.add_argument("--admin", required=True)
    imp.add_argument("--owner", required=True)
    imp.add_argument("--knowledge", required=True)
    imp.add_argument("--filename", required=True)
    imp.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    names = legacy_names()
    if args.action == "list":
        for name in sorted(names):
            print(name)
        return
    if args.filename != ntpath.basename(args.filename) or args.filename not in names:
        raise SystemExit("文件名不在旧索引清单中")
    path = (LEGACY_FILES / args.filename).resolve()
    try:
        path.relative_to(LEGACY_FILES.resolve())
    except ValueError:
        raise SystemExit("旧文件路径越界")
    if not path.is_file():
        raise SystemExit("找不到旧原文件，需人工处理；不会根据旧向量推断归属")
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == args.admin, User.role == "admin").first()
        owner = db.query(User).filter(User.username == args.owner).first()
        if admin is None or owner is None:
            raise SystemExit("管理员或目标用户不存在")
        knowledge = db.query(KnowledgeBase).filter(KnowledgeBase.id == args.knowledge,
            KnowledgeBase.owner_id == owner.id, KnowledgeBase.status == "active").first()
        if knowledge is None:
            raise SystemExit("目标知识库不存在或不属于目标用户")
        existing = db.query(KnowledgeDocument).filter(
            KnowledgeDocument.legacy_filename == args.filename,
            KnowledgeDocument.knowledge_id == knowledge.id).first()
        if existing:
            print(f"已导入：{existing.id} ({existing.status})")
            return
        print(f"旧文件 {args.filename} -> 用户 {owner.username} / 知识库 {knowledge.id}")
        if not args.apply:
            print("仅预览；添加 --apply 才会执行")
            return
        with path.open("rb") as raw:
            upload = UploadFile(file=io.BytesIO(raw.read()), filename=args.filename)
        item = asyncio.run(save_upload(db, owner.id, knowledge.id, upload))
        item.legacy_filename = args.filename
        db.commit()
        process_document(item.id)
        db.refresh(item)
        audit = LEGACY_FILES.parent / "legacy_import_audit.jsonl"
        with audit.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                "admin": admin.username, "owner": owner.username, "knowledge_id": knowledge.id,
                "legacy_filename": args.filename, "document_id": item.id, "status": item.status},
                ensure_ascii=False) + "\n")
        print(f"导入结果：{item.id} ({item.status})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
