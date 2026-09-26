"""Opt-in Milvus smoke test. Uses and removes a unique temporary collection."""

from uuid import uuid4

from backend.milvus_client import MilvusManager


def main():
    name = "m1_smoke_" + uuid4().hex[:12]
    manager = MilvusManager(collection_name=name)
    try:
        manager.init_collection(dense_dim=4)
        rows = []
        for idx, (kid, did, text) in enumerate([
            ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "11111111-1111-1111-1111-111111111111", "Alice"),
            ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "22222222-2222-2222-2222-222222222222", "Bob"),
        ]):
            rows.append({
                "dense_embedding": [1.0, 0.0, 0.0, 0.0] if idx == 0 else [0.0, 1.0, 0.0, 0.0],
                "sparse_embedding": {idx + 1: 1.0}, "text": text,
                "filename": "same.pdf", "knowledge_id": kid, "document_id": did,
                "file_type": "PDF", "file_path": "", "page_number": 0,
                "chunk_idx": 0, "chunk_id": f"{did}::p0::l3::0",
                "parent_chunk_id": "", "root_chunk_id": "", "chunk_level": 3,
            })
        manager.insert(rows)
        manager.flush()
        expr = 'chunk_level == 3 and document_id in ["11111111-1111-1111-1111-111111111111"]'
        hybrid = manager.hybrid_retrieve([1.0, 0.0, 0.0, 0.0], {1: 1.0}, filter_expr=expr)
        dense = manager.dense_retrieve([1.0, 0.0, 0.0, 0.0], filter_expr=expr)
        for mode, result in [("hybrid", hybrid), ("dense", dense)]:
            assert len(result) == 1 and result[0]["document_id"] == rows[0]["document_id"], (mode, result)
        print("Milvus M1 hybrid/dense filter smoke passed")
    finally:
        manager.drop_collection()


if __name__ == "__main__":
    main()
