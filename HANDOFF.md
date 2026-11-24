# Handoff

## Langfuse tracing and single-query evaluation

- The running server must include the tracing code and use the same Langfuse project as the evaluator. A successful request against an older or disabled server has no trace ID and cannot be scored on its existing trace.
- MCP returns the trace ID in `structuredContent.traceId`. Chat returns it in `X-Langfuse-Trace-Id`. The evaluator checks stream completion before scoring, and the dashboard may need a refresh after batch ingestion.
- The smoke score checks score delivery. MeowMCP only returns retrieved chunks, so its optional Ragas score uses reference document IDs. Faithfulness uses the chat stream's final retrieved chunks and complete answer.
- A 500-iteration local check measured median tracing overhead of 0.46 ms for six metadata spans with the in-memory exporter. This excludes retrieval, network and optional content capture. Test first-byte latency and throughput on staging after enabling real export.

## Multi-format preview verification (2026-11-24)

- Word ingestion and preview share a private PDF stored under the original file's storage key. Preserve `.previews` when moving the original storage directory. Existing Word and Excel files can open before reprocessing. Reprocess them to build page or cell-range citations from the new extractors.
- The Windows LibreOffice check used the official 26.8.3.1 runtime extracted into a temporary directory, with the `program` directory on `PATH`. The Windows Docker engine was unavailable. Local Office integration used temporary SQLite, real JWT and document parsers, with vector storage and rate limits replaced by test fixtures.
- The same two-page Word fixture took 12.98 s on its first on-demand conversion and PNG response and 41 ms when cached. In a two-sheet 120-by-23 workbook, XLSX preview requests took 31–32 ms and BIFF8 XLS requests took 222 ms. Excel responses contained up to 40 rows by 20 columns. These are local single-fixture timings.
- The legacy M1 test expected `GET /documents` to return 404. Commit `dd86ad8` added the authenticated, account-scoped listing. The old assertion was removed, and `test_m2_tools` still covers authentication and document ownership.
- A local backend process can serve freshly edited frontend files while retaining its original API routes. If Excel preview returns 404, check for `/spreadsheet-preview` in `/openapi.json`. Verify uploads and previews against a fresh process backed by isolated test data.
- Valid Strict Open XML workbooks can return 422 because openpyxl finds no worksheets in the Strict namespace. This response comes from the registered preview route. An old process without the route returns 404.

## T2Retrieval evaluation

- The source pack is `data/benchmarks/t2-smoke/`: 10 queries, 80 documents, 45 labeled positive documents and 35 random distractors. All labeled positives for the selected queries are present. This small corpus is a smoke test, not a full benchmark. The completed evaluation must use `data/benchmarks/t2-isolated/`; the first shared-environment attempt was interrupted before scoring.
- Run `benchmarks/t2_retrieval.py` from the repository root. Results and the source revisions are recorded in the pack. The `data/` directory is ignored by Git.
- HTTP retrieval returns chunks. Repeated chunks from one document consume the top-K budget but must not increase document recall. Scores at 5 use the prefix of a topK=10 request; the server's candidate pool changes with topK, so they do not represent an independent topK=5 request.
- Uploads are limited to 10 per minute by default. A new account can hold 100 files. Wait for every file to become `ready`; failed or absent documents invalidate the run. The script resumes uploads using `upload-state.json`.
- The isolated test uses port 8001, a migrated SQLite fixture, a separate Milvus collection, a separate BM25 state file and a separate Redis key prefix. Its non-secret environment overrides are in `data/benchmarks/t2-isolated/test-environment.json`. Only fixture upload throttling is disabled; model and retrieval settings remain inherited from the project.
- The local test uses a dedicated ordinary account; its username and ID are in `runtime.json`. No passwords or bearer tokens are saved. The test knowledge ID is in `upload-state.json`.

## Local execution pitfalls

- `.env.example` includes empty model and embedding values; empty environment variables override code defaults, and an empty `DENSE_EMBEDDING_DIM` fails integer parsing. Fill the required values before startup. The root README includes a minimal configuration.
- `/docs` is ignored except for already-tracked files. The former README linked an untracked deployment guide, and `docs/deploy/DEPLOY_DOCKER.md` describes obsolete commands. Use the root README and current `deploy.sh` for the two-environment release flow; README links must resolve in a fresh checkout.
- `.codegraph/` exists, but neither the `codegraph` shell command nor a CodeGraph MCP tool was available during this task. Attempt CodeGraph first, then use source inspection if it remains unavailable.
- A Chromium stress check reproduced two Element Plus `offsetHeight` errors on both `d75fb79` and `a467b3c` when alternating routes six times with only Vue's `nextTick` between switches. Six switches spaced 38 ms apart passed. Compare programmatic route stress failures with the base commit when checking for regressions.
- An application can finish starting between a readiness check and a new launch. Check the listener PID and process logs; a successful readiness response alone does not prove the newly spawned process owns the port. Do not run multiple maintenance workers against the same database. During this test an additional launch exited because port 8000 was already occupied; the existing listener was reused.
- A listener on port 8000 belonged to `C:/Users/fengy/.codex/worktrees/a68e/SuperMew`, at a different Git revision. It shared PostgreSQL and Milvus with the main checkout but had separate uploaded files and BM25 vocabulary state. After it stopped, recovery from the main checkout failed to find its files. Never mix these storage roots or vocabularies when evaluating; use the isolated fixture. No scores were emitted from that interrupted attempt.
- The measured application uses local BGE-M3 on CPU and a configured Jina reranker. Record actual returned rerank scores, because configured reranking can fall back after an upstream error. Request latency on a small serial test is not a load-test result.
- Windows polling occasionally reset an idle HTTP connection while the service stayed alive. The execution driver reconnected read-only GET requests only; uploads and retrieval POSTs were not replayed after ambiguous failures.

## Completed isolated result (2026-10-26)

- Report: `data/benchmarks/t2-isolated/report.md`; raw results: `results.json`; missed IDs: `analysis.json`. All 80 documents were ready and all 10 retrieval requests succeeded. Every query returned rerank scores.
- Macro Recall@5: 76.92%; Recall@10: 89.42%; Hit@10: 100%; MRR@10: 1.0; nDCG@10: 0.8984. Seven queries recovered every labeled positive.
- Average request time: 1.55 seconds, range 0.99–2.61 seconds. Fixture ingestion took 822.69 seconds (13.71 minutes), with upload throttling disabled and two document-processing slots.
- Missed-query recall: 肢体综合训练器的类别有哪些 4/6; 富贵竹怎么养才能生根 7/8; 厨房抽拉水龙头排名 2/5. These are retrieval results, not generated-answer accuracy.
- The first attempt's dedicated ordinary account and test knowledge remain in the shared development environment as an interrupted attempt. Its uploaded files are in the other worktree. It has no results file and must not be reused as a completed benchmark or mixed with the isolated fixture.

## Expanded evaluation

- Pack: `data/benchmarks/t2-100q-1000d/`: 100 queries, 1,000 documents, 590 labeled positive documents and 410 other documents. All 10 baseline queries and all 80 baseline documents are retained byte-for-byte. Compare the matched 10 questions separately from the full 100-question average.
- A CUDA-enabled PyTorch 2.14.0 runtime was installed only in `data/benchmarks/gpu-runtime/`, loaded through the test process's PYTHONPATH. The normal project environment still uses CPU PyTorch. GPU: RTX 4060 Laptop, 8 GiB. The fixture uses its own SQLite database, Milvus collection, BM25 file and Redis prefix; API port 8002.
- A concurrent flush probe took 27.06 seconds while the sequential ingestion path was active. The GPU was mostly idle. Per-file synchronous flush makes a large retrieval fixture unnecessarily slow; do not attribute that wait to embedding inference.
- Expanded fixture construction is offline: reuse the unchanged `process_document`, defer flush only in the private builder, flush each 100 documents and finally verify every document has vectors before serving any retrieval requests. Encoding batch size is 8. The web worker was drained before stopping, avoiding interrupted BM25 updates. Product ingestion code is unchanged; bulk construction time is not production upload throughput.
- Batch-construction harness and trace launcher are saved inside the expanded pack as `bulk_index.py` and `serve.py`. No query scores were collected during the abandoned sequential preparation phase.
- The offline phase completed 976 remaining documents in 579.46 seconds, after the initial 24 had completed normally. Final verification found 5,634 distinct leaf chunks covering every one of the 1,000 documents. This is a bulk fixture-construction measurement, not product upload throughput.
- GPU model reload once took over three minutes. A 40-second readiness window was too short; the process later became healthy without restarting. Check logs and readiness rather than equating an early timeout with process failure.

## Completed expanded result (2026-10-26)

- Report: `data/benchmarks/t2-100q-1000d/report.md`; raw quality results: `results.json`; comparison and missed IDs: `analysis.json`. All 100 quality-run requests used hybrid retrieval and successful reranking, with zero rerank errors. The test service was stopped after completion; the isolated index and data remain available.
- Quality run: Recall@5 72.05%; Recall@10 82.63%; Hit@10 99%; MRR@10 0.9833; nDCG@10 0.8602. Forty-eight questions recovered every labeled positive; one question had no relevant hit.
- Same 10 baseline questions in the expanded corpus: Recall@10 88.17%, compared with 89.42% in the 80-document corpus, a decline of 1.25 percentage points. Do not interpret the full 100-question mean as a paired comparison against the original 10 questions.
- Mean request time 1.725 seconds, P50 1.541, descriptive P95 3.192, maximum 4.990. The quality run included an extra four-second wait between questions, outside request timing: 396 seconds of deliberate pacing, 172.505 seconds of requests, 568.664 seconds total wall time. It is not a concurrency test.
- Initial continuous serial requests hit the upstream rerank token limit (3 HTTP 429s) and TLS/network errors (49), producing Recall@10 82.12% with 52 rerank fallbacks. This full initial run is preserved in `continuous-run/`; the paced rerun is a separate complete 100-question run, not a per-question selection of better answers. The observed upstream limit was 100,000 tokens/minute.
- CPU/GPU equivalence sample: 256 matching leaf texts; minimum cosine 0.999999999975, mean 0.999999999997. Model and float precision were retained; GPU/bulk timing still cannot be directly compared with the earlier CPU/per-file timing.
- The benchmark now supports nested `--base-pack` preparation and `--query-interval` pacing. Safe GET reconnects do not replay POST uploads. Seven targeted tests pass, including pacing outside request timing. The original project CPU PyTorch was left unchanged; CUDA dependencies are confined to `data/benchmarks/gpu-runtime/`.

## Markdown previews

- Upload and ingestion are supported by commit `96c6921`. The frontend reads original bytes through the authenticated content route and renders Markdown with the bundled Marked and DOMPurify only when a preview is opened. Uploaded Markdown preview strips inline HTML and displays image alt text so opening a document does not fetch external images.
- The repository virtual environment has unittest and does not include pytest. Run the ingestion suite with `python -m unittest discover -s tests -p test_document_ingestion.py`.
