"""Run from the repo root: python test/test_cache.py (no image or server needed)."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
from main import resolve_model_args as use_cached_model  # noqa: E402


def touch(root, *parts):
    path = os.path.join(root, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


ARGS = ["-hf", "org/Repo-GGUF:Q4_K_M", "-c", "8192", "-ngl", "99"]
with tempfile.TemporaryDirectory() as cache:
    check("no cache -> unchanged", use_cached_model(ARGS, cache) == ARGS)
    snap = ("models--org--Repo-GGUF", "snapshots", "abc123")
    touch(cache, *snap, "Repo-Q8_0.gguf")
    check("cached repo without the wanted quant -> unchanged", use_cached_model(ARGS, cache) == ARGS)
    want = touch(cache, *snap, "Repo-Q4_K_M.gguf")
    check("cached quant -> -m path, other args kept", use_cached_model(ARGS, cache) == ["-m", want, "-c", "8192", "-ngl", "99"])
    check("quant match ignores case", use_cached_model(["-hf", "org/Repo-GGUF:q4_k_m"], cache) == ["-m", want])
    check("no -hf -> unchanged", use_cached_model(["-m", "x.gguf"], cache) == ["-m", "x.gguf"])
    check("-hf with no value -> unchanged", use_cached_model(["-c", "1", "-hf"], cache) == ["-c", "1", "-hf"])

with tempfile.TemporaryDirectory() as cache:
    snap = ("models--org--Big", "snapshots", "abc")
    touch(cache, *snap, "Big-Q4_K_M-00002-of-00002.gguf")
    first = touch(cache, *snap, "Big-Q4_K_M-00001-of-00002.gguf")
    check("split model -> first shard", use_cached_model(["-hf", "org/Big:Q4_K_M"], cache) == ["-m", first])

with tempfile.TemporaryDirectory() as cache:
    snap = ("models--org--Repo-GGUF", "snapshots", "abc")
    touch(cache, *snap, "Repo-Q8_0.gguf")
    touch(cache, *snap, "mmproj-Repo-Q4_K_M.gguf")
    q4 = touch(cache, *snap, "Repo-Q4_K_M.gguf")
    check("no model args -> the cached default quant, other args kept", use_cached_model(["-c", "4096"], cache) == ["-m", q4, "-c", "4096"])
    check("no model args, other quant requested", use_cached_model([], cache, quant="Q8_0") == ["-m", os.path.join(cache, *snap, "Repo-Q8_0.gguf")])
    check("explicit -m wins over the cache", use_cached_model(["-m", "mine.gguf"], cache) == ["-m", "mine.gguf"])
    check("-hf for a repo that is not cached is left to download", use_cached_model(["-hf", "other/Repo:Q4_K_M"], cache) == ["-hf", "other/Repo:Q4_K_M"])

with tempfile.TemporaryDirectory() as cache:
    try:
        use_cached_model([], cache, wait_s=0)
        check("no model anywhere exits with a message", False)
    except SystemExit as e:
        check("no model anywhere exits with a message", "no model" in str(e) and "empty" in str(e))

    naps = []
    def fake_sleep(_s):
        naps.append(_s)
        if len(naps) == 2:  # the cache fills in while the worker waits
            touch(cache, "models--org--Late", "snapshots", "abc", "Late-Q4_K_M.gguf")
    got = use_cached_model([], cache, wait_s=60, sleep=fake_sleep)
    check("waits for the cache to fill, then uses it", got[0] == "-m" and got[1].endswith("Late-Q4_K_M.gguf") and len(naps) == 2)
print("all cache checks passed")
