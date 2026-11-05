"""The providers' own numbers for the war room's API usage panel (clipper/usage.py, #72)."""
from __future__ import annotations

import json

from clipper import usage


def test_cost_rows_adds_cents_by_day_model_and_type():
    pages = [{"data": [
        {"starting_at": "2026-11-03T00:00:00Z", "results": [
            {"amount": "123.45", "currency": "USD", "model": "claude-sonnet-5-5", "cost_type": "tokens"},
            {"amount": "5", "currency": "USD", "description": "Web Search", "cost_type": "web_search"}]},
        {"starting_at": "2026-11-04T00:00:00Z", "results": [
            {"amount": "200", "currency": "USD", "model": "claude-haiku-5-5", "cost_type": "tokens"}]},
    ], "has_more": False}]
    r = usage.cost_rows(pages, "2026-11-04")
    assert r["month_usd"] == 3.28 and r["today_usd"] == 2.0
    assert list(r["by_model"]) == ["claude-haiku-5-5", "claude-sonnet-5-5", "Web Search"]
    assert r["by_type"] == {"tokens": 3.23, "web_search": 0.05}
    assert r["days"] == {"2026-11-03": 1.28, "2026-11-04": 2.0}


def test_openrouter_rows_key_and_balance():
    key = {"data": {"label": "clipper", "usage": 12.3456, "usage_daily": 0.5, "usage_weekly": 2,
                    "usage_monthly": 4.25, "limit": 20, "limit_remaining": 7.65, "is_free_tier": False}}
    r = usage.openrouter_rows(key, {"data": {"total_credits": 25, "total_usage": 12.35}})
    assert (r["today_usd"], r["week_usd"], r["month_usd"], r["all_time_usd"]) == (0.5, 2.0, 4.25, 12.35)
    assert (r["limit_usd"], r["limit_left_usd"], r["balance_usd"]) == (20.0, 7.65, 12.65)
    unlimited = usage.openrouter_rows({"data": {"usage": 1, "limit": None, "limit_remaining": None}}, None)
    assert unlimited["limit_usd"] is None and "balance_usd" not in unlimited


def test_status_without_keys_says_what_to_set(tmp_path, monkeypatch):
    monkeypatch.delenv(usage.ADMIN_ENV, raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    usage._LAST.clear()
    log = tmp_path / "costs.jsonl"
    month = usage.datetime.now(usage.UTC).strftime("%Y-%m")
    log.write_text("\n".join(json.dumps(r) for r in [
        {"ts": f"{month}-01T00:00:00+00:00", "kind": "llm", "provider": "anthropic", "usd": 1.5},
        {"ts": f"{month}-02T00:00:00+00:00", "kind": "llm", "provider": "openrouter", "usd": 0.25},
        {"ts": "2020-01-01T00:00:00+00:00", "kind": "llm", "provider": "anthropic", "usd": 9},
    ]), encoding="utf-8")
    cfg = {"paths": {"costs": log, "anthropic_admin_key": tmp_path / "none.txt",
                     "openrouter_key": tmp_path / "none.txt"}}
    s = usage.status(cfg)
    assert usage.ADMIN_ENV in s["claude"]["missing"] and s["claude"]["logged_usd"] == 1.5
    assert "OPENROUTER_API_KEY" in s["openrouter"]["missing"] and s["openrouter"]["logged_usd"] == 0.25
    usage._LAST.clear()


def test_logged_keeps_the_ledgers_precision(tmp_path):
    log = tmp_path / "costs.jsonl"
    month = usage.datetime.now(usage.UTC).strftime("%Y-%m")
    line = json.dumps({"ts": f"{month}-01T00:00:00+00:00", "kind": "llm", "provider": "anthropic", "usd": 0.000049})
    log.write_text("\n".join([line] * 1000), encoding="utf-8")
    assert round(usage.logged({"paths": {"costs": log}}, month)["anthropic"], 2) == 0.05


def test_throttled_counts_failures_and_keeps_the_last_good(monkeypatch):
    usage._LAST.clear()
    usage._GOOD.clear()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("HTTP 429")

    assert "429" in usage.throttled("t", boom)["error"]
    assert "429" in usage.throttled("t", boom)["error"] and len(calls) == 1  # not asked again in the TTL
    clock = [usage.time.time()]
    monkeypatch.setattr(usage.time, "time", lambda: clock[0])
    clock[0] += usage.TTL + 1
    assert usage.throttled("t", lambda: {"month_usd": 1.0}) == {"month_usd": 1.0}
    clock[0] += usage.TTL + 1
    stale = usage.throttled("t", boom)
    assert stale["month_usd"] == 1.0 and "429" in stale["stale"] and len(calls) == 2
    usage._LAST.clear()
    usage._GOOD.clear()
