"""Run inside the image: python /test/test_handler.py (starts the stub itself)."""
import asyncio, json, os, subprocess, sys, time

os.environ["LLAMA_PORT"] = "8099"
sys.path.insert(0, "/worker")
from handler import handler, normalize  # noqa: E402

stub = subprocess.Popen(["/test/stub_llama_server.py", "--port", "8099"])
time.sleep(4)


async def collect(job_input):
    return [out async for out in handler({"input": job_input})]


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


msgs = [{"role": "user", "content": "hi"}]
check("normalize openai_input", normalize({"openai_route": "/v1/chat/completions", "openai_input": {"messages": msgs}}) == ("/v1/chat/completions", "POST", {"messages": msgs}))
check("normalize default route", normalize({"openai_input": {}})[0] == "/v1/chat/completions")
check("normalize models route", normalize({"openai_route": "/v1/models"}) == ("/v1/models", "GET", None))
check("normalize shorthand merges sampling_params", normalize({"messages": msgs, "sampling_params": {"temperature": 0.1}})[2] == {"messages": msgs, "temperature": 0.1})
check("normalize prompt shorthand", normalize({"prompt": "x"})[0] == "/v1/completions")
try:
    normalize({"nonsense": 1}); check("normalize rejects unknown input", False)
except ValueError:
    check("normalize rejects unknown input", True)

out = asyncio.run(collect({"openai_route": "/v1/chat/completions", "openai_input": {"messages": msgs}}))
check("non-streaming yields the parsed json once", len(out) == 1 and out[0]["choices"][0]["message"]["content"] == "Hello there")

out = asyncio.run(collect({"openai_route": "/v1/chat/completions", "openai_input": {"messages": msgs, "stream": True}}))
text = "".join(out)
pieces = [json.loads(l[5:])["choices"][0]["delta"]["content"] for l in text.splitlines() if l.startswith("data:") and "[DONE]" not in l]
check("streaming yields raw SSE text with every piece and [DONE]", "".join(pieces) == "Hello there" and text.rstrip().endswith("data: [DONE]"))
check("streaming arrives in more than one chunk", len(out) > 1)

out = asyncio.run(collect({"openai_route": "/v1/models"}))
check("GET route", out[0]["data"][0]["id"] == "stub-model")
out = asyncio.run(collect({"openai_route": "/v1/chat/completions", "openai_input": {"bad": True}}))
check("upstream 4xx becomes an error payload", out[0]["error"]["status"] == 400)
out = asyncio.run(collect({"nonsense": 1}))
check("bad input becomes an error payload", "error" in out[0])
stub.kill()
stub.terminate()
print("all handler checks passed")
