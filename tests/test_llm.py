"""The provider routing in clipper/llm.py (#72), against a fake chat/completions server."""
from __future__ import annotations

import copy

import pytest

from clipper import costs, doctor, llm


class FakeResponse:
    def __init__(self, data: dict, status: int = 200):
        self.status_code, self.ok, self._data, self.headers = status, status < 400, data, {}
        self.text = str(data)

    def json(self) -> dict:
        return self._data


def answer(content: str, usage: dict | None = None) -> dict:
    return {"id": "r1", "model": "openai/gpt-5-mini",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 100}}


@pytest.fixture
def posted(monkeypatch):
    """Every request the client sends, answered from `replies` in order."""
    import requests

    sent, replies = [], []
    monkeypatch.setattr(costs, "llm_call", lambda *a, **k: None)

    def post(url, json, headers, timeout):
        sent.append({"url": url, "body": copy.deepcopy(json), "headers": dict(headers)})
        return replies.pop(0)
    monkeypatch.setattr(requests, "post", post)
    return sent, replies


def test_split_knows_openrouter_and_keeps_its_vendor_path():
    assert llm.split("openrouter:openai/gpt-5-mini") == ("openrouter", "openai/gpt-5-mini")
    assert llm.split("claude-haiku-5-5") == ("anthropic", "claude-haiku-5-5")
    assert llm.split("anthropic:claude-haiku-5-5") == ("anthropic", "claude-haiku-5-5")


def test_openrouter_sends_reasoning_and_charges_its_reported_cost(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    replies.append(FakeResponse(answer("hi", {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.0123})))
    client = llm.LLM(defaults)
    text, meta = client.text_call("openrouter:openai/gpt-5-mini", "sys", "hello", "xhigh")
    assert text == "hi" and meta["model"] == "openrouter:openai/gpt-5-mini"
    req = sent[0]
    assert req["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert req["body"]["model"] == "openai/gpt-5-mini"
    assert req["body"]["reasoning"] == {"effort": "high"} and "reasoning_effort" not in req["body"]
    assert req["body"]["usage"] == {"include": True}
    assert req["headers"]["Authorization"] == "Bearer k"
    assert client.cost == pytest.approx(0.0123)  # not in [llm] prices, so the table would say $0


def test_a_model_without_reasoning_is_asked_again_without_it(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    replies += [FakeResponse({"error": "reasoning is not supported for this model"}, 400),
                FakeResponse(answer("ok"))]
    text, _ = llm.LLM(defaults).text_call("openrouter:meta/llama-4", "sys", "hi", "medium")
    assert text == "ok"
    assert "reasoning" in sent[0]["body"] and "reasoning" not in sent[1]["body"]


def test_openai_keeps_reasoning_effort_and_the_price_table(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    defaults["llm"]["prices"]["openai:gpt-5-mini"] = [1.0, 10.0]
    replies.append(FakeResponse(answer('{"a": 1}')))
    client = llm.LLM(defaults)
    out, _ = client.json_call("openai:gpt-5-mini", "sys", "hi",
                              {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}, "low")
    assert out == {"a": 1}
    assert sent[0]["body"]["reasoning_effort"] == "low" and "usage" not in sent[0]["body"]
    assert client.cost == pytest.approx((1000 * 1.0 + 100 * 10.0) / 1e6)


def test_claude_model_finds_a_claude_stage_or_none(defaults):
    assert llm.claude_model(defaults).startswith("claude-")
    for sec in defaults.values():
        if isinstance(sec, dict):
            for k, v in list(sec.items()):
                if llm.is_llm(k, v):
                    sec[k] = "openrouter:openai/gpt-5-mini"
    assert llm.claude_model(defaults) == ""


def test_doctor_names_the_openrouter_key(defaults, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    defaults["paths"]["openrouter_key"] = tmp_path / "none.txt"
    defaults["scoring"]["model_a"] = "openrouter:openai/gpt-5-mini"
    (status, _, what, _), = doctor.collect(doctor.check_llm_keys, defaults)
    assert status == "FAIL" and "OPENROUTER_API_KEY" in what and "scoring.model_a" in what


def test_an_openrouter_model_is_priced_from_its_list(defaults, monkeypatch):
    monkeypatch.setattr(llm, "_OPENROUTER_PRICES", {"openai/gpt-6-luna": (0.1, 0.5)})
    assert llm.known_price(defaults, "openrouter:openai/gpt-6-luna") == (0.1, 0.5)
    assert llm.estimate(defaults, "openrouter:openai/gpt-6-luna", 1e6, 1e6) == pytest.approx(0.6)
    assert llm.known_price(defaults, "openrouter:nobody/none") is None
    assert llm.known_price(defaults, "openai:gpt-6-luna") is None  # only OpenRouter's own models


def test_doctor_wants_no_price_for_openrouter(defaults):
    defaults["scoring"]["model_a"] = "openrouter:openai/gpt-6-luna"
    (status, _, what, _), = doctor.collect(doctor.check_llm_prices, defaults)
    assert status == "OK", what


def test_a_provider_key_from_env_or_the_secrets_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    paths = {"openrouter_key": tmp_path / "openrouter_secret.txt"}
    assert llm.provider_key("openrouter", paths) == ""
    paths["openrouter_key"].write_text("sk-file\n", encoding="utf-8")
    assert llm.provider_key("openrouter", paths) == "sk-file"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert llm.provider_key("openrouter", paths) == "sk-env"


def test_the_client_sends_the_file_key(posted, monkeypatch, defaults, tmp_path):
    sent, replies = posted
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    defaults["paths"]["openrouter_key"] = tmp_path / "k.txt"
    defaults["paths"]["openrouter_key"].write_text("sk-file", encoding="utf-8")
    replies.append(FakeResponse(answer("hi")))
    llm.LLM(defaults).text_call("openrouter:openai/gpt-6-luna", "sys", "hello", "low")
    assert sent[0]["headers"]["Authorization"] == "Bearer sk-file"


def test_preflight_takes_a_reasoning_model_running_out_of_tokens(posted, monkeypatch, defaults):
    sent, replies = posted
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    out = answer("")
    out["choices"][0]["finish_reason"] = "length"
    replies.append(FakeResponse(out))
    llm.LLM(defaults).preflight("openrouter:deepseek/deepseek-v4.1-flash")  # no raise
    assert "reasoning" not in sent[0]["body"]


def test_prefixed_claude_models_count_as_claude(defaults):
    for sec in defaults.values():
        if isinstance(sec, dict):
            for k, v in list(sec.items()):
                if llm.is_llm(k, v):
                    sec[k] = "openrouter:openai/gpt-5-mini"
    defaults["segments"]["model"] = "anthropic:claude-opus-5-5"
    assert llm.claude_model(defaults) == "claude-opus-5-5"


def test_doctor_checks_the_openrouter_key_itself(defaults, monkeypatch):
    import requests

    monkeypatch.setenv("OPENROUTER_API_KEY", "bad")
    defaults["scoring"]["model_a"] = "openrouter:openai/gpt-5-mini"
    got = []

    def get(url, timeout, headers):
        got.append(url)
        return FakeResponse({"data": [{"id": "openai/gpt-5-mini"}]} if url.endswith("/models") else {}, 
                            200 if url.endswith("/models") else 401)
    monkeypatch.setattr(requests, "get", get)
    results = doctor.collect(doctor.check_llm_online, defaults)
    assert any(r[0] == "FAIL" and "refused the key" in r[2] for r in results)
    assert got[-1].endswith("/key")
