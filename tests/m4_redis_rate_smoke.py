"""Verify user/key limits atomically against a running local Redis."""

import sys
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.config import settings
from backend.services.rate_limits import enforce_rate_limits


def main() -> None:
    identity = uuid4().hex
    user = f"mcp:user:m4-smoke-{identity}"
    key = f"mcp:key:m4-smoke-{identity}"
    names = [f"{settings.redis_key_prefix}:rate:{name}" for name in (user, key)]
    with Redis.from_url(settings.redis_url) as redis:
        try:
            enforce_rate_limits((user, 2), (key, 1))
            assert [int(redis.get(name)) for name in names] == [1, 1]
            try:
                enforce_rate_limits((user, 2), (key, 1))
            except HTTPException as exc:
                assert exc.status_code == 429
            else:
                raise AssertionError("Key rate limit did not reject")
            assert [int(redis.get(name)) for name in names] == [1, 1]
            print("Redis M4 rate limit passed; rejected call did not consume either counter")
        finally:
            redis.delete(*names)


if __name__ == "__main__":
    main()
