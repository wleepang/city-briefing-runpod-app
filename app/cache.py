"""Tiny in-memory TTL cache. Also serves *stale* entries when the upstream is failing, which is the
cheapest way to survive a free-tier rate limit during a demo."""
import time
from collections import OrderedDict


class TtlCache:
    def __init__(self, max_size=500, now=time.monotonic):
        self._data = OrderedDict()
        self._max = max_size
        self._now = now

    def get(self, key):
        """Return (value, fresh) or None."""
        hit = self._data.get(key)
        if hit is None:
            return None
        value, expires = hit
        return value, expires > self._now()

    def set(self, key, value, ttl_s):
        if key not in self._data and len(self._data) >= self._max:
            self._data.popitem(last=False)  # evict oldest
        self._data[key] = (value, self._now() + ttl_s)


async def cached(cache, key, ttl_s, fn):
    """Fresh cached value, else await fn(). If fn raises and a stale value exists, return it.
    Returns (value, stale)."""
    hit = cache.get(key)
    if hit and hit[1]:
        return hit[0], False
    try:
        value = await fn()
    except Exception:
        if hit:
            return hit[0], True
        raise
    cache.set(key, value, ttl_s)
    return value, False
