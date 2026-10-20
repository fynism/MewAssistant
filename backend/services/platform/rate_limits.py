"""Atomic minute-window limits shared by all web workers."""

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError

from backend.core.config import settings

_SCRIPT = """
for i = 1, #KEYS do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  if current >= tonumber(ARGV[i]) then return i end
end
for i = 1, #KEYS do
  redis.call('INCR', KEYS[i])
  if redis.call('TTL', KEYS[i]) < 0 then redis.call('EXPIRE', KEYS[i], 60) end
end
return 0
"""


def enforce_rate_limits(*limits: tuple[str, int]) -> None:
    active = [(name, value) for name, value in limits if value > 0]
    if not active:
        return
    keys = [f"{settings.redis_key_prefix}:rate:{name}" for name, _ in active]
    try:
        with Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1) as redis:
            denied = redis.eval(_SCRIPT, len(keys), *keys, *(value for _, value in active))
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="限流服务暂时不可用") from exc
    if denied:
        raise HTTPException(status_code=429, detail="调用过于频繁，请稍后重试",
                            headers={"Retry-After": "60"})
