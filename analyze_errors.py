#!/usr/bin/env python3
"""
Trading Hydra Log Error Analyzer
Finds ALL errors, exceptions, warnings, and failure events across the full week of logs.
"""

import json
import gzip
import os
import re
from collections import defaultdict, Counter
from datetime import datetime

# ─── Configuration ──────────────────────────────────────────────────────────

LOG_DIR   = r"C:\Users\admin1\Downloads\export (1)\export\logs"
ARCH_DIR  = os.path.join(LOG_DIR, "archive")

# Level keywords that always qualify
ERROR_LEVELS = {"error", "warning", "warn", "critical", "fatal"}

# Event / message keyword patterns that qualify any record
KEYWORD_PATTERNS = [
    r"\berror\b", r"\bfailed\b", r"\bfail\b", r"\bfailure\b",
    r"\bexception\b", r"\brejected\b", r"\bblocked\b", r"\bhalted\b",
    r"\btimeout\b", r"\btraceback\b", r"\bstack trace\b",
    r"\brate.?limit\b", r"\bstate.?corrupt\b", r"\border.?reject\b",
]
KEYWORD_RE = re.compile("|".join(KEYWORD_PATTERNS), re.IGNORECASE)

# ─── Critical categories for special highlighting ──────────────────────────

CRITICAL_PATTERNS = {
    "API_RATE_LIMIT":       re.compile(r"rate.?limit|429|too many request", re.I),
    "ORDER_REJECTION":      re.compile(r"order.?reject|reject.*order|execution.?fail|trade.?fail", re.I),
    "POSITION_CHECK_FAIL":  re.compile(r"position.*(fail|error|corrupt)|position.?check", re.I),
    "STATE_CORRUPTION":     re.compile(r"state.?(corrupt|invalid|mismatch)|corrupt.?state", re.I),
    "DATA_LOSS":            re.compile(r"data.?(loss|lost|missing|corrupt)|missing.?data", re.I),
    "EXCEPTION_TRACEBACK":  re.compile(r"traceback|exception|Error:", re.I),
    "CONNECTION_FAILURE":   re.compile(r"connect.?(fail|error|timeout|refused)|disconnect", re.I),
    "AUTH_FAILURE":         re.compile(r"auth.?(fail|error|invalid)|401|403|unauthorized|forbidden", re.I),
    "TIMEOUT":              re.compile(r"timeout|timed out", re.I),
    "HALT_OR_BLOCK":        re.compile(r"\bhalted\b|\bblocked\b|\bblacklisted\b", re.I),
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def normalize_event_key(record: dict) -> str:
    """Create a deduplication key from a record."""
    event  = record.get("event", "")
    reason = record.get("reason", "")
    level  = record.get("level", "")
    msg    = record.get("message", record.get("msg", record.get("error", "")))

    # Strip variable parts from messages to group similar errors
    msg_clean = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*", "<TS>", str(msg))
    msg_clean = re.sub(r"\b[\w.-]+@[\w.-]+\b", "<EMAIL>", msg_clean)
    msg_clean = re.sub(r"\b\d+\.\d+\.\d+\.\d+\b", "<IP>", msg_clean)
    msg_clean = re.sub(r"'[A-Z]{1,5}'", "<TICKER>", msg_clean)
    msg_clean = re.sub(r"\b[0-9]+\b", "<N>", msg_clean)
    msg_clean = re.sub(r"\s+", " ", msg_clean).strip()

    parts = [p for p in [level, event, reason, msg_clean[:120]] if p]
    return " | ".join(parts) if parts else "<unknown>"


def record_to_str(record: dict) -> str:
    """Single-line representation of a record."""
    ts    = record.get("ts", "?")
    event = record.get("event", "?")
    level = record.get("level", "")
    msg   = record.get("message", record.get("msg", record.get("error", "")))
    extra = {k: v for k, v in record.items()
             if k not in {"ts", "event", "level", "message", "msg", "error"}}
    extra_str = json.dumps(extra, default=str)[:200] if extra else ""
    parts = [p for p in [ts, level.upper() if level else None, event, str(msg)[:160], extra_str] if p]
    return "  ".join(parts)


def classify_critical(record: dict) -> list[str]:
    """Return list of critical category labels that apply to this record."""
    text = json.dumps(record, default=str)
    return [label for label, pat in CRITICAL_PATTERNS.items() if pat.search(text)]


def is_error_record(record: dict) -> bool:
    """Return True if this record should be captured."""
    level = str(record.get("level", "")).lower()
    if level in ERROR_LEVELS:
        return True
    text = json.dumps(record, default=str)
    return bool(KEYWORD_RE.search(text))


def open_log(path: str):
    """Yield raw lines from a plain or gzip log file."""
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            yield from fh
    else:
        with open(path, encoding="utf-8", errors="replace") as fh:
            yield from fh


def collect_log_files() -> list[str]:
    """Collect all log file paths to process."""
    files = []
    # Plain rotated logs: app.jsonl, app.jsonl.1 … app.jsonl.7
    for name in ["app.jsonl"] + [f"app.jsonl.{i}" for i in range(1, 8)]:
        p = os.path.join(LOG_DIR, name)
        if os.path.exists(p):
            files.append(p)
    # Additional plain logs in log dir
    for name in os.listdir(LOG_DIR):
        full = os.path.join(LOG_DIR, name)
        if os.path.isfile(full) and name.endswith(".jsonl") and full not in files:
            files.append(full)
    # Archived gzip logs
    if os.path.isdir(ARCH_DIR):
        for name in sorted(os.listdir(ARCH_DIR)):
            if name.endswith(".jsonl.gz"):
                files.append(os.path.join(ARCH_DIR, name))
    return files


# ─── Main analysis ────────────────────────────────────────────────────────────

def main():
    files = collect_log_files()
    print(f"Found {len(files)} log file(s) to process:")
    for f in files:
        size_mb = os.path.getsize(f) / 1024 / 1024
        print(f"  {os.path.basename(f):55s}  {size_mb:6.2f} MB")
    print()

    # Data structures
    error_groups: dict[str, dict] = {}   # key -> {count, first, last, level, examples, critical_cats}
    total_records   = 0
    total_errors    = 0
    parse_errors    = 0
    file_stats      = {}

    critical_records: list[dict] = []    # records matching a critical category

    for filepath in files:
        fname = os.path.basename(filepath)
        file_record_count = 0
        file_error_count  = 0

        try:
            for raw in open_log(filepath):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue

                total_records    += 1
                file_record_count += 1

                if not is_error_record(record):
                    continue

                total_errors    += 1
                file_error_count += 1

                key = normalize_event_key(record)
                ts  = record.get("ts", "")

                if key not in error_groups:
                    error_groups[key] = {
                        "count":         0,
                        "first":         ts,
                        "last":          ts,
                        "first_record":  record,
                        "last_record":   record,
                        "level":         str(record.get("level", "")).upper(),
                        "event":         record.get("event", ""),
                        "examples":      [],
                        "critical_cats": set(),
                        "files":         set(),
                    }

                g = error_groups[key]
                g["count"] += 1
                if ts and (not g["first"] or ts < g["first"]):
                    g["first"]        = ts
                    g["first_record"] = record
                if ts and (not g["last"] or ts > g["last"]):
                    g["last"]        = ts
                    g["last_record"] = record
                if len(g["examples"]) < 3:
                    g["examples"].append(record)
                g["files"].add(fname)

                cats = classify_critical(record)
                if cats:
                    g["critical_cats"].update(cats)
                    if len(critical_records) < 2000:
                        critical_records.append(record)

        except Exception as e:
            print(f"  [ERROR] Could not process {fname}: {e}")
            file_stats[fname] = {"records": 0, "errors": 0, "file_error": str(e)}
            continue

        file_stats[fname] = {"records": file_record_count, "errors": file_error_count}

    # ── Sort groups by frequency ──
    sorted_groups = sorted(error_groups.items(), key=lambda x: x[1]["count"], reverse=True)

    # ── Separate critical groups ──
    critical_groups = [(k, g) for k, g in sorted_groups if g["critical_cats"]]
    non_critical    = [(k, g) for k, g in sorted_groups if not g["critical_cats"]]

    # ─────────────────────────────────────────────────────────────────────────
    # OUTPUT
    # ─────────────────────────────────────────────────────────────────────────

    # Force UTF-8 output so arrow/unicode chars don't crash on Windows cp1252 terminals
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    SEP  = "=" * 100
    SEP2 = "-" * 100

    print(SEP)
    print("TRADING HYDRA LOG ERROR ANALYSIS REPORT")
    print(SEP)
    print(f"  Total log records processed : {total_records:,}")
    print(f"  Total error/warning records : {total_errors:,}")
    print(f"  Unique error types grouped  : {len(error_groups):,}")
    print(f"  Critical error types        : {len(critical_groups):,}")
    print(f"  JSON parse errors           : {parse_errors:,}")
    print()

    # ── Per-file stats ──
    print(SEP)
    print("PER-FILE STATISTICS")
    print(SEP)
    print(f"  {'File':<55}  {'Records':>10}  {'Errors':>8}  {'Error%':>7}")
    print(f"  {'-'*55}  {'-'*10}  {'-'*8}  {'-'*7}")
    for fpath in files:
        fname = os.path.basename(fpath)
        st = file_stats.get(fname, {})
        rec = st.get("records", 0)
        err = st.get("errors", 0)
        pct = (err / rec * 100) if rec else 0
        fe  = f"  FILE-ERROR: {st.get('file_error','')}" if "file_error" in st else ""
        print(f"  {fname:<55}  {rec:>10,}  {err:>8,}  {pct:>6.1f}%{fe}")
    print()

    # ── TOP 10 MOST FREQUENT ERRORS ──
    print(SEP)
    print("TOP 10 MOST FREQUENT ERROR TYPES")
    print(SEP)
    for rank, (key, g) in enumerate(sorted_groups[:10], 1):
        cats = ", ".join(sorted(g["critical_cats"])) if g["critical_cats"] else "—"
        print(f"\n  #{rank}  Count: {g['count']:,}  |  Level: {g['level'] or 'N/A'}  |  Event: {g['event']}")
        print(f"       Critical categories : {cats}")
        print(f"       First occurrence    : {g['first']}")
        print(f"       Last  occurrence    : {g['last']}")
        print(f"       Files seen in       : {', '.join(sorted(g['files']))}")
        print(f"       Key                 : {key[:140]}")
        # Show one example record (compact)
        ex = g["first_record"]
        ex_clean = {k: v for k, v in ex.items() if k != "ts"}
        print(f"       Example             : {json.dumps(ex_clean, default=str)[:280]}")
    print()

    # ── ALL CRITICAL ERRORS ──
    print(SEP)
    print("CRITICAL ERRORS (could cause missing trades or data loss)")
    print(SEP)

    if not critical_groups:
        print("  No critical errors detected.\n")
    else:
        # Group by critical category for easier reading
        by_cat: dict[str, list] = defaultdict(list)
        for key, g in critical_groups:
            for cat in g["critical_cats"]:
                by_cat[cat].append((key, g))

        for cat in sorted(by_cat.keys()):
            entries = sorted(by_cat[cat], key=lambda x: x[1]["count"], reverse=True)
            total_in_cat = sum(g["count"] for _, g in entries)
            print(f"\n  [{cat}]  —  {len(entries)} unique type(s),  {total_in_cat:,} total occurrences")
            print(f"  {SEP2}")
            for key, g in entries[:20]:  # cap at 20 per category
                print(f"    Count: {g['count']:>6,}  |  {g['first']}  →  {g['last']}")
                print(f"    Key  : {key[:160]}")
                ex = g["first_record"]
                ex_clean = {k: v for k, v in ex.items() if k != "ts"}
                print(f"    Ex   : {json.dumps(ex_clean, default=str)[:320]}")
                print()

    # ── ALL ERROR GROUPS (full list) ──
    print(SEP)
    print(f"ALL {len(error_groups)} ERROR/WARNING TYPES  (sorted by frequency)")
    print(SEP)
    for rank, (key, g) in enumerate(sorted_groups, 1):
        cats = f"  *** {', '.join(sorted(g['critical_cats']))}" if g["critical_cats"] else ""
        print(f"  {rank:>4}.  [{g['count']:>6,}x]  {g['first'][:19]}→{g['last'][:19]}  "
              f"lvl={g['level'] or 'N/A':<8}  event={g['event']:<45}{cats}")
        print(f"        key : {key[:150]}")
    print()

    # ── SPECIAL CHECKS ──
    print(SEP)
    print("SPECIAL CHECKS SUMMARY")
    print(SEP)

    checks = {
        "API Rate Limit Errors":    "API_RATE_LIMIT",
        "Order Rejections":         "ORDER_REJECTION",
        "Position Check Failures":  "POSITION_CHECK_FAIL",
        "State Corruption":         "STATE_CORRUPTION",
        "Data Loss Events":         "DATA_LOSS",
        "Connection Failures":      "CONNECTION_FAILURE",
        "Auth / 401 / 403 Errors":  "AUTH_FAILURE",
        "Timeouts":                 "TIMEOUT",
        "Halt / Block Events":      "HALT_OR_BLOCK",
        "Exceptions / Tracebacks":  "EXCEPTION_TRACEBACK",
    }

    for label, cat in checks.items():
        matching = [(k, g) for k, g in sorted_groups if cat in g["critical_cats"]]
        total_count = sum(g["count"] for _, g in matching)
        if matching:
            print(f"\n  {label}")
            print(f"    Unique types : {len(matching)}")
            print(f"    Total events : {total_count:,}")
            # List up to 5 most frequent
            for key, g in matching[:5]:
                print(f"      [{g['count']:>5,}x]  {g['first'][:19]} → {g['last'][:19]}  |  {key[:120]}")
                ex = g["first_record"]
                ex_clean = {k: v for k, v in ex.items() if k not in {"ts"}}
                print(f"               Ex: {json.dumps(ex_clean, default=str)[:280]}")
        else:
            print(f"\n  {label}")
            print(f"    ** NONE DETECTED **")

    # ── Event-level counts for warning / error / critical ──
    print()
    print(SEP)
    print("ERROR LEVEL BREAKDOWN")
    print(SEP)
    level_counter: Counter = Counter()
    event_counter: Counter = Counter()
    for key, g in sorted_groups:
        level_counter[g["level"] or "UNLABELED"] += g["count"]
        event_counter[g["event"] or "<no-event>"] += g["count"]

    print("\n  By Level:")
    for lvl, cnt in level_counter.most_common():
        print(f"    {lvl:<15} {cnt:>8,}")

    print("\n  Top 30 Error Events by frequency:")
    for evt, cnt in event_counter.most_common(30):
        print(f"    {evt:<55} {cnt:>8,}")

    print()
    print(SEP)
    print("ANALYSIS COMPLETE")
    print(SEP)


if __name__ == "__main__":
    main()
