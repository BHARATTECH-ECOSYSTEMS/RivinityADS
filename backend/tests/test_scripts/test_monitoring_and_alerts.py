"""
Monitoring, Exporters, Worker Heartbeats & Alertmanager Configuration Tests (Prompt 5: P1-02).

Verifies:
1. prometheus.yml, alert_rules.yml, alertmanager.yml, and blackbox.yml parse cleanly as valid YAML
   and conform to expected schemas (non-empty alertmanagers target, valid route/receivers, valid rules).
2. Every scrape target host in prometheus.yml maps to a real service in backend/docker-compose.prod.yml
   (or localhost for Prometheus self-scrape) and `redis:6379` is NOT scraped directly over HTTP.
3. Every metric referenced in alert_rules.yml exists either in the live RivinityADS Prometheus registry
   or in configured exporter/standard metrics (`up`, `pg_up`, `redis_up`, `probe_success`).
4. Every alert rule has balanced PromQL parentheses/brackets/braces, valid duration, severity, and summary/runbook.
5. Worker heartbeat metrics emit with distinct `worker_type` labels and sync cleanly from Redis.
6. Conversion and ledger discrepancy helpers emit real Prometheus metrics.
7. Zero real webhook URLs, tokens, or plaintext passwords are committed in monitoring YAML configs.
"""

from pathlib import Path
import re
import time
from unittest.mock import AsyncMock, patch
import pytest
import yaml
from prometheus_client import REGISTRY

from liteads.ad_server.middleware.metrics import (
    AD_CONVERSIONS_TOTAL,
    LEDGER_UNBALANCED_TRANSACTIONS_TOTAL,
    WORKER_HEARTBEAT_TYPES,
    WORKER_LAST_HEARTBEAT_TIMESTAMP,
    metrics_endpoint,
    publish_worker_heartbeat,
    record_conversion,
    record_ledger_discrepancy,
    record_worker_heartbeat,
    sync_worker_heartbeats_from_redis,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PROD_COMPOSE_PATH = REPO_ROOT / "backend" / "docker-compose.prod.yml"
PROMETHEUS_CFG_PATH = REPO_ROOT / "backend" / "deployment" / "prometheus" / "prometheus.yml"
ALERT_RULES_PATH = REPO_ROOT / "backend" / "deployment" / "prometheus" / "alert_rules.yml"
BLACKBOX_CFG_PATH = REPO_ROOT / "backend" / "deployment" / "prometheus" / "blackbox.yml"
ALERTMANAGER_CFG_PATH = REPO_ROOT / "backend" / "deployment" / "alertmanager" / "alertmanager.yml"

EXPORTER_AND_BUILTIN_METRICS = {"up", "pg_up", "redis_up", "probe_success", "time"}
PROMQL_KEYWORDS_AND_LABELS = {
    "sum", "rate", "increase", "delta", "count", "histogram_quantile",
    "or", "and", "unless", "by", "without", "le", "status", "endpoint",
    "instance", "job", "error_category", "operation", "queue", "method",
    "stage", "cache_type", "query_type", "worker_type",
}


def _check_balanced_delimiters(expr: str) -> bool:
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    in_quotes = False
    quote_char = ""
    escaped = False
    for ch in expr:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if in_quotes:
            if ch == quote_char:
                in_quotes = False
            continue
        if ch in ('"', "'"):
            in_quotes = True
            quote_char = ch
            continue
        if ch in ("(", "[", "{"):
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not in_quotes and len(stack) == 0


def _registered_prometheus_names() -> set[str]:
    names: set[str] = set()
    for collector in REGISTRY.collect():
        names.add(collector.name)
        for sample in collector.samples:
            names.add(sample.name)
    return names


def test_monitoring_yaml_files_valid_and_linked():
    """Verify prometheus.yml, alert_rules.yml, alertmanager.yml, and blackbox.yml parse and link properly."""
    for p in (PROMETHEUS_CFG_PATH, ALERT_RULES_PATH, BLACKBOX_CFG_PATH, ALERTMANAGER_CFG_PATH):
        assert p.exists(), f"Missing monitoring config: {p}"

    prom_cfg = yaml.safe_load(PROMETHEUS_CFG_PATH.read_text(encoding="utf-8"))
    rules_cfg = yaml.safe_load(ALERT_RULES_PATH.read_text(encoding="utf-8"))
    blackbox_cfg = yaml.safe_load(BLACKBOX_CFG_PATH.read_text(encoding="utf-8"))
    am_cfg = yaml.safe_load(ALERTMANAGER_CFG_PATH.read_text(encoding="utf-8"))

    # Prometheus alerting targets must NOT be empty
    alertmanagers = prom_cfg.get("alerting", {}).get("alertmanagers", [])
    assert len(alertmanagers) >= 1
    targets = []
    for am in alertmanagers:
        for sc in am.get("static_configs", []):
            targets.extend(sc.get("targets", []))
    assert "alertmanager:9093" in targets, f"Expected alertmanager:9093 in alertmanagers targets, got {targets}"

    # Rule files linkage
    assert "alert_rules.yml" in prom_cfg.get("rule_files", [])

    # Blackbox http_2xx module present
    assert "http_2xx" in blackbox_cfg.get("modules", {})

    # Alertmanager route, routes, inhibit_rules, and receivers
    route = am_cfg.get("route", {})
    assert route.get("receiver")
    assert len(route.get("routes", [])) >= 2
    assert len(am_cfg.get("inhibit_rules", [])) >= 1
    receivers = am_cfg.get("receivers", [])
    receiver_names = {r["name"] for r in receivers}
    assert {"critical-pager", "warning-webhook"}.issubset(receiver_names)

    # Rules groups present
    assert len(rules_cfg.get("groups", [])) >= 1


def test_prometheus_scrape_targets_match_compose_services():
    """Verify every scrape target maps to a real Compose service and redis:6379 is never scraped directly."""
    compose_data = yaml.safe_load(PROD_COMPOSE_PATH.read_text(encoding="utf-8"))
    compose_services = set(compose_data.get("services", {}).keys())

    # Required monitoring stack services (all pinned, no :latest)
    for required_svc in ("prometheus", "alertmanager", "redis-exporter", "postgres-exporter", "blackbox-exporter", "grafana"):
        assert required_svc in compose_services, f"Missing Compose monitoring service: {required_svc}"
        svc_img = compose_data["services"][required_svc]["image"]
        assert ":latest" not in svc_img

    prom_cfg = yaml.safe_load(PROMETHEUS_CFG_PATH.read_text(encoding="utf-8"))
    scrape_jobs = {j["job_name"]: j for j in prom_cfg.get("scrape_configs", [])}

    assert {"prometheus", "liteads", "redis", "postgres", "blackbox-http"}.issubset(scrape_jobs.keys())

    # Redis & Postgres must scrape exporters, NOT direct DB/Redis ports
    redis_targets = scrape_jobs["redis"]["static_configs"][0]["targets"]
    assert "redis-exporter:9121" in redis_targets
    assert "redis:6379" not in redis_targets

    pg_targets = scrape_jobs["postgres"]["static_configs"][0]["targets"]
    assert "postgres-exporter:9187" in pg_targets

    # Blackbox probe targets ad-server readiness and relabels through blackbox-exporter:9115
    bb_job = scrape_jobs["blackbox-http"]
    assert "http://ad-server:8000/health/ready" in bb_job["static_configs"][0]["targets"]
    relabels = bb_job.get("relabel_configs", [])
    assert any(r.get("replacement") == "blackbox-exporter:9115" for r in relabels)

    # All scrape target hosts exist in Compose or localhost
    for job in scrape_jobs.values():
        if job["job_name"] == "blackbox-http":
            continue
        for sc in job.get("static_configs", []):
            for t in sc.get("targets", []):
                host = t.split(":", 1)[0]
                assert host == "localhost" or host in compose_services, (
                    f"Scrape target '{t}' in job '{job['job_name']}' does not match any Compose service"
                )


def test_alert_rules_metrics_syntax_and_no_orphans():
    """Verify every alert rule in alert_rules.yml references real metrics and has valid PromQL syntax."""
    rules_cfg = yaml.safe_load(ALERT_RULES_PATH.read_text(encoding="utf-8"))
    prom_cfg = yaml.safe_load(PROMETHEUS_CFG_PATH.read_text(encoding="utf-8"))
    has_blackbox = any(j["job_name"] == "blackbox-http" for j in prom_cfg.get("scrape_configs", []))

    registered_metrics = _registered_prometheus_names()
    duration_re = re.compile(r"^\d+[smhd]$")

    all_rules = []
    for group in rules_cfg.get("groups", []):
        all_rules.extend(group.get("rules", []))

    assert len(all_rules) >= 10

    for rule in all_rules:
        name = rule["alert"]
        expr = rule["expr"]
        assert _check_balanced_delimiters(expr), f"Unbalanced PromQL delimiters in {name}: {expr}"
        assert duration_re.match(str(rule.get("for", ""))), f"Invalid 'for' duration in {name}"
        assert rule.get("labels", {}).get("severity") in ("critical", "warning")
        assert rule.get("annotations", {}).get("summary")
        assert rule.get("annotations", {}).get("runbook_url")

        # Remove quoted string literals before extracting metric names
        expr_no_strings = re.sub(r'"[^"]*"|\'[^\']*\'', "", expr)
        # Remove range durations like [5m], [10m]
        expr_clean = re.sub(r"\[\d+[smhd]\]", "", expr_no_strings)
        tokens = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", expr_clean)

        for tok in tokens:
            if tok in PROMQL_KEYWORDS_AND_LABELS:
                continue
            if tok == "probe_success":
                assert has_blackbox, f"probe_success referenced in {name} without blackbox-http scrape job"
                continue
            if tok in EXPORTER_AND_BUILTIN_METRICS:
                continue
            # Must be a real registered RivinityADS metric (checking base name for _total/_bucket/_count)
            base_tok = re.sub(r"_(total|bucket|count|sum)$", "", tok)
            assert tok in registered_metrics or base_tok in registered_metrics, (
                f"Orphan/unregistered metric '{tok}' referenced in alert '{name}'"
            )


def test_no_hardcoded_secrets_or_real_webhooks_in_monitoring_configs():
    """Verify zero real Slack/PagerDuty/Discord URLs, tokens, or plaintext passwords exist in configs."""
    forbidden_patterns = (
        r"hooks\.slack\.com",
        r"events\.pagerduty\.com",
        r"discord\.com/api/webhooks",
        r"xox[baprs]-",
        r"password\s*:\s*['\"]?(?!\$\{)[a-zA-Z0-9]",
    )
    for p in (PROMETHEUS_CFG_PATH, ALERT_RULES_PATH, BLACKBOX_CFG_PATH, ALERTMANAGER_CFG_PATH):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            assert not re.search(pat, text, flags=re.IGNORECASE), (
                f"Potential hardcoded secret/webhook matching '{pat}' found in {p.name}"
            )


@pytest.mark.asyncio
async def test_worker_heartbeats_conversion_and_ledger_metrics_emitted():
    """Verify worker heartbeat gauge, Redis sync, conversion counter, and ledger counter work."""
    now_ts = time.time()
    for idx, wtype in enumerate(WORKER_HEARTBEAT_TYPES):
        record_worker_heartbeat(wtype, now_ts + idx)
        val = WORKER_LAST_HEARTBEAT_TIMESTAMP.labels(worker_type=wtype)._value.get()
        assert val == pytest.approx(now_ts + idx)

    # Test publish + Redis sync roundtrip via mocked redis_client
    fake_store: dict[str, str] = {}

    async def fake_set(key: str, value: str, ttl: int | None = None, **kwargs):
        fake_store[key] = value
        return True

    async def fake_get(key: str):
        return fake_store.get(key)

    with patch("liteads.common.cache.redis_client._connected", True), \
         patch("liteads.common.cache.redis_client.set", side_effect=fake_set), \
         patch("liteads.common.cache.redis_client.get", side_effect=fake_get):
        await publish_worker_heartbeat("optimization", 1700000123.0)
        assert fake_store["worker:heartbeat:optimization"] == "1700000123.0"

        WORKER_LAST_HEARTBEAT_TIMESTAMP.labels(worker_type="optimization").set(0.0)
        synced = await sync_worker_heartbeats_from_redis()
        assert synced["optimization"] == pytest.approx(1700000123.0)
        assert WORKER_LAST_HEARTBEAT_TIMESTAMP.labels(worker_type="optimization")._value.get() == pytest.approx(1700000123.0)

        # Scrape /metrics endpoint and confirm Prometheus text includes heartbeat & registered metrics
        resp = await metrics_endpoint()
        assert resp.status_code == 200
        body = resp.body.decode("utf-8")
        assert 'liteads_worker_last_heartbeat_timestamp{worker_type="optimization"}' in body

    # Test conversion & ledger discrepancy counters
    before_conv = AD_CONVERSIONS_TOTAL.labels(campaign_id="999", creative_id="888")._value.get()
    record_conversion(999, 888)
    after_conv = AD_CONVERSIONS_TOTAL.labels(campaign_id="999", creative_id="888")._value.get()
    assert after_conv == before_conv + 1

    before_ledger = LEDGER_UNBALANCED_TRANSACTIONS_TOTAL._value.get()
    record_ledger_discrepancy(2)
    after_ledger = LEDGER_UNBALANCED_TRANSACTIONS_TOTAL._value.get()
    assert after_ledger == before_ledger + 2
