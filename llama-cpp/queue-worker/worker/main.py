"""Start llama-server, wait until it can answer, then register with Runpod's queue.
Registering last means a worker only shows as ready once the model is loaded, like the vLLM worker."""
import glob
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BIN = os.getenv("LLAMA_SERVER_BIN", "/app/llama-server")
PORT = int(os.getenv("LLAMA_PORT", "8081"))
STARTUP_TIMEOUT_S = int(os.getenv("STARTUP_TIMEOUT", "1800"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "4"))  # llama-server runs 4 slots by default
# Where Runpod puts a model named in the endpoint's "Model" field (Hugging Face cache layout).
HF_CACHE = os.getenv("HF_CACHE_DIR", "/runpod-volume/huggingface-cache/hub")
# Picks one file when the cached repo holds several quantizations.
DEFAULT_QUANT = os.getenv("LLAMA_QUANT", "Q4_K_M")
# How long to wait for Runpod to finish caching the endpoint's Model when no model argument is given.
CACHE_WAIT_S = int(os.getenv("CACHE_WAIT", "300"))
MODEL_FLAGS = {"-m", "--model", "-hf", "-hfr", "--hf-repo", "-mu", "--model-url"}


def cached_ggufs(cache, repo="*", quant=""):
    """GGUF files in the Hugging Face cache layout, optionally limited to one repo and quantization. A split
    model is several files; only the first is returned, since llama-server finds the rest next to it."""
    pattern = os.path.join(cache, "models--" + repo.replace("/", "--"), "snapshots", "*", "*.gguf")
    files = sorted(
        f for f in glob.glob(pattern)
        if (not quant or quant.lower() in os.path.basename(f).lower()) and "mmproj" not in os.path.basename(f).lower()
    )
    return [f for f in files if not re.search(r"-0000[2-9]-of-", f)]


def _listing(cache):
    try:
        return sorted(os.listdir(cache)) or "empty"
    except OSError:
        return "missing"


def _model_env_names():
    """Names only (values could hold tokens)."""
    return sorted(k for k in os.environ if re.search(r"MODEL|HF_|HUGGING", k)) or "none"


def resolve_model_args(args, cache=HF_CACHE, quant=DEFAULT_QUANT, wait_s=CACHE_WAIT_S, sleep=time.sleep):
    """The llama-server arguments with a model in them. The model is normally named once, in the endpoint's
    "Model" field, which Runpod caches on the host; then no model argument is needed and the cached file is used.
    An explicit -m/-hf argument wins (a cached `-hf` repo is swapped for its file so nothing is downloaded).
    Exits with a clear message when no model can be found, rather than serving with none."""
    if "-hf" in args and args.index("-hf") + 1 < len(args):
        at = args.index("-hf")
        repo, _, repo_quant = args[at + 1].partition(":")
        files = cached_ggufs(cache, repo, repo_quant)
        if not files:
            return args  # not cached on this host: let llama-server download it
        print(f"using cached model {files[0]}", flush=True)
        return args[:at] + ["-m", files[0]] + args[at + 2:]
    if MODEL_FLAGS & set(args):
        return args
    files = cached_ggufs(cache, quant=quant)
    waited = 0
    while not files and waited < wait_s:  # the cache may still be filling when the container starts
        print(f"waiting for a cached model ({waited}s)", flush=True)
        sleep(5)
        waited += 5
        files = cached_ggufs(cache, quant=quant)
    if not files:
        sys.exit(f"no model: pass -hf/-m, or set the endpoint's Model field. Looked for {quant} .gguf files in {cache} "
                 f"for {waited}s. Cache contents: {_listing(cache)}. Model-related env vars: {_model_env_names()}")
    print(f"using cached model {files[0]}", flush=True)
    return ["-m", files[0]] + args


def wait_until_healthy(proc):
    deadline = time.time() + STARTUP_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit(f"llama-server exited with code {proc.returncode} before it was ready")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as res:
                if res.status == 200:
                    return
        except (urllib.error.URLError, OSError):  # refused while starting, 503 while loading the model
            pass
        time.sleep(1)
    proc.kill()
    sys.exit("llama-server did not become healthy in time")


def main():
    # Our own --host/--port go last so they win over any in the container start command.
    proc = subprocess.Popen([BIN, *resolve_model_args(sys.argv[1:]), "--host", "127.0.0.1", "--port", str(PORT)])
    wait_until_healthy(proc)
    sys.argv = sys.argv[:1]  # the Runpod SDK parses argv; llama-server's options aren't its
    import runpod
    from handler import handler
    runpod.serverless.start({
        "handler": handler,
        "concurrency_modifier": lambda _current: MAX_CONCURRENCY,
        "return_aggregate_stream": True,
    })


if __name__ == "__main__":
    main()
