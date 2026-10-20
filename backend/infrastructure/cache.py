import json
from typing import Any, Optional

import redis

from backend.core.config import settings


class RedisCache:
    def __init__(self):
        self.redis_url = settings.redis_url
        self.key_prefix = settings.redis_key_prefix
        self.default_ttl = settings.redis_cache_ttl_seconds
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def get_json(self, key: str) -> Optional[Any]:
        try:
            value = self._get_client().get(self._key(key))
            if not value:
                return None
            return json.loads(value)
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False)
            self._get_client().setex(self._key(key), ttl or self.default_ttl, payload)
        except Exception:
            return

    def delete(self, key: str) -> None:
        try:
            self._get_client().delete(self._key(key))
        except Exception:
            return

    def delete_pattern(self, pattern: str) -> None:
        try:
            full_pattern = self._key(pattern)
            keys = self._get_client().keys(full_pattern)
            if keys:
                self._get_client().delete(*keys)
        except Exception:
            return


cache = RedisCache()
