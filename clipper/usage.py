"""What the model providers themselves say this month cost: the Claude API and OpenRouter.

Usage:
    clipper costs --providers         each provider's own numbers, next to what our log recorded

The war room's API usage panel shows the same (/api/usage). `clipper costs` adds up our own
log ([paths] costs), priced from [llm] prices or OpenRouter's per-call cost; this asks the
providers, so a call the log missed (another tool on the same key, the Console's workbench, a
crash before the line was written) still shows:

    Claude      the Admin API's cost report (/v1/organizations/cost_report), month to date by
                model. Needs an Admin API key (sk-ant-admin..., Console -> Settings -> Admin keys;
                organizations only), $ANTHROPIC_ADMIN_KEY or [paths] anthropic_admin_key. The
                normal API key can't read it. Costs appear about 5 minutes after a call.
    OpenRouter  /api/v1/key for the key clipper uses ($OPENROUTER_API_KEY or [paths]
                openrouter_key): today, this week, this month, all time, and what's left of the
                key's limit; /api/v1/credits for the account balance when the key may read it.

Each provider is asked at most every 5 minutes (modalmon.cached); the last good answer is
kept, marked stale, when one fails.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
ADMIN_ENV = "ANTHROPIC_ADMIN_KEY"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
TTL = 300
TIMEOUT = 20


def admin_key(cfg: dict) -> str:
    """The Anthropic Admin API key: $ANTHROPIC_ADMIN_KEY, else the file [paths] anthropic_admin_key."""
    key = os.environ.get(ADMIN_ENV, "").strip()
    path = cfg["paths"].get("anthropic_admin_key")
    if key or not path:
        return key
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# ---------- Claude (Admin API cost report) ----------

def cost_rows(pages: list[dict], today: str) -> dict:
    """Month to date from cost_report pages (daily buckets grouped by description): total,
    today, by model and by cost type. Amounts are decimal strings in cents."""
    total = day = 0.0
    by_model: dict[str, float] = {}
    by_type: dict[str, float] = {}
    days: dict[str, float] = {}
    for page in pages:
        for bucket in page.get("data", []):
            when = str(bucket.get("starting_at", ""))[:10]
            for r in bucket.get("results", []):
                usd = float(r.get("amount") or 0) / 100
                total += usd
                days[when] = days.get(when, 0.0) + usd
                if when == today:
                    day += usd
                name = r.get("model") or r.get("description") or "other"
                by_model[name] = by_model.get(name, 0.0) + usd
                kind = r.get("cost_type") or "other"
                by_type[kind] = by_type.get(kind, 0.0) + usd
    top = lambda d: {k: round(v, 2) for k, v in sorted(d.items(), key=lambda kv: -kv[1]) if round(v, 2)}
    return {"month_usd": round(total, 2), "today_usd": round(day, 2), "by_model": top(by_model),
            "by_type": top(by_type), "days": {k: round(v, 2) for k, v in sorted(days.items())}}


def claude(cfg: dict) -> dict:
    import requests

    key = admin_key(cfg)
    if not key:
        return {"missing": f"set {ADMIN_ENV} (an Admin API key, sk-ant-admin...) to read Claude's own bill"}
    now = datetime.now(UTC)
    params: dict = {"starting_at": month_start(now).strftime("%Y-%m-%dT%H:%M:%SZ"), "bucket_width": "1d",
                    "group_by[]": "description", "limit": 31}
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "User-Agent": "clipper"}
    pages = []
    for _ in range(10):  # a month is 31 daily buckets at most; pages are a guard, not expected
        r = requests.get(f"{ANTHROPIC_BASE}/organizations/cost_report", params=params,
                         headers=headers, timeout=TIMEOUT)
        if r.status_code in (401, 403):
            raise RuntimeError(f"HTTP {r.status_code}: {ADMIN_ENV} must be an Admin API key (sk-ant-admin...)")
        r.raise_for_status()
        page = r.json()
        pages.append(page)
        if not page.get("has_more") or not page.get("next_page"):
            break
        params["page"] = page["next_page"]
    return {**cost_rows(pages, now.strftime("%Y-%m-%d")),
            "link": "https://platform.claude.com/cost"}


# ---------- OpenRouter (/key, /credits) ----------

def openrouter_rows(key_info: dict, credits: dict | None) -> dict:
    d = key_info.get("data", key_info)
    num = lambda v: round(float(v), 2) if v is not None else None
    out = {"label": d.get("label") or "", "today_usd": num(d.get("usage_daily")) or 0.0,
           "week_usd": num(d.get("usage_weekly")) or 0.0, "month_usd": num(d.get("usage_monthly")) or 0.0,
           "all_time_usd": num(d.get("usage")) or 0.0, "limit_usd": num(d.get("limit")),
           "limit_left_usd": num(d.get("limit_remaining")), "free_tier": bool(d.get("is_free_tier"))}
    c = (credits or {}).get("data") or {}
    if "total_credits" in c:
        out["credits_usd"] = num(c["total_credits"])
        out["balance_usd"] = round(float(c["total_credits"]) - float(c.get("total_usage") or 0), 2)
    return out


def openrouter(cfg: dict) -> dict:
    import requests

    from . import llm

    key = llm.provider_key("openrouter", cfg["paths"])
    if not key:
        return {"missing": f"set {llm.PROVIDERS['openrouter'][1]} or put the key in {cfg['paths'].get('openrouter_key')}"}
    headers = {"Authorization": f"Bearer {key}"}
    r = requests.get(f"{OPENROUTER_BASE}/key", headers=headers, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        raise RuntimeError(f"HTTP {r.status_code}: OpenRouter refused the key")
    r.raise_for_status()
    credits = None
    try:  # the account balance; some keys may not read it
        c = requests.get(f"{OPENROUTER_BASE}/credits", headers=headers, timeout=TIMEOUT)
        credits = c.json() if c.ok else None
    except (requests.RequestException, ValueError):
        pass
    return {**openrouter_rows(r.json(), credits), "link": "https://openrouter.ai/activity"}


# ---------- both, with our log beside them ----------

def logged(cfg: dict, month: str) -> dict:
    """This month's spend per provider as our own log ([paths] costs) recorded it."""
    from . import costs

    out: dict[str, float] = {}
    for r in costs.read(Path(cfg["paths"]["costs"])):
        if r.get("kind") == "llm" and str(r.get("ts", "")).startswith(month):
            prov = r.get("provider") or "anthropic"
            out[prov] = round(out.get(prov, 0.0) + float(r.get("usd") or 0), 4)
    return out


def status(cfg: dict) -> dict:
    from .modalmon import cached

    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    ours = logged(cfg, month)
    return {"at": now.isoformat(timespec="seconds"), "month": month,
            "claude": {**cached("usage:claude", TTL, lambda: claude(cfg)), "logged_usd": round(ours.get("anthropic", 0.0), 2)},
            "openrouter": {**cached("usage:openrouter", TTL, lambda: openrouter(cfg)),
                           "logged_usd": round(ours.get("openrouter", 0.0), 2)}}


def print_status(cfg: dict) -> None:
    s = status(cfg)
    usd = lambda v: f"${v:,.2f}"
    for name, title in (("claude", "Claude API"), ("openrouter", "OpenRouter")):
        p = s[name]
        ours = f"our log {usd(p['logged_usd'])}"
        if p.get("missing"):
            print(f"{title}: {p['missing']} ({ours})")
            continue
        if p.get("error"):
            print(f"{title}: {p['error']} ({ours})")
            continue
        line = f"{title} {s['month']}: {usd(p['month_usd'])} by its own count, {usd(p['today_usd'])} today; {ours}"
        if name == "openrouter":
            line += f"; all time {usd(p['all_time_usd'])}"
            if p.get("limit_left_usd") is not None:
                line += f", {usd(p['limit_left_usd'])} left of the key's {usd(p['limit_usd'])} limit"
            if p.get("balance_usd") is not None:
                line += f", balance {usd(p['balance_usd'])}"
        print(line + (f" (stale: {p['stale']})" if p.get("stale") else ""))
        if p.get("by_model"):
            print("  by model: " + ", ".join(f"{k} {usd(v)}" for k, v in list(p["by_model"].items())[:6]))
