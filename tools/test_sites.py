#!/usr/bin/env python3
"""BEGG provider diagnostics: homepage HTTP checks + optional provider API search tests.

Run from the repository root:
    python tools/test_sites.py
    python tools/test_sites.py --query "test" --timeout 20
    python tools/test_sites.py --http-only

The script never downloads video files. It makes one homepage request per provider
and, unless --http-only is supplied, one small search request through each provider
library. Results are written to reports/site_test_report.json and .csv.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from services.providers import PROVIDERS, search_videos
except Exception as exc:
    print(f"Cannot load BEGG provider registry: {exc}", file=sys.stderr)
    raise SystemExit(2)

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None

try:
    from curl_cffi import requests as _curl_requests
except Exception:
    _curl_requests = None

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


def extract_http_status(exc: BaseException) -> int | None:
    """Find an HTTP status code exposed by common HTTP client exceptions."""
    for obj in (exc, getattr(exc, "response", None)):
        if obj is None:
            continue
        for attr in ("status_code", "status"):
            value = getattr(obj, attr, None)
            try:
                if value is not None:
                    code = int(value)
                    if 100 <= code <= 599:
                        return code
            except (TypeError, ValueError):
                pass
    text = str(exc)
    import re
    match = re.search(r"\b(401|403|404|407|408|418|429|500|502|503|504)\b", text)
    return int(match.group(1)) if match else None


def status_label(code: int | None, error: str | None = None) -> str:
    if code == 403:
        return "FORBIDDEN_403"
    if code == 429:
        return "RATE_LIMITED_429"
    if code is not None and 200 <= code < 300:
        return "OK"
    if code is not None and 300 <= code < 400:
        return "REDIRECT"
    if code is not None and code >= 400:
        return f"HTTP_{code}"
    return "ERROR" if error else "UNKNOWN"


def check_homepage(key: str, spec: Any, timeout: float) -> dict[str, Any]:
    domain = spec.domains[0]
    url = f"https://{domain}/"
    started = time.perf_counter()
    result: dict[str, Any] = {
        "provider": key,
        "site": spec.name,
        "url": url,
        "http_status": None,
        "http_result": "NOT_TESTED",
        "final_url": None,
        "elapsed_seconds": None,
        "error": None,
    }
    if curl_requests is None:
        result["http_result"] = "MISSING_CURL_CFFI"
        result["error"] = "Install dependency: pip install curl-cffi"
        return result

    try:
        response = curl_requests.get(
            url,
            impersonate="chrome",
            timeout=timeout,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            },
        )
        code = int(response.status_code)
        result.update({
            "http_status": code,
            "http_result": status_label(code),
            "final_url": str(response.url),
        })
    except Exception as exc:
        code = extract_http_status(exc)
        result.update({
            "http_status": code,
            "http_result": status_label(code, str(exc)),
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        })
    result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return result


async def check_api(key: str, spec: Any, query: str, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {
        "provider": key,
        "site": spec.name,
        "api_result": "NOT_TESTED",
        "api_items": 0,
        "api_elapsed_seconds": None,
        "api_error": None,
        "api_http_status": None,
    }
    async with semaphore:
        try:
            # One result only: this is a connectivity smoke test, not a full search.
            items = await asyncio.wait_for(search_videos(key, query, limit=1), timeout=35)
            result["api_items"] = len(items)
            result["api_result"] = "OK" if items else "RESPONDED_NO_RESULTS"
        except asyncio.TimeoutError:
            result["api_result"] = "TIMEOUT"
            result["api_error"] = "API search exceeded 35 seconds"
        except Exception as exc:
            code = extract_http_status(exc)
            result["api_http_status"] = code
            result["api_result"] = status_label(code, str(exc))
            result["api_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
    result["api_elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return result


def print_table(rows: list[dict[str, Any]], api_enabled: bool) -> None:
    columns = ["PROVIDER", "HTTP", "API"] if api_enabled else ["PROVIDER", "HTTP"]
    widths = [max(len(columns[0]), *(len(str(r["site"])) for r in rows))]
    if api_enabled:
        widths += [max(8, *(len(str(r.get("http_result", ""))) for r in rows)),
                   max(8, *(len(str(r.get("api_result", ""))) for r in rows))]
    else:
        widths += [max(8, *(len(str(r.get("http_result", ""))) for r in rows))]
    print("\n" + " | ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        values = [row["site"], row.get("http_result", "—")]
        if api_enabled:
            values.append(row.get("api_result", "—"))
        print(" | ".join(str(v).ljust(w) for v, w in zip(values, widths)))


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test all configured BEGG provider sites and APIs.")
    parser.add_argument("--query", default="test", help="Small search phrase for API smoke tests (default: test)")
    parser.add_argument("--timeout", type=float, default=18, help="Homepage request timeout in seconds")
    parser.add_argument("--http-only", action="store_true", help="Skip provider-library API search tests")
    parser.add_argument("--workers", type=int, default=5, help="Parallel homepage checks (default: 5)")
    parser.add_argument("--output", default="reports/site_test_report", help="Output path without extension")
    args = parser.parse_args()

    specs = list(PROVIDERS.items())
    print(f"BEGG diagnostics | {len(specs)} providers | Pornhub excluded")
    print("Checking provider homepages…")

    http_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 10))) as pool:
        futures = {
            pool.submit(check_homepage, key, spec, args.timeout): key
            for key, spec in specs
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                http_results[key] = future.result()
            except Exception as exc:
                http_results[key] = {
                    "provider": key, "site": PROVIDERS[key].name,
                    "http_status": None, "http_result": "ERROR",
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                }

    api_results: dict[str, dict[str, Any]] = {}
    if not args.http_only:
        print(f"Running one API search smoke test per provider (query={args.query!r})…")
        semaphore = asyncio.Semaphore(3)
        checks = [
            check_api(key, spec, args.query, semaphore)
            for key, spec in specs
        ]
        for item in await asyncio.gather(*checks):
            api_results[item["provider"]] = item

    rows = []
    for key, spec in specs:
        row = dict(http_results.get(key, {}))
        if key in api_results:
            row.update(api_results[key])
        rows.append(row)

    print_table(rows, api_enabled=not args.http_only)
    forbidden = [
        row for row in rows
        if row.get("http_status") == 403 or row.get("api_http_status") == 403
        or row.get("http_result") == "FORBIDDEN_403"
        or row.get("api_result") == "FORBIDDEN_403"
    ]
    issues = [
        row for row in rows
        if row.get("http_result") not in ("OK", "REDIRECT")
        or (not args.http_only and row.get("api_result") not in ("OK", "RESPONDED_NO_RESULTS"))
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": None if args.http_only else args.query,
        "provider_count": len(rows),
        "pornhub_excluded": True,
        "forbidden_403_count": len(forbidden),
        "issue_count": len(issues),
        "results": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output.with_suffix(".json")
    csv_path = output.with_suffix(".csv")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_fields = sorted({field for row in rows for field in row})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n403 responses: {len(forbidden)}")
    for row in forbidden:
        print(f"  🚫 {row['site']}: HTTP={row.get('http_status')} API={row.get('api_http_status')}")

    print(f"Providers with one or more issues: {len(issues)}")
    print(f"JSON report: {json_path.resolve()}")
    print(f"CSV report:  {csv_path.resolve()}")
    return 1 if forbidden else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
