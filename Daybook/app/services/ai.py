"""Local-only AI client (Ollama, or any OpenAI-compatible local server such as
LM Studio, llama.cpp, MLX or Jan).

Privacy guarantee: before every request the endpoint's hostname is resolved
and *every* address must be on this machine, the LAN, or a Tailscale network.
Public endpoints (OpenAI, Anthropic, Google, etc.) are refused outright —
there is no setting to turn this off.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

_ALLOWED_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
)]


class AIError(Exception):
    pass


class AIBlocked(AIError):
    pass


class AIUnavailable(AIError):
    pass


def check_local_endpoint(base_url: str) -> dict:
    """Is this endpoint on this computer / LAN / tailnet? Never trust a name alone."""
    try:
        parsed = urlparse(base_url or "")
    except ValueError:
        return {"ok": False, "reason": "That address isn't a valid URL."}
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "reason": "Use an http:// or https:// address, like http://127.0.0.1:11434."}
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return {"ok": False, "host": host, "reason": f"Couldn't find {host} on your network."}
    addrs = sorted({i[4][0] for i in infos})
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not any(ip in net for net in _ALLOWED_NETS):
            return {
                "ok": False, "host": host, "addresses": addrs,
                "reason": f"{host} is on the public internet ({a}). Daybook only talks to AI running on your own devices.",
            }
    kind = "this computer" if all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addrs) else "your network"
    return {"ok": True, "host": host, "addresses": addrs, "where": kind}


_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_think(text: str) -> str:
    text = _THINK_BLOCK.sub("", text or "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


class ThinkFilter:
    """Removes <think>…</think> reasoning from a token stream."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.inside = False
        self.started = False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = ""
        while True:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i == -1:
                    self.buf = self.buf[-len(self.CLOSE):]
                    break
                self.buf = self.buf[i + len(self.CLOSE):]
                self.inside = False
                continue
            i = self.buf.find(self.OPEN)
            if i == -1:
                keep = 0
                for k in range(1, len(self.OPEN)):
                    if self.buf.endswith(self.OPEN[:k]):
                        keep = k
                out += self.buf[: len(self.buf) - keep]
                self.buf = self.buf[len(self.buf) - keep:]
                break
            out += self.buf[:i]
            self.buf = self.buf[i + len(self.OPEN):]
            self.inside = True
        if not self.started:
            out = out.lstrip()
            if out:
                self.started = True
        return out

    def flush(self) -> str:
        out = "" if self.inside else self.buf
        self.buf = ""
        return out


def extract_json(text: str):
    text = strip_think(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidates = [text, fence.group(1) if fence else None]
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except ValueError:
            pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:j + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise AIError("The model didn't return valid JSON. Try again, or pick a larger model.")


EMBED_HINTS = ("embed", "bge", "minilm", "e5-", "gte", "nomic", "arctic-embed", "snowflake")
VISION_HINTS = ("llava", "vision", "-vl", "vl:", "moondream", "minicpm-v", "gemma3", "qwen2.5vl", "qwen3-vl",
                "llama4", "mistral-small3", "granite3.2-vision", "bakllava")


def classify_model(name: str) -> str:
    n = name.lower()
    if any(h in n for h in EMBED_HINTS):
        return "embedding"
    if any(h in n for h in VISION_HINTS):
        return "vision"
    return "chat"


class AIClient:
    def __init__(self, provider: str, base_url: str, *, think: bool = False, num_ctx: int = 8192,
                 temperature: float = 0.3, timeout: float = 600.0) -> None:
        self.provider = provider if provider in ("ollama", "openai") else "ollama"
        self.base_url = (base_url or "").rstrip("/")
        self.think = think
        self.num_ctx = int(num_ctx or 8192)
        self.temperature = float(temperature if temperature is not None else 0.3)
        self.timeout = httpx.Timeout(connect=5.0, read=timeout, write=60.0, pool=10.0)

    @classmethod
    def from_settings(cls, settings: dict) -> "AIClient":
        ai = settings["ai"]
        return cls(ai.get("provider", "ollama"), ai.get("base_url", ""), think=bool(ai.get("think")),
                   num_ctx=ai.get("num_ctx", 8192), temperature=ai.get("temperature", 0.3))

    # -- plumbing -----------------------------------------------------------
    def _guard(self) -> None:
        res = check_local_endpoint(self.base_url)
        if not res["ok"]:
            raise AIBlocked(res["reason"])

    def _oa_base(self) -> str:
        return self.base_url if self.base_url.endswith("/v1") else self.base_url + "/v1"

    def _client(self) -> httpx.AsyncClient:
        # trust_env=False: never route local AI traffic through a system proxy.
        return httpx.AsyncClient(timeout=self.timeout, trust_env=False, follow_redirects=False)

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error") or resp.text
                if isinstance(detail, dict):
                    detail = detail.get("message") or json.dumps(detail)
            except ValueError:
                detail = resp.text
            raise AIError(f"The local AI server replied with an error: {str(detail)[:300]}")

    # -- discovery --------------------------------------------------------------
    async def models(self) -> list[dict]:
        self._guard()
        try:
            async with self._client() as c:
                if self.provider == "ollama":
                    r = await c.get(self.base_url + "/api/tags")
                    self._raise_for(r)
                    out = []
                    for m in r.json().get("models", []):
                        d = m.get("details") or {}
                        out.append({"name": m["name"], "size": m.get("size", 0), "family": d.get("family"),
                                    "parameters": d.get("parameter_size"), "quantization": d.get("quantization_level"),
                                    "kind": classify_model(m["name"])})
                    return sorted(out, key=lambda x: x["name"])
                r = await c.get(self._oa_base() + "/models")
                self._raise_for(r)
                return sorted(({"name": m["id"], "size": 0, "kind": classify_model(m["id"])} for m in r.json().get("data", [])),
                              key=lambda x: x["name"])
        except httpx.HTTPError as exc:
            raise AIUnavailable(self._unreachable_msg()) from exc

    def _unreachable_msg(self) -> str:
        app = "Ollama" if self.provider == "ollama" else "your local AI server"
        return f"Couldn't reach {app} at {self.base_url}. Make sure it's running."

    async def status(self) -> dict:
        t0 = time.perf_counter()
        local = check_local_endpoint(self.base_url)
        out = {"provider": self.provider, "base_url": self.base_url, "local": local, "ok": False, "models": []}
        if not local["ok"]:
            out["error"] = local["reason"]
            return out
        try:
            out["models"] = await self.models()
            out["ok"] = True
            if self.provider == "ollama":
                try:
                    async with self._client() as c:
                        r = await c.get(self.base_url + "/api/version")
                        out["version"] = r.json().get("version")
                except (httpx.HTTPError, ValueError):
                    pass
        except AIError as exc:
            out["error"] = str(exc)
        out["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        return out

    # -- chat -----------------------------------------------------------------
    def _ollama_payload(self, messages, model, temperature, json_schema, stream, think):
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "keep_alive": "15m",
            "options": {"temperature": self.temperature if temperature is None else temperature, "num_ctx": self.num_ctx},
        }
        if json_schema is not None:
            payload["format"] = json_schema
        if think is not None:
            payload["think"] = think
        return payload

    def _openai_messages(self, messages):
        out = []
        for m in messages:
            if m.get("images"):
                parts = [{"type": "text", "text": m.get("content", "")}]
                parts += [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}} for img in m["images"]]
                out.append({"role": m["role"], "content": parts})
            else:
                out.append({"role": m["role"], "content": m.get("content", "")})
        return out

    async def chat(self, messages: list[dict], *, model: str, temperature: float | None = None,
                   json_schema: dict | None = None) -> str:
        if not model:
            raise AIUnavailable("Choose a model in Settings → Local AI first.")
        self._guard()
        try:
            async with self._client() as c:
                if self.provider == "ollama":
                    think = True if self.think else False
                    for attempt in range(3):
                        r = await c.post(self.base_url + "/api/chat",
                                         json=self._ollama_payload(messages, model, temperature, json_schema, False, think))
                        if r.status_code >= 400:
                            txt = r.text.lower()
                            if "think" in txt and think is not None:
                                think = None
                                continue
                            if json_schema not in (None, "json") and ("format" in txt or "schema" in txt):
                                json_schema = "json"
                                continue
                        self._raise_for(r)
                        break
                    return strip_think((r.json().get("message") or {}).get("content", ""))
                body = {"model": model, "messages": self._openai_messages(messages), "stream": False,
                        "temperature": self.temperature if temperature is None else temperature}
                if json_schema is not None:
                    body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": json_schema}}
                r = await c.post(self._oa_base() + "/chat/completions", json=body)
                if r.status_code == 400 and json_schema is not None:
                    body.pop("response_format", None)
                    r = await c.post(self._oa_base() + "/chat/completions", json=body)
                self._raise_for(r)
                choice = (r.json().get("choices") or [{}])[0]
                return strip_think((choice.get("message") or {}).get("content", ""))
        except httpx.TimeoutException as exc:
            raise AIUnavailable("The local model took too long to answer. Try a smaller model.") from exc
        except httpx.HTTPError as exc:
            raise AIUnavailable(self._unreachable_msg()) from exc

    async def stream_chat(self, messages: list[dict], *, model: str, temperature: float | None = None) -> AsyncIterator[str]:
        if not model:
            raise AIUnavailable("Choose a model in Settings → Local AI first.")
        self._guard()
        filt = ThinkFilter()
        try:
            async with self._client() as c:
                if self.provider == "ollama":
                    think = True if self.think else False
                    for attempt in range(2):
                        payload = self._ollama_payload(messages, model, temperature, None, True, think)
                        async with c.stream("POST", self.base_url + "/api/chat", json=payload) as r:
                            if r.status_code >= 400:
                                txt = (await r.aread()).decode("utf-8", "ignore")
                                if "think" in txt.lower() and think is not None and attempt == 0:
                                    think = None
                                    continue
                                raise AIError(f"The local AI server replied with an error: {txt[:300]}")
                            async for line in r.aiter_lines():
                                if not line.strip():
                                    continue
                                try:
                                    data = json.loads(line)
                                except ValueError:
                                    continue
                                if data.get("error"):
                                    raise AIError(str(data["error"]))
                                piece = filt.feed((data.get("message") or {}).get("content", ""))
                                if piece:
                                    yield piece
                                if data.get("done"):
                                    break
                        break
                else:
                    body = {"model": model, "messages": self._openai_messages(messages), "stream": True,
                            "temperature": self.temperature if temperature is None else temperature}
                    async with c.stream("POST", self._oa_base() + "/chat/completions", json=body) as r:
                        if r.status_code >= 400:
                            txt = (await r.aread()).decode("utf-8", "ignore")
                            raise AIError(f"The local AI server replied with an error: {txt[:300]}")
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            chunk = line[5:].strip()
                            if chunk == "[DONE]":
                                break
                            try:
                                data = json.loads(chunk)
                            except ValueError:
                                continue
                            delta = ((data.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                            piece = filt.feed(delta)
                            if piece:
                                yield piece
            tail = filt.flush()
            if tail:
                yield tail
        except httpx.TimeoutException as exc:
            raise AIUnavailable("The local model took too long to answer. Try a smaller model.") from exc
        except httpx.HTTPError as exc:
            raise AIUnavailable(self._unreachable_msg()) from exc

    # -- embeddings -----------------------------------------------------------
    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        if not model:
            raise AIUnavailable("Choose an embedding model in Settings → Local AI.")
        self._guard()
        try:
            async with self._client() as c:
                if self.provider == "ollama":
                    r = await c.post(self.base_url + "/api/embed", json={"model": model, "input": texts, "keep_alive": "15m"})
                    if r.status_code == 404:  # older Ollama
                        out = []
                        for t in texts:
                            r2 = await c.post(self.base_url + "/api/embeddings", json={"model": model, "prompt": t})
                            self._raise_for(r2)
                            out.append(r2.json()["embedding"])
                        return out
                    self._raise_for(r)
                    return r.json()["embeddings"]
                r = await c.post(self._oa_base() + "/embeddings", json={"model": model, "input": texts})
                self._raise_for(r)
                return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d.get("index", 0))]
        except httpx.TimeoutException as exc:
            raise AIUnavailable("The embedding model took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise AIUnavailable(self._unreachable_msg()) from exc


def run_sync(coro):
    """Run a coroutine from a worker thread (no running event loop there)."""
    return asyncio.run(coro)


def pick_models(settings: dict, available: list[dict]) -> dict:
    """Configured models, falling back to sensible picks from what's installed."""
    ai = settings["ai"]
    chats = [m for m in available if m["kind"] in ("chat", "vision")]
    embeds = [m for m in available if m["kind"] == "embedding"]
    names = {m["name"] for m in available}
    by_size = sorted(chats, key=lambda m: m.get("size") or 0)

    def ok(n):
        return n if n and (not available or n in names) else ""

    smart = ok(ai.get("smart_model"))
    fast = ok(ai.get("fast_model"))
    if not smart and by_size:
        under = [m for m in by_size if (m.get("size") or 0) < 40e9] or by_size
        smart = under[-1]["name"]
    if not fast and by_size:
        fast = by_size[0]["name"]
    embed = ok(ai.get("embed_model")) or (embeds[0]["name"] if embeds else "")
    vision = ok(ai.get("vision_model"))
    return {"smart": smart or fast, "fast": fast or smart, "embed": embed, "vision": vision}
