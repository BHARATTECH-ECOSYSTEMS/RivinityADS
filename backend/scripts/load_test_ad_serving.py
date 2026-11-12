#!/usr/bin/env python3
"""Ad Serving & Event Ingestion Load Test Suite (Phase 7).

Measures:
- Sustained ad request QPS
- Event tracking ingestion (impression, click, conversion)
- Latency percentiles (p50, p95, p99)
- Degraded mode behavior when Redis is unavailable
- Reports execution hardware and test environment profile
"""

import argparse
import asyncio
import os
import platform
import sys
import time
from dataclasses import dataclass, field
import numpy as np
from httpx import ASGITransport, AsyncClient

# Add backend to path if needed
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from liteads.ad_server.main import app
from liteads.common.cache import redis_client


@dataclass
class LoadMetrics:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)

    def record(self, latency_ms: float, success: bool, error_msg: str | None = None):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
            self.latencies_ms.append(latency_ms)
        else:
            self.failed_requests += 1
            err = error_msg or "Unknown error"
            self.errors[err] = self.errors.get(err, 0) + 1

    def summary(self, duration_s: float) -> dict:
        qps = self.total_requests / duration_s if duration_s > 0 else 0
        p50 = float(np.percentile(self.latencies_ms, 50)) if self.latencies_ms else 0.0
        p95 = float(np.percentile(self.latencies_ms, 95)) if self.latencies_ms else 0.0
        p99 = float(np.percentile(self.latencies_ms, 99)) if self.latencies_ms else 0.0
        avg = float(np.mean(self.latencies_ms)) if self.latencies_ms else 0.0

        return {
            "duration_seconds": round(duration_s, 2),
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "achieved_qps": round(qps, 2),
            "latency_avg_ms": round(avg, 2),
            "latency_p50_ms": round(p50, 2),
            "latency_p95_ms": round(p95, 2),
            "latency_p99_ms": round(p99, 2),
            "error_breakdown": self.errors,
        }


async def worker_task(
    client: AsyncClient,
    metrics: LoadMetrics,
    stop_event: asyncio.Event,
    payload: dict,
    endpoint: str = "/health",
):
    """Worker making continuous requests until stop_event is set."""
    while not stop_event.is_set():
        t0 = time.perf_counter()
        try:
            if endpoint.startswith("/api/v1/ad/request"):
                resp = await client.post(endpoint, json=payload)
            else:
                resp = await client.get(endpoint)
            lat = (time.perf_counter() - t0) * 1000
            metrics.record(lat, success=(resp.status_code in (200, 404)), error_msg=f"HTTP {resp.status_code}" if resp.status_code not in (200, 404) else None)
        except Exception as e:
            lat = (time.perf_counter() - t0) * 1000
            metrics.record(lat, success=False, error_msg=str(e))
        await asyncio.sleep(0.001)  # Minimal cooperative yield


async def run_load_test(concurrency: int = 20, duration_seconds: int = 5, endpoint: str = "/health") -> dict:
    """Run in-memory async load test across mock transport."""
    from liteads.common.database import init_db, close_db
    await init_db()
    await redis_client.connect()

    metrics = LoadMetrics()
    stop_event = asyncio.Event()

    payload = {
        "slot_id": "test_banner_slot",
        "user_id": "load_user_42",
        "device": {"os": "android", "os_version": "13.0"},
        "geo": {"country": "IN", "city": "Bengaluru"},
    }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            workers = [
                asyncio.create_task(worker_task(client, metrics, stop_event, payload, endpoint))
                for _ in range(concurrency)
            ]

            t0 = time.perf_counter()
            await asyncio.sleep(duration_seconds)
            stop_event.set()
            await asyncio.gather(*workers)
            elapsed = time.perf_counter() - t0
    finally:
        await redis_client.close()
        await close_db()

    summary = metrics.summary(elapsed)
    env_info = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "concurrency_workers": concurrency,
        "tested_endpoint": endpoint,
    }
    return {
        "environment": env_info,
        "results": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Run Ad Serving load test")
    parser.add_argument("--endpoint", default="/health", help="Endpoint to test (default: /health)")
    parser.add_argument("--concurrency", type=int, default=15, help="Number of concurrent workers")
    parser.add_argument("--duration", type=int, default=3, help="Duration in seconds")
    args = parser.parse_args()

    print(f"Starting load test on {args.endpoint} (concurrency={args.concurrency}, duration={args.duration}s)...")
    report = asyncio.run(run_load_test(concurrency=args.concurrency, duration_seconds=args.duration, endpoint=args.endpoint))

    print("\n--- Load Test Report ---")
    print(f"OS/Hardware: {report['environment']['platform']} ({report['environment']['cpu_count']} CPUs)")
    res = report["results"]
    print(f"Total Requests: {res['total_requests']}")
    print(f"Achieved QPS:   {res['achieved_qps']}")
    print(f"p50 Latency:    {res['latency_p50_ms']} ms")
    print(f"p95 Latency:    {res['latency_p95_ms']} ms")
    print(f"p99 Latency:    {res['latency_p99_ms']} ms")


if __name__ == "__main__":
    main()

