import pytest

from app.cache import TtlCache, cached

from ..conftest import run


def clocked(**kw):
    t = {"now": 0.0}
    return TtlCache(now=lambda: t["now"], **kw), t


def test_entries_are_fresh_until_the_ttl_then_kept_but_flagged_stale():
    cache, t = clocked()
    assert cache.get("k") is None
    cache.set("k", "v", ttl_s=10)
    assert cache.get("k") == ("v", True)
    t["now"] = 10
    assert cache.get("k") == ("v", False)


def test_the_oldest_entry_is_evicted_when_full_but_overwrites_do_not_evict():
    cache, _ = clocked(max_size=2)
    cache.set("a", 1, 10); cache.set("b", 2, 10)
    cache.set("a", 3, 10)  # overwrite, nothing evicted
    assert cache.get("a") == (3, True) and cache.get("b") is not None
    cache.set("c", 4, 10)
    assert cache.get("a") is None and cache.get("b") is not None and cache.get("c") is not None


def make_fn(results):
    calls = []

    async def fn():
        calls.append(1)
        r = results[min(len(calls), len(results)) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    return fn, calls


def test_cached_serves_fresh_entries_without_calling_and_refreshes_expired_ones():
    cache, t = clocked()
    fn, calls = make_fn(["v1", "v2"])
    assert run(cached(cache, "k", 10, fn)) == ("v1", False)
    assert run(cached(cache, "k", 10, fn)) == ("v1", False)
    assert len(calls) == 1
    t["now"] = 11
    assert run(cached(cache, "k", 10, fn)) == ("v2", False)
    assert len(calls) == 2


def test_a_failing_refresh_serves_the_stale_value_but_a_failure_with_nothing_cached_is_raised():
    cache, t = clocked()
    fn, _ = make_fn(["v1", RuntimeError("down")])
    run(cached(cache, "k", 10, fn))
    t["now"] = 11
    assert run(cached(cache, "k", 10, fn)) == ("v1", True)
    with pytest.raises(RuntimeError):
        run(cached(clocked()[0], "other", 10, make_fn([RuntimeError("down")])[0]))
