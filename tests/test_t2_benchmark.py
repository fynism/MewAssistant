import importlib.util
import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests


spec = importlib.util.spec_from_file_location(
    "t2_benchmark", Path(__file__).resolve().parents[1] / "benchmarks" / "t2_retrieval.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class RetrievalMetricsTest(unittest.TestCase):
    def test_query_pacing_is_outside_request_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            manifest = {"benchmark": "fixture", "document_count": 1,
                        "documents": {"a": "a.txt"}, "source_revisions": {},
                        "queries": [{"id": str(i), "text": "query", "relevant_ids": ["a"]}
                                    for i in range(2)]}
            benchmark.save_json(pack / "manifest.json", manifest)
            benchmark.save_json(pack / "upload-state.json", {
                "base_url": "http://example.test", "knowledge_id": "kb",
                "manifest_sha256": hashlib.sha256((pack / "manifest.json").read_bytes()).hexdigest()})
            document = {"id": "doc", "filename": "a.txt", "status": "ready"}
            events, clocks = [], iter([10, 11, 30, 32])
            def http(session, method, url, **kwargs):
                if url.endswith("knowledge-settings"):
                    payload = {"max_retrieval_results": 20}
                elif method == "GET":
                    payload = {"documents": [document]}
                else:
                    events.append("request")
                    payload = {"status": "ok", "results": [{"documentId": "doc"}]}
                return Mock(json=lambda: payload)
            def clock():
                events.append("clock")
                return next(clocks)
            with patch.dict(benchmark.os.environ, {"SUPERMEW_BENCHMARK_TOKEN": "fixture"}), \
                    patch.object(benchmark, "http", side_effect=http), \
                    patch.object(benchmark.time, "perf_counter", side_effect=clock), \
                    patch.object(benchmark.time, "sleep", side_effect=lambda _: events.append("sleep")), \
                    patch("builtins.print"):
                benchmark.run(argparse.Namespace(pack=pack, base_url="http://example.test",
                                                username=None, wait_seconds=1, query_interval=4))
            result = json.loads((pack / "results.json").read_text())
            self.assertEqual(events, ["clock", "request", "clock", "sleep", "clock", "request", "clock"])
            self.assertEqual(result["summary"]["mean_request_seconds_including_throttle_wait"], 1.5)
            self.assertEqual(result["query_interval_seconds"], 4)

    @patch.object(benchmark.time, "sleep")
    def test_reconnects_reads_but_does_not_replay_posts(self, sleep):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        session = Mock()
        session.request.side_effect = [requests.ConnectionError("reset"), response]
        self.assertIs(benchmark.http(session, "GET", "http://example.test/documents"), response)
        self.assertEqual(session.request.call_count, 2)
        session.reset_mock()
        session.request.side_effect = requests.ConnectionError("reset")
        with self.assertRaises(requests.ConnectionError):
            benchmark.http(session, "POST", "http://example.test/upload")
        self.assertEqual(session.request.call_count, 1)

    def test_duplicate_chunks_do_not_inflate_recall_or_ndcg(self):
        result = benchmark.score(["a", "a", "b"], {"a", "b"}, 2)
        self.assertEqual(result["Recall@2"], 0.5)
        self.assertAlmostEqual(result["nDCG@2"], 1 / (1 + 1 / math.log2(3)))

    def test_duplicates_still_consume_rank_and_result_budget(self):
        result = benchmark.score(["x", "x", "a"], {"a", "b"}, 3)
        self.assertEqual(result["MRR@3"], 1 / 3)
        self.assertEqual(result["Recall@3"], 0.5)
        self.assertEqual(benchmark.score(["x", "a"], {"a"}, 1)["Hit@1"], 0)

    def test_perfect_ranking_and_empty_results(self):
        self.assertEqual(benchmark.score(["a", "b"], {"a", "b"}, 10)["nDCG@10"], 1)
        self.assertTrue(all(value == 0 for value in benchmark.score([], {"a"}, 10).values()))

    def test_http_failure_is_not_converted_to_a_miss(self):
        response = requests.Response()
        response.status_code = 503
        response._content = b'{"detail":"unavailable"}'
        session = Mock()
        session.request.return_value = response
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            benchmark.http(session, "POST", "http://example.test/retrieve")
        self.assertEqual(session.request.call_count, 1)

    @patch.object(benchmark.time, "sleep")
    def test_throttle_retries_without_scoring_the_throttle_response(self, sleep):
        throttled, success = requests.Response(), requests.Response()
        throttled.status_code = 429
        throttled.headers["Retry-After"] = "1"
        success.status_code = 200
        success._content = b'{"status":"ok","results":[]}'
        session = Mock()
        session.request.side_effect = [throttled, success]
        self.assertEqual(benchmark.http(session, "POST", "http://example.test/retrieve").json()["status"], "ok")
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
