"""Prepare a T2Retrieval subset, then exercise SuperMew's HTTP pipeline.

Preparation requires requests and pyarrow; running requires only requests.
No application imports, credentials in files, or changes to server settings.
"""

import argparse
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time

import requests


DEFAULT_PACK = Path("data/benchmarks/t2-smoke")


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def http(session, method, url, **kwargs):
    # Polling can race a server's idle-connection timeout; only reads are replayable.
    for attempt in range(6):
        try:
            response = session.request(method, url, timeout=180, **kwargs)
        except requests.ConnectionError:
            if method != "GET" or attempt >= 2:
                raise
            time.sleep(0.5 * (attempt + 1))
            continue
        if response.status_code != 429 or attempt == 5:
            break
        time.sleep(min(60, max(1, int(response.headers.get("Retry-After", "60")))))
        for upload in kwargs.get("files", {}).values():
            upload[1].seek(0)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code} at {url}: {response.text[:400]}")
    response.encoding = "utf-8"
    return response


def prepare(args):
    import pyarrow.parquet as pq

    if args.queries < 1 or args.docs < 1:
        raise ValueError("queries and docs must be positive")
    pack = args.pack
    if (pack / "manifest.json").exists():
        raise ValueError(f"Pack already exists: {pack}. Use a different --pack directory.")
    cache = pack.parent / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    revisions = {}

    def table(dataset, split):
        api = f"https://huggingface.co/api/datasets/{dataset}"
        revision = revisions.get(dataset) or http(session, "GET", api).json()["sha"]
        revisions[dataset] = revision
        entries = http(session, "GET", f"{api}/tree/{revision}",
                       params={"recursive": "true"}).json()
        paths = sorted(e["path"] for e in entries
                       if e["path"].startswith(f"data/{split}-")
                       and e["path"].endswith(".parquet"))
        if not paths:
            raise ValueError(f"No parquet files for {dataset}/{split}")
        for remote in paths:
            entry = next(e for e in entries if e["path"] == remote)
            local = cache / f"{dataset.replace('/', '--')}--{revision}--{Path(remote).name}"
            if not local.exists():
                print(f"Downloading {dataset}/{split}: {entry['size'] / 1e6:.1f} MB", flush=True)
                url = f"https://huggingface.co/datasets/{dataset}/resolve/{revision}/{remote}"
                with session.get(url, stream=True, timeout=(30, 120)) as response:
                    response.raise_for_status()
                    temp = local.with_suffix(".part")
                    with temp.open("wb") as output:
                        for chunk in response.iter_content(1024 * 1024):
                            output.write(chunk)
                if temp.stat().st_size != entry["size"]:
                    raise ValueError(f"Incomplete download: {temp}")
                expected = entry.get("lfs", {}).get("oid")
                with temp.open("rb") as source:
                    if expected and hashlib.file_digest(source, "sha256").hexdigest() != expected:
                        raise ValueError(f"Download checksum mismatch: {temp}")
                temp.replace(local)
            for batch in pq.ParquetFile(local).iter_batches(batch_size=1024):
                yield from batch.to_pylist()

    queries = list(table("C-MTEB/T2Retrieval", "queries"))
    qrels = {}
    for row in table("C-MTEB/T2Retrieval-qrels", "dev"):
        if float(row["score"]) > 0:
            qrels.setdefault(str(row["qid"]), set()).add(str(row["pid"]))
    eligible = [q for q in queries if str(q["id"]) in qrels and 0 < len(q["text"].strip()) <= 500]
    base_manifest = None
    base_ids = set()
    if getattr(args, "base_pack", None):
        base_manifest = json.loads((args.base_pack / "manifest.json").read_text(encoding="utf-8"))
        if base_manifest["source_revisions"] != revisions:
            raise ValueError("Base pack uses different source revisions")
        base_queries = base_manifest["queries"]
        base_ids = set(base_manifest["documents"])
        if len(base_queries) > args.queries or len(base_ids) > args.docs:
            raise ValueError("Expanded pack must contain the entire base pack")
        query_ids = {q["id"] for q in base_queries}
        selected = [q for q in queries if str(q["id"]) in query_ids]
        remaining = [q for q in eligible if str(q["id"]) not in query_ids]
        selected += random.Random(args.seed).sample(remaining, args.queries - len(selected))
    else:
        selected = random.Random(args.seed).sample(eligible, args.queries)
    positive_ids = set().union(*(qrels[str(q["id"])] for q in selected))
    required_ids = positive_ids | base_ids
    if len(required_ids) >= args.docs:
        raise ValueError(f"Selected queries and base pack need {len(required_ids)} documents. "
                         "Increase --docs or reduce --queries; leave room for distractors.")
    # Reservoir sampling avoids keeping the entire corpus in memory.
    rng = random.Random(args.seed + 1)
    positives, distractors = {}, []
    capacity = args.docs - len(required_ids)
    seen = total = 0
    for row in table("C-MTEB/T2Retrieval", "corpus"):
        pid = str(row["id"])
        total += 1
        if pid in required_ids:
            positives[pid] = row["text"]
        else:
            seen += 1
            if len(distractors) < capacity:
                distractors.append((pid, row["text"]))
            else:
                position = rng.randrange(seen)
                if position < capacity:
                    distractors[position] = (pid, row["text"])
    if required_ids - positives.keys():
        raise ValueError("Some gold documents are missing from the source corpus")
    documents = {**positives, **dict(distractors)}
    if len(documents) != args.docs:
        raise ValueError("Not enough corpus documents")
    hashes = [hashlib.sha256(text.encode()).hexdigest() for text in documents.values()]
    if len(set(hashes)) != len(hashes):
        raise ValueError("Subset contains identical files rejected by upload deduplication. Try another --seed.")
    texts = pack / "documents"
    texts.mkdir(parents=True, exist_ok=True)
    for pid, text in documents.items():
        (texts / f"t2_{pid}.txt").write_bytes(text.encode("utf-8"))
    manifest = {
        "benchmark": f"T2Retrieval custom subset ({args.queries} queries, {args.docs} documents; not full benchmark)",
        "seed": args.seed, "source_revisions": revisions, "source_corpus_size": total,
        "document_count": len(documents), "positive_document_count": len(positive_ids),
        "distractor_count": len(documents) - len(positive_ids),
        "documents": {pid: f"t2_{pid}.txt" for pid in documents},
        "queries": [{"id": str(q["id"]), "text": q["text"],
                     "relevant_ids": sorted(qrels[str(q["id"])])} for q in selected],
    }
    if base_manifest:
        manifest["base_query_ids"] = [q["id"] for q in base_manifest["queries"]]
        manifest["base_document_count"] = len(base_ids)
    save_json(pack / "manifest.json", manifest)
    print(f"Prepared {len(selected)} queries, {len(positive_ids)} positive documents, "
          f"{len(documents) - len(positive_ids)} distractors in {pack}", flush=True)


def score(ranked, relevant, k):
    """Cut at K returned chunks, then deduplicate document IDs for scoring.

    Repeated chunks consume retrieval slots but never count as multiple hits.
    Duplicate documents have zero additional gain and still consume rank slots.
    """
    seen, gains = set(), []
    for pid in ranked[:k]:
        gains.append(pid in relevant and pid not in seen)
        seen.add(pid)
    hits = seen & relevant
    first = next((i for i, gain in enumerate(gains, 1) if gain), None)
    dcg = sum(1 / math.log2(i + 2) for i, gain in enumerate(gains) if gain)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {f"Recall@{k}": len(hits) / len(relevant), f"Hit@{k}": float(bool(hits)),
            f"MRR@{k}": 1 / first if first else 0.0,
            f"nDCG@{k}": dcg / ideal if ideal else 0.0}


def run(args):
    pack = args.pack
    query_interval = getattr(args, "query_interval", 0)
    if query_interval < 0:
        raise ValueError("query interval must be nonnegative")
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    session = requests.Session()
    base = args.base_url.rstrip("/")
    token = os.environ.get("SUPERMEW_BENCHMARK_TOKEN")
    if not token:
        username = args.username or input("SuperMew username: ")
        token = http(session, "POST", f"{base}/auth/login", json={
            "username": username, "password": getpass.getpass("Password (hidden): ")}).json()["access_token"]
    session.headers["Authorization"] = f"Bearer {token}"
    limits = http(session, "GET", f"{base}/knowledge-settings").json()
    if limits["max_retrieval_results"] < 10:
        raise ValueError("Server must support topK >= 10 for this run")
    manifest_hash = hashlib.sha256((pack / "manifest.json").read_bytes()).hexdigest()
    state_path = pack / "upload-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["base_url"] != base or state["manifest_sha256"] != manifest_hash:
            raise ValueError("Saved upload state belongs to another server or test pack")
    else:
        knowledges = http(session, "GET", f"{base}/knowledges").json()["knowledges"]
        occupied = sum(len(http(session, "GET", f"{base}/knowledges/{k['id']}/documents")
                           .json()["documents"]) for k in knowledges)
        if occupied + manifest["document_count"] > limits["max_user_documents"]:
            raise ValueError("Account has insufficient document slots. Use a test account or prepare a smaller pack.")
        knowledge = http(session, "POST", f"{base}/knowledges", json={
            "name": f"T2Retrieval {len(manifest['queries'])}q/{manifest['document_count']}d",
            "description": manifest["benchmark"]}).json()
        state = {"base_url": base, "manifest_sha256": manifest_hash, "knowledge_id": knowledge["id"]}
        save_json(state_path, state)
    kid = state["knowledge_id"]
    print(f"Test knowledge ID: {kid}", flush=True)
    path = f"{base}/knowledges/{kid}/documents"
    existing = http(session, "GET", path).json()["documents"]
    by_filename = {d["filename"]: d for d in existing}
    required = set(manifest["documents"].values())
    if set(by_filename) - required or len(existing) != len(by_filename):
        raise ValueError("Test knowledge contains unexpected or duplicate files")
    missing = required - by_filename.keys()
    for filename in sorted(missing):
        with (pack / "documents" / filename).open("rb") as source:
            result = http(session, "POST", path, files={"file": (filename, source, "text/plain")}).json()
        by_filename[filename] = result
        print(f"Uploaded {len(by_filename)}/{len(required)}: {filename}", flush=True)
    deadline = time.monotonic() + args.wait_seconds
    while True:
        documents = http(session, "GET", path).json()["documents"]
        if len(documents) != len(required) or {d["filename"] for d in documents} != required:
            raise ValueError("Uploaded corpus differs from the manifest")
        failed = [d for d in documents if d["status"] not in {"pending", "processing", "ready"}]
        if failed:
            examples = [{key: d.get(key) for key in ("filename", "status", "error_summary")}
                        for d in failed[:5]]
            raise ValueError(f"{len(failed)} documents not ready; no benchmark score emitted. "
                             f"First examples: {examples}")
        ready = sum(d["status"] == "ready" for d in documents)
        print(f"Ready: {ready}/{len(required)}", flush=True)
        if ready == len(required):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Documents still processing. Rerun the same command to resume.")
        time.sleep(5)
    filename_ids = {name: pid for pid, name in manifest["documents"].items()}
    ids = {d["id"]: filename_ids[d["filename"]] for d in documents}
    rows = []
    for index, query in enumerate(manifest["queries"]):
        if index and query_interval:
            time.sleep(query_interval)
        started = time.perf_counter()
        result = http(session, "POST", f"{base}/tools/debug/retrieve", json={
            "query": query["text"], "knowledgeIds": [kid], "topK": 10}).json()
        elapsed = time.perf_counter() - started
        if result.get("status") not in {"ok", "no_match"}:
            raise ValueError(f"Unexpected retrieval status: {result.get('status')}")
        ranked = [ids[item["documentId"]] for item in result["results"]]
        metrics = {}
        for k in (5, 10):
            metrics.update(score(ranked, set(query["relevant_ids"]), k))
        rows.append({**query, "returned_ids": ranked, "latency_seconds": elapsed, **metrics})
        print(f"Query {query['id']}: Recall@10={metrics['Recall@10']:.3f}", flush=True)
    summary = {key: statistics.mean(row[key] for row in rows) for key in metrics}
    summary["mean_request_seconds_including_throttle_wait"] = statistics.mean(r["latency_seconds"] for r in rows)
    report = {"benchmark": manifest["benchmark"], "knowledge_id": kid,
              "document_count": len(documents), "query_count": len(rows),
              "query_interval_seconds": query_interval,
              "note": "Custom subset. TopK is a chunk budget; document IDs are deduplicated within it. "
                      "Each query requests topK=10; @5 scores its prefix. "
                      "Not a full-corpus score, independent topK=5 run, or load test.",
              "source_revisions": manifest["source_revisions"], "summary": summary, "queries": rows}
    save_json(pack / "results.json", report)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"Report: {pack / 'results.json'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    prep.add_argument("--queries", type=int, default=10)
    prep.add_argument("--docs", type=int, default=80)
    prep.add_argument("--seed", type=int, default=42)
    prep.add_argument("--base-pack", type=Path,
                      help="Retain all queries and documents from an earlier pack")
    execute = sub.add_parser("run")
    execute.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    execute.add_argument("--base-url", default="http://127.0.0.1:8000")
    execute.add_argument("--username")
    execute.add_argument("--wait-seconds", type=int, default=1800)
    execute.add_argument("--query-interval", type=float, default=0,
                         help="Extra seconds between queries, excluded from request latency")
    args = parser.parse_args()
    try:
        (prepare if args.command == "prepare" else run)(args)
    except (ValueError, RuntimeError, TimeoutError, requests.RequestException) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
