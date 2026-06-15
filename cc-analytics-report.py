#!/usr/bin/env python3
"""
Claude Code Analytics Report
Pulls per-user metrics from the Anthropic Admin API and prints a summary.

Requirements:
  - Python 3.8+ (stdlib only, no pip installs needed)
  - Admin API key: Console → Settings → Admin Keys (Admin role required)

Usage:
  ADMIN_API_KEY=sk-ant-admin... python cc-analytics-report.py
  ADMIN_API_KEY=sk-ant-admin... python cc-analytics-report.py 2026-06-10
  ADMIN_API_KEY=sk-ant-admin... python cc-analytics-report.py 2026-06-01 2026-06-10

Arguments:
  [date]            Single day to report (default: yesterday). Format: YYYY-MM-DD
  [start] [end]     Date range — runs one request per day in range, aggregates results

Output:
  Per-user table: sessions, LOC added, commits, PRs, tool accept %, cost, cache hit %, models used
  Org totals + cache hit rate + top spenders

Metrics explained:
  Accept %    = edit_tool accepted / (accepted + rejected)  — prompt quality signal
  Cache Hit % = cache_read_tokens / all_input_tokens        — prompt efficiency signal
  Cost        = estimated_cost from Anthropic (cents → USD) — sum across all models used
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import date, timedelta
from collections import defaultdict


# ── Config ────────────────────────────────────────────────────────────────────

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
if not ADMIN_KEY:
    print("ERROR: set ADMIN_API_KEY=sk-ant-admin... in your environment")
    sys.exit(1)

BASE_URL = "https://api.anthropic.com/v1/organizations"
HEADERS = {
    "anthropic-version": "2023-06-01",
    "x-api-key": ADMIN_KEY,
    "User-Agent": "cc-analytics-report/1.0",
}


# ── API helpers ───────────────────────────────────────────────────────────────

def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code} from {url}\n{body}")
        sys.exit(1)


def fetch_cc_analytics(target_date: str) -> list:
    """Fetch all users for a single day (cursor-paginated)."""
    rows = []
    page = None
    while True:
        params = {"starting_at": target_date, "limit": 1000}
        if page:
            params["page"] = page
        url = f"{BASE_URL}/usage_report/claude_code?{urllib.parse.urlencode(params)}"
        data = get_json(url)
        rows.extend(data.get("data", []))
        if not data.get("has_more"):
            break
        page = data.get("next_page")
    return rows


# ── Metric helpers ────────────────────────────────────────────────────────────

def actor_label(row: dict) -> str:
    actor = row.get("actor", {})
    return actor.get("email_address") or actor.get("api_key_name") or "unknown"


def tool_accept_pct(row: dict) -> float:
    ta = row.get("tool_actions", {})
    accepted = sum(v.get("accepted", 0) for v in ta.values())
    rejected = sum(v.get("rejected", 0) for v in ta.values())
    total = accepted + rejected
    return 100.0 * accepted / total if total else None  # None = no tool calls


def cost_usd(row: dict) -> float:
    return sum(m["estimated_cost"]["amount"] for m in row.get("model_breakdown", [])) / 100.0


def cache_hit_pct(row: dict) -> float:
    inp = sum(m["tokens"].get("input", 0) for m in row.get("model_breakdown", []))
    read = sum(m["tokens"].get("cache_read", 0) for m in row.get("model_breakdown", []))
    create = sum(m["tokens"].get("cache_creation", 0) for m in row.get("model_breakdown", []))
    total = inp + read + create
    return 100.0 * read / total if total else 0.0


def models_used(row: dict) -> str:
    names = sorted(set(
        m["model"].replace("claude-", "")
        for m in row.get("model_breakdown", [])
    ))
    return ", ".join(names) or "—"


def total_tokens(row: dict) -> int:
    inp = sum(m["tokens"].get("input", 0) for m in row.get("model_breakdown", []))
    out = sum(m["tokens"].get("output", 0) for m in row.get("model_breakdown", []))
    return inp + out


# ── Aggregation (multi-day) ───────────────────────────────────────────────────

def merge_rows(all_rows: list) -> dict:
    """Merge multiple days of per-user rows into one dict keyed by user."""
    users = defaultdict(lambda: {
        "sessions": 0, "loc_added": 0, "loc_removed": 0,
        "commits": 0, "prs": 0,
        "tool_accepted": 0, "tool_rejected": 0,
        "cost_usd": 0.0,
        "cache_read": 0, "cache_create": 0, "tokens_input": 0,
        "models": set(),
        "terminal_types": set(),
    })
    for row in all_rows:
        key = actor_label(row)
        u = users[key]
        cm = row.get("core_metrics", {})
        u["sessions"] += cm.get("num_sessions", 0)
        u["loc_added"] += cm.get("lines_of_code", {}).get("added", 0)
        u["loc_removed"] += cm.get("lines_of_code", {}).get("removed", 0)
        u["commits"] += cm.get("commits_by_claude_code", 0)
        u["prs"] += cm.get("pull_requests_by_claude_code", 0)

        for v in row.get("tool_actions", {}).values():
            u["tool_accepted"] += v.get("accepted", 0)
            u["tool_rejected"] += v.get("rejected", 0)

        for m in row.get("model_breakdown", []):
            u["cost_usd"] += m["estimated_cost"]["amount"] / 100.0
            u["cache_read"] += m["tokens"].get("cache_read", 0)
            u["cache_create"] += m["tokens"].get("cache_creation", 0)
            u["tokens_input"] += m["tokens"].get("input", 0)
            u["models"].add(m["model"].replace("claude-", ""))

        if row.get("terminal_type"):
            u["terminal_types"].add(row["terminal_type"])

    return users


# ── Printing ──────────────────────────────────────────────────────────────────

COL = {
    "user":    32,
    "sess":     7,
    "loc":      7,
    "commits":  7,
    "prs":      4,
    "accept":   8,
    "cost":     8,
    "cache":    9,
}

HEADER = (
    f"{'User':<{COL['user']}} {'Sess':>{COL['sess']}} {'LOC+':>{COL['loc']}} "
    f"{'Commits':>{COL['commits']}} {'PRs':>{COL['prs']}} {'Accept%':>{COL['accept']}} "
    f"{'Cost$':>{COL['cost']}} {'Cache%':>{COL['cache']}}  Models"
)
SEP = "-" * len(HEADER)


def fmt_pct(val) -> str:
    return f"{val:>7.0f}%" if val is not None else "      —%"


def print_user_row(user: str, u: dict):
    total_ta = u["tool_accepted"] + u["tool_rejected"]
    accept = 100.0 * u["tool_accepted"] / total_ta if total_ta else None
    total_in = u["tokens_input"] + u["cache_read"] + u["cache_create"]
    cache = 100.0 * u["cache_read"] / total_in if total_in else 0.0
    mods = ", ".join(sorted(u["models"])) or "—"
    print(
        f"{user[:COL['user']-1]:<{COL['user']}} "
        f"{u['sessions']:>{COL['sess']}} "
        f"{u['loc_added']:>{COL['loc']}} "
        f"{u['commits']:>{COL['commits']}} "
        f"{u['prs']:>{COL['prs']}} "
        f"{fmt_pct(accept):>{COL['accept']}} "
        f"${u['cost_usd']:>{COL['cost']-1}.2f} "
        f"{cache:>{COL['cache']-1}.0f}%  {mods}"
    )


def print_report(users: dict, date_label: str):
    # Sort by cost descending
    sorted_users = sorted(users.items(), key=lambda x: x[1]["cost_usd"], reverse=True)

    print(f"\n{'='*72}")
    print(f"  Claude Code Analytics — {date_label}  ({len(users)} users)")
    print(f"{'='*72}")
    print(HEADER)
    print(SEP)

    totals = defaultdict(float)
    for user, u in sorted_users:
        print_user_row(user, u)
        totals["sessions"] += u["sessions"]
        totals["loc_added"] += u["loc_added"]
        totals["commits"] += u["commits"]
        totals["prs"] += u["prs"]
        totals["tool_accepted"] += u["tool_accepted"]
        totals["tool_rejected"] += u["tool_rejected"]
        totals["cost_usd"] += u["cost_usd"]
        totals["cache_read"] += u["cache_read"]
        totals["tokens_input"] += u["tokens_input"]
        totals["cache_create"] += u["cache_create"]

    print(SEP)
    total_ta = totals["tool_accepted"] + totals["tool_rejected"]
    org_accept = 100.0 * totals["tool_accepted"] / total_ta if total_ta else None
    total_in = totals["tokens_input"] + totals["cache_read"] + totals["cache_create"]
    org_cache = 100.0 * totals["cache_read"] / total_in if total_in else 0.0

    print(
        f"{'TOTAL':<{COL['user']}} "
        f"{totals['sessions']:>{COL['sess']}.0f} "
        f"{totals['loc_added']:>{COL['loc']}.0f} "
        f"{totals['commits']:>{COL['commits']}.0f} "
        f"{totals['prs']:>{COL['prs']}.0f} "
        f"{fmt_pct(org_accept):>{COL['accept']}} "
        f"${totals['cost_usd']:>{COL['cost']-1}.2f} "
        f"{org_cache:>{COL['cache']-1}.0f}%"
    )

    print(f"\n  Org cache hit rate : {org_cache:.1f}%  {'✓ good' if org_cache >= 60 else '⚠ low — check caching'}")
    print(f"  Org tool accept %  : {org_accept:.1f}%  {'✓ good' if org_accept and org_accept >= 80 else '⚠ low — review prompt quality'}" if org_accept else "  Org tool accept % : — (no tool calls)")
    print(f"  Total spend        : ${totals['cost_usd']:.2f}")

    # Flag low cache users
    low_cache = [(u, d) for u, d in sorted_users
                 if (d["tokens_input"] + d["cache_read"] + d["cache_create"]) > 0
                 and 100.0 * d["cache_read"] / (d["tokens_input"] + d["cache_read"] + d["cache_create"]) < 30
                 and d["sessions"] >= 2]
    if low_cache:
        print(f"\n  ⚠ Low cache users (< 30%, ≥ 2 sessions) — likely starting fresh sessions each time:")
        for u, _ in low_cache[:5]:
            print(f"    {u}")

    print()


# ── Date parsing ──────────────────────────────────────────────────────────────

def date_range(start: str, end: str) -> list:
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(str(d))
        d += timedelta(days=1)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    yesterday = str(date.today() - timedelta(days=1))

    if len(sys.argv) == 1:
        dates = [yesterday]
        label = yesterday
    elif len(sys.argv) == 2:
        dates = [sys.argv[1]]
        label = sys.argv[1]
    elif len(sys.argv) == 3:
        dates = date_range(sys.argv[1], sys.argv[2])
        label = f"{sys.argv[1]} → {sys.argv[2]}"
    else:
        print(f"Usage: {sys.argv[0]} [YYYY-MM-DD] | [start] [end]")
        sys.exit(1)

    print(f"Fetching {len(dates)} day(s)…", end="", flush=True)
    all_rows = []
    for d in dates:
        rows = fetch_cc_analytics(d)
        all_rows.extend(rows)
        print(".", end="", flush=True)
    print()

    users = merge_rows(all_rows)
    print_report(users, label)


if __name__ == "__main__":
    main()
