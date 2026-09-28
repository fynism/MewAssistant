import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.services.rate_limits import enforce_rate_limits


class RateLimitTests(unittest.TestCase):
    def test_user_and_key_limits_are_checked_together(self):
        seen = []

        class FakeRedis:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def eval(self, script, count, *args):
                seen.append((script, count, args))
                return 2

        with patch("backend.services.rate_limits.Redis.from_url", return_value=FakeRedis()):
            with self.assertRaises(HTTPException) as denied:
                enforce_rate_limits(("mcp:user:1", 60), ("mcp:key:key-id", 30))
        self.assertEqual(denied.exception.status_code, 429)
        self.assertEqual(denied.exception.headers["Retry-After"], "60")
        self.assertEqual(seen[0][1], 2)
        self.assertEqual(seen[0][2][-2:], (60, 30))


if __name__ == "__main__":
    unittest.main()
