#!/usr/bin/env python3
"""Load test: how fast does LabGuard accept messages, and how fast does it finish processing them?

    docker compose up -d --build                      # use a freshly emptied database
    python -m scripts.loadtest --count 2000 --concurrency 20 --label "1 worker"
    docker compose up -d --scale worker=2             # then compare
    python -m scripts.loadtest --count 2000 --concurrency 20 --label "2 workers" --markdown

Reports ingest throughput and latency percentiles, plus end-to-end time until every message
has been processed by the worker. Run it against your own machine, not a shared host, and
quote the numbers WITH the hardware and setup (they only mean something in context).
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import sys
import time
from collections import Counter

from scripts.seed import fake_patients, generate_dataset


def percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of an ascending list."""
    if not sorted_values:
        return None
    rank = max(0, math.ceil(pct / 100 * len(sorted_values)) - 1)
    return sorted_values[rank]


async def run(args: argparse.Namespace) -> int:
    import httpx  # imported here so the helpers above are importable without it

    items = generate_dataset(args.count, 0.2, 0.0, args.seed, fake_patients(100, random.Random(args.seed)))
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)

    async with httpx.AsyncClient(base_url=args.api, timeout=60, headers=headers, limits=limits) as client:
        await client.get("/healthz")
        before = (await client.get("/api/stats")).json()["messages"]
        if before.get("pending", 0):
            print(f"WARNING: {before['pending']} messages were already pending; drain time will include them.\n")

        latencies: list[float] = []
        outcomes: Counter = Counter()
        gate = asyncio.Semaphore(args.concurrency)

        async def send(item) -> None:
            async with gate:
                t0 = time.perf_counter()
                try:
                    r = await client.post("/messages", json=item.payload, headers={"Idempotency-Key": item.key})
                    outcomes[r.status_code] += 1
                except httpx.HTTPError:
                    outcomes["error"] += 1
                latencies.append(time.perf_counter() - t0)

        start = time.perf_counter()
        await asyncio.gather(*(send(i) for i in items))
        ingest_secs = time.perf_counter() - start

        # Wait for the worker(s). A message stays 'pending' until its transaction commits,
        # so pending == 0 means everything has really been processed.
        deadline = time.perf_counter() + args.timeout
        while time.perf_counter() < deadline:
            if (await client.get("/api/stats")).json()["messages"].get("pending", 0) == 0:
                break
            await asyncio.sleep(0.25)
        else:
            print("TIMED OUT waiting for the worker to drain the queue.")
        total_secs = time.perf_counter() - start

    accepted = outcomes[201] + outcomes[200]
    lat = sorted(latencies)
    rows = [
        ("Messages sent", f"{args.count}"),
        ("Accepted (201/200)", f"{accepted}"),
        ("Errors / other", f"{args.count - accepted}"),
        ("Concurrency", f"{args.concurrency}"),
        ("Ingest throughput", f"{accepted / ingest_secs:,.0f} req/s"),
        ("Latency p50 / p95 / p99", " / ".join(f"{percentile(lat, p) * 1000:,.0f} ms" for p in (50, 95, 99))),
        ("Time until all processed", f"{total_secs:,.1f} s"),
        ("End-to-end throughput", f"{accepted / total_secs:,.0f} msg/s"),
    ]
    title = f"Load test: {args.label}" if args.label else "Load test"
    if args.markdown:
        print(f"**{title}**\n\n| Metric | Result |\n|---|---|")
        for k, v in rows:
            print(f"| {k} | {v} |")
    else:
        print(title)
        for k, v in rows:
            print(f"  {k:<26}{v}")
    if set(outcomes) - {200, 201}:
        print(f"\nOutcomes: {dict(outcomes)}")
    return 0 if accepted == args.count else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--api-key", default=os.environ.get("LABGUARD_API_KEY"))
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--seed", type=int, default=random.randint(1000, 999999), help="keys derive from this; reuse = replays")
    ap.add_argument("--timeout", type=float, default=300, help="seconds to wait for the queue to drain")
    ap.add_argument("--label", default="", help='e.g. "2 workers"')
    ap.add_argument("--markdown", action="store_true")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
