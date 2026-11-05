"""Model calls for every stage, whichever provider answers (#72).

The model string picks the provider by its prefix:
    claude-sonnet-5-5, anthropic:claude-...  the Claude API (ANTHROPIC_API_KEY)
    openai:<model>                           OpenAI (OPENAI_API_KEY)
    gemini:<model>                           Google, OpenAI-compatible endpoint (GEMINI_API_KEY)
    local:<model>                            any OpenAI-compatible server at [llm] local_base_url
                                             (Ollama, vLLM, LM Studio; LOCAL_LLM_API_KEY if it
                                             wants one)
Every stage keeps its own model key ([scoring] model_a, [segments] model, ...), so moving a
stage is one config line. The prompts are tuned on Claude: if another model needs different
wording, give that stage a new prompt version rather than editing one.

What differs per provider:
- effort: Claude's output_config.effort; OpenAI-compatible servers get reasoning_effort
  (low/medium/high), and a model that rejects it is asked again without it.
- structured output: native JSON schema on both; a server without json_schema gets JSON mode
  with the schema in the prompt. Answers from OpenAI-compatible servers are checked against
  the schema and sent back once with what was wrong.
- refusals: (None, meta) from json_call and ("", meta) from text_call everywhere; the
  server-side refusal fallback exists on the Claude API only.
- images: Claude content blocks ({"type": "image", "source": {"type": "base64", ...}}) are
  converted to image_url data URLs.
Prices are [llm] prices ($ per 1M input/output tokens, by model string), for cost lines only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

from .errors import ClipperError

PROVIDERS = {  # prefix -> (base URL, key env var)
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "local": ("", "LOCAL_LLM_API_KEY"),
    # Decision models (TypeSafe's Jev): typed answers with probabilities, no text. Only
    # LLM.decide() calls them; json_call/text_call refuse.
    "typesafe": ("https://api.typesafe.ai/v1", "TYPESAFE_API_KEY"),
}
DECIDERS = {"typesafe"}
EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
RETRY_STATUS = (408, 429, 500, 502, 503, 504, 529)
MODEL_KEY = re.compile(r"(^|_)model(_[ab])?$")


def prices(cfg: dict) -> dict:
    """The price table the client uses: the built-in defaults plus [llm.prices] (which
    replaces the nested default when load() merges clipper.toml)."""
    from .config import DEFAULTS

    return {**DEFAULTS["llm"]["prices"], **(cfg.get("llm") or {}).get("prices", {})}


def price_of(prices: dict, model: str) -> tuple[float, float] | None:
    """[llm] prices entry for a model string: exact, or the most specific listed prefix, keyed
    with or without the provider ("openai:gpt-5" matches "openai:gpt-5-mini-20260601", and a
    dated claude-sonnet-5-5-20260601 costs as claude-sonnet-5-5)."""
    name = split(model)[1]
    for key in (model, name):
        if key in prices:
            return tuple(prices[key])
    best = max((k for k in prices if model.startswith(k) or name.startswith(split(k)[1])),
               key=lambda k: len(split(k)[1]), default=None)
    return tuple(prices[best]) if best else None


def split(model: str) -> tuple[str, str]:
    """("anthropic" | "openai" | "gemini" | "local", the provider's own model name)."""
    prefix, _, name = model.partition(":")
    if name and (prefix in PROVIDERS or prefix == "anthropic"):
        return prefix, name
    return "anthropic", model


def is_llm(key: str, value: Any) -> bool:
    """A config value that names a chat model (not a TTS, Whisper or lip-sync model)."""
    return (isinstance(value, str) and bool(MODEL_KEY.search(key))
            and (value.startswith("claude-") or split(value)[0] != "anthropic"))


def configured(cfg: dict) -> dict[str, str]:
    """Every chat model the config names, as {"section.key" (or "shows.<s>.section.key"): model}."""
    from .config import _sections

    out = {f"{s}.{k}": v for s, sec in _sections(cfg) for k, v in sec.items() if is_llm(k, v)}
    for show, prof in cfg.get("shows", {}).items():
        for s, sec in prof.get("overrides", {}).items():
            out.update({f"shows.{show}.{s}.{k}": v for k, v in sec.items() if is_llm(k, v)})
    return out


def check_schema(schema: dict, v: Any, path: str = "$") -> list[str]:
    """What's wrong with `v` against the subset of JSON schema the stages use (type,
    required, properties, items, enum, anyOf)."""
    if "anyOf" in schema:
        branches = [check_schema(s, v, path) for s in schema["anyOf"]]
        return [] if any(not b for b in branches) else min(branches, key=len)
    t = schema.get("type")
    types = t if isinstance(t, list) else [t] if t else []
    py = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
          "boolean": bool, "null": type(None)}
    if types and not any(isinstance(v, py[x]) and not (isinstance(v, bool) and x in ("integer", "number"))
                         for x in types if x in py):
        return [f"{path} should be {'/'.join(types)}"]
    if "enum" in schema and v not in schema["enum"]:
        return [f"{path} should be one of {schema['enum']}"]
    errs = []
    if isinstance(v, dict):
        errs += [f"{path}.{k} is missing" for k in schema.get("required", []) if k not in v]
        for k, sub in schema.get("properties", {}).items():
            if k in v:
                errs += check_schema(sub, v[k], f"{path}.{k}")
    if isinstance(v, list) and "items" in schema:
        for i, x in enumerate(v):
            errs += check_schema(schema["items"], x, f"{path}[{i}]")
    return errs


def parse_json(text: str) -> Any:
    """The JSON object in an answer, ignoring a code fence or words around it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    i, j = text.find("{"), text.rfind("}")
    return json.loads(text[i:j + 1] if i >= 0 and j > i else text)


def _openai_content(user: str | list[dict]) -> str | list[dict]:
    if isinstance(user, str):
        return user
    out = []
    for b in user:
        if b["type"] == "text":
            out.append({"type": "text", "text": b["text"]})
        elif b["type"] == "image":
            s = b["source"]
            out.append({"type": "image_url",
                        "image_url": {"url": f"data:{s['media_type']};base64,{s['data']}"}})
    return out


class NoSchema(Exception):
    """The server doesn't take response_format json_schema."""


class LLM:
    """One client for every stage; routes each call by its model string. `cost` adds up
    every call made through it (for the stages' cost lines)."""

    def __init__(self, cfg: dict | str = ""):
        from .config import DEFAULTS

        if isinstance(cfg, dict):
            workspace_id, lc = cfg["scoring"]["workspace_id"], cfg.get("llm") or DEFAULTS["llm"]
            self.ledger = cfg["paths"].get("costs")  # [paths] costs: one line per call
        else:
            workspace_id, lc, self.ledger = cfg, DEFAULTS["llm"], None
        # Keys not scoped to a workspace must name one on every request.
        self.workspace_id = workspace_id or os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
        self.lc = {**DEFAULTS["llm"], **lc}
        self.prices = prices(cfg if isinstance(cfg, dict) else {})
        self.cost = 0.0
        self._lock = threading.Lock()
        self._client = None
        self._no_effort: set[str] = set()
        self._no_schema: set[str] = set()

    @property
    def client(self):
        """The Anthropic SDK client (Claude models only)."""
        if self._client is None:
            import anthropic

            headers = {"anthropic-workspace-id": self.workspace_id} if self.workspace_id else None
            self._client = anthropic.Anthropic(max_retries=4, default_headers=headers)
        return self._client

    def _account(self, model: str, tokens_in: int, tokens_out: int) -> dict:
        from . import costs

        pin, pout = price_of(self.prices, model) or (0.0, 0.0)
        usd = (tokens_in * pin + tokens_out * pout) / 1e6
        with self._lock:
            self.cost += usd
        costs.llm_call(self.ledger, split(model)[0], model, tokens_in, tokens_out, usd)
        return {"input_tokens": tokens_in, "output_tokens": tokens_out}

    # ---------- the calls the stages make ----------

    def json_call(self, model: str, system: str, user: str | list[dict], schema: dict,
                  effort: str, fallback: bool = False,
                  stream: bool = False) -> tuple[dict | None, dict]:
        """Structured-output call. Returns (parsed JSON or None on refusal, meta).
        stream=True for long inputs/outputs, so the request can't hit an HTTP timeout.
        fallback: Claude's server-side refusal fallback (ignored elsewhere)."""
        if split(model)[0] != "anthropic":
            return self._compat_json(model, system, user, schema, effort, 64000 if stream else 16000)
        kwargs = {
            "model": split(model)[1], "max_tokens": 64000 if stream else 16000, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        }
        if fallback:
            # Server-side refusal fallback (Claude API only); routes by refusal category.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            api = self.client.beta.messages
        else:
            api = self.client.messages
        if stream:
            with api.stream(**kwargs) as s:
                resp = s.get_final_message()
        else:
            resp = api.create(**kwargs)
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            meta["refusal"] = getattr(resp.stop_details, "category", None)
            return None, meta
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), meta

    def text_call(self, model: str, system: str, user: str | list[dict], effort: str,
                  max_tokens: int = 4000) -> tuple[str, dict]:
        """Plain text answer; "" on refusal."""
        if split(model)[0] != "anthropic":
            data, meta = self._compat(model, self._messages(system, user), effort, max_tokens)
            if meta.get("refusal") is not None:
                return "", meta
            return (data["choices"][0]["message"].get("content") or "").strip(), meta
        resp = self.client.messages.create(
            model=split(model)[1], max_tokens=max_tokens, system=system,
            output_config={"effort": effort}, messages=[{"role": "user", "content": user}])
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            return "", meta
        return "".join(b.text for b in resp.content if b.type == "text").strip(), meta

    def json_text(self, model: str, system: str, user: str, effort: str,
                  max_tokens: int = 32000) -> tuple[dict | None, dict]:
        """A JSON object without a schema (one too large for a structured-output grammar):
        the prompt describes the fields (and asks for JSON only) and the caller checks
        them. None on refusal."""
        if split(model)[0] != "anthropic":
            data, meta = self._compat(model, self._messages(system, user), effort, max_tokens,
                                      {"type": "json_object"})
            if meta.get("refusal") is not None:
                return None, meta
            return parse_json(data["choices"][0]["message"].get("content") or ""), meta
        with self.client.messages.stream(
                model=split(model)[1], max_tokens=max_tokens, system=system,
                output_config={"effort": effort}, messages=[{"role": "user", "content": user}]) as st:
            resp = st.get_final_message()
        meta = self._claude_meta(model, resp)
        if resp.stop_reason == "refusal":
            return None, meta
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        return parse_json("".join(b.text for b in resp.content if b.type == "text")), meta

    def decide(self, model: str, state: Any, questions: dict) -> tuple[dict, dict]:
        """A decision model (typesafe:jev-latest): typed questions about `state` (text or a
        JSON object), {name: {"type": "noul" | "choice" | "score", "instructions",
        "criteria"}}. Returns ({name: answer}, meta); a noul answer is {"noul": p}, a choice
        {"choice", "confidence", "probabilities"}, a score {"score", "confidence", ...}."""
        prov, name = split(model)
        if prov not in DECIDERS:
            raise ClipperError(f"{model}: not a decision model (use typesafe:<model>)")
        base, env = PROVIDERS[prov]
        key = os.environ.get(env, "")
        if not key:
            raise ClipperError(f"{model}: set the {env} environment variable")
        t0 = time.time()
        r = self._post(model, f"{base}/systemone", {"model": name, "state": state, "questions": questions},
                       {"Authorization": f"Bearer {key}"})
        if r.status_code == 401:
            raise ClipperError(f"{model}: {env} rejected (HTTP 401)")
        if not r.ok:
            raise RuntimeError(f"{model}: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        usage = data.get("usage") or {}
        meta = {"model": f"{prov}:{data.get('model') or name}", "latency_s": round(time.time() - t0, 3),
                **self._account(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))}
        return data.get("answers") or {}, meta

    def _post(self, model: str, url: str, body: dict, headers: dict):
        """POST with retries on network errors and RETRY_STATUS; returns the last response."""
        import requests

        for attempt in range(6):
            try:
                r = requests.post(url, json=body, headers=headers, timeout=self.lc["timeout_s"])
            except requests.RequestException as e:
                if attempt == 5:
                    raise RuntimeError(f"{model}: {type(e).__name__}: {e}") from None
                time.sleep(2 ** attempt)
                continue
            if r.status_code in RETRY_STATUS and attempt < 5:
                time.sleep(min(60.0, float(r.headers.get("retry-after") or 2 ** (attempt + 1))))
                continue
            return r
        raise RuntimeError(f"{model}: no response")  # unreachable: the loop returns or raises

    def preflight(self, model: str) -> None:
        """One tiny call so account problems (auth, workspace, credits, a wrong URL) stop the
        run with one clear line instead of 60 identical failures."""
        if split(model)[0] in DECIDERS:
            try:
                self.decide(model, "OK", {"ok": {"type": "noul", "instructions": "The text says OK."}})
            except RuntimeError as e:
                raise ClipperError(f"{model} unavailable: {e}") from None
            return
        if split(model)[0] != "anthropic":
            try:
                self._compat(model, [{"role": "user", "content": "Say OK."}], "low", 16)
            except RuntimeError as e:
                raise ClipperError(f"{model} unavailable: {e}") from None
            return
        import anthropic

        try:
            self.client.messages.create(model=split(model)[1], max_tokens=16,
                                        messages=[{"role": "user", "content": "Say OK."}])
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                anthropic.BadRequestError) as e:
            raise ClipperError(f"Claude API unavailable ({e.status_code}): {e.message}") from None

    # ---------- Claude ----------

    def _claude_meta(self, model: str, resp) -> dict:
        return {"model": resp.model, "request_id": getattr(resp, "_request_id", None),
                "stop_reason": resp.stop_reason,
                **self._account(model, resp.usage.input_tokens, resp.usage.output_tokens)}

    # ---------- OpenAI-compatible (OpenAI, Gemini, local servers) ----------

    @staticmethod
    def _messages(system: str, user: str | list[dict]) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": _openai_content(user)}]

    def _compat_json(self, model: str, system: str, user, schema: dict, effort: str,
                     max_tokens: int) -> tuple[dict | None, dict]:
        msgs = self._messages(system, user)
        rf: dict = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": False}}
        if model in self._no_schema:
            rf = self._json_mode(msgs, schema)
        for attempt in range(2):
            try:
                data, meta = self._compat(model, msgs, effort, max_tokens, rf)
            except NoSchema:
                self._no_schema.add(model)
                rf = self._json_mode(msgs, schema)
                data, meta = self._compat(model, msgs, effort, max_tokens, rf)
            if meta.get("refusal") is not None:
                return None, meta
            text = data["choices"][0]["message"].get("content") or ""
            try:
                out = parse_json(text)
                errs = check_schema(schema, out)
            except ValueError as e:
                out, errs = None, [f"not JSON ({e})"]
            if not errs:
                return out, meta
            if attempt == 0:  # once more, with what was wrong
                msgs += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "That answer doesn't match the JSON schema: "
                          + "; ".join(errs[:12]) + ". Answer again with the corrected JSON only."}]
        raise RuntimeError(f"{model} answered JSON that doesn't match the schema: {'; '.join(errs[:5])}")

    @staticmethod
    def _json_mode(msgs: list[dict], schema: dict) -> dict:
        msgs[0]["content"] += ("\n\nAnswer with one JSON object that matches this JSON schema, "
                               "and nothing else:\n" + json.dumps(schema))
        return {"type": "json_object"}

    def _compat(self, model: str, messages: list[dict], effort: str, max_tokens: int,
                response_format: dict | None = None) -> tuple[dict, dict]:
        """One chat/completions request with retries. Returns (response JSON, meta); meta has
        "refusal" when the model refused."""
        prov, name = split(model)
        if prov in DECIDERS:
            raise ClipperError(f"{model} is a decision model: it answers typed questions "
                               "(LLM.decide), not prompts")
        base, env = PROVIDERS[prov]
        base = (self.lc["local_base_url"] if prov == "local" else base).rstrip("/")
        if not base:
            raise ClipperError(f"{model}: set [llm] local_base_url in clipper.toml "
                             "(an OpenAI-compatible server, e.g. http://<host>:11434/v1)")
        key = os.environ.get(env, "")
        if not key and prov != "local":
            raise ClipperError(f"{model}: set the {env} environment variable")
        body: dict[str, Any] = {"model": name, "messages": messages,
                                ("max_completion_tokens" if prov == "openai" else "max_tokens"): max_tokens}
        if response_format:
            body["response_format"] = response_format
        if effort and model not in self._no_effort:
            body["reasoning_effort"] = EFFORT.get(effort, "medium")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        while True:
            r = self._post(model, f"{base}/chat/completions", body, headers)
            if r.status_code == 400:
                text = r.text.lower()
                if "reasoning_effort" in body and "reasoning" in text:
                    self._no_effort.add(model)  # not a reasoning model
                    body.pop("reasoning_effort")
                    continue
                if (response_format or {}).get("type") == "json_schema" and (
                        "response_format" in text or "json_schema" in text):
                    raise NoSchema
            if not r.ok:
                raise RuntimeError(f"{model}: HTTP {r.status_code}: {r.text[:300]}")
            break
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        meta = {"model": f"{prov}:{data.get('model') or name}", "request_id": data.get("id"),
                "stop_reason": choice.get("finish_reason"),
                **self._account(model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))}
        refusal = choice["message"].get("refusal")
        if refusal or choice.get("finish_reason") == "content_filter":
            meta["refusal"] = refusal or "content_filter"
        elif choice.get("finish_reason") == "length":
            raise RuntimeError(f"{model} hit max_tokens ({meta['request_id']})")
        return data, meta


# Every stage's model calls go through clipper/llm.py, which picks the provider from the
# model string; `Claude` stays the name the stages use.
Claude = LLM


def preflight(claude: LLM, model: str) -> None:
    """One tiny call so account problems stop the run with one clear line."""
    claude.preflight(model)


# ---------- budgets ----------

class OverBudget(Exception):
    pass


class Budget:
    """A spending cap across threads: a call goes ahead only if what is spent, what is in
    flight and its own estimate fit. `spent_before` is what the client had spent before this
    budget began (a caller's client shared with the swarm)."""

    def __init__(self, limit: float, claude, spent_before: float = 0.0):
        self.limit, self.claude, self.held, self.before = limit, claude, 0.0, spent_before
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        return self.claude.cost - self.before

    def reserve(self, est: float) -> None:
        with self._lock:
            if self.spent + self.held + est > self.limit:
                raise OverBudget(f"budget ${self.limit:.2f}: ${self.spent:.2f} spent, "
                                 f"next call ~${est:.3f}")
            self.held += est

    def release(self, est: float) -> None:
        with self._lock:
            self.held -= est


def estimate(cfg: dict, model: str, tokens_in: float, tokens_out: float) -> float:
    pin, pout = price_of(prices(cfg), model) or (0.0, 0.0)
    return (tokens_in * pin + tokens_out * pout) / 1e6
