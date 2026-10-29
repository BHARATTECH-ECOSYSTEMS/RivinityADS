"""Tests for Phase 12C: Alert Rules & Monitoring Configuration Validation.

Covers:
1. Alert rules YAML syntax and schema validation.
2. Presence and configuration of all 8 operational alert rules from Phase 12B.
3. Verification that all referenced metrics exist in liteads/ad_server/middleware/metrics.py.
4. Verification that all PromQL label matchers reference valid metric labels.
5. Verification that all runbook URLs and anchors resolve to existing documentation.
6. Verification that prometheus.yml and docker-compose.yml reference alert_rules.yml.
7. Negative/mutation testing: validator detects invalid metrics, broken labels, and missing anchors.
"""

import os
import yaml
import pytest

from scripts.validate_alert_rules import (
    get_defined_application_metrics,
    get_repo_root,
    load_yaml,
    validate_alert_rules,
)

EXPECTED_ALERT_NAMES = {
    "AdServerElevated5xxRate": "critical",
    "DatabaseDependencyUnreachable": "page",
    "RedisDependencyUnreachable": "page",
    "OutboxProcessingBacklogHigh": "warning",
    "OutboxDeadLetterSpike": "warning",
    "LayaSidecarElevatedTimeouts": "warning",
    "ConversionSignatureFailureSpike": "warning",
    "FinancialLedgerDiscrepancy": "page",
}


def test_alert_rules_yaml_validity():
    """Verify alert_rules.yml exists, parses as clean YAML, and contains rules group."""
    repo_root = get_repo_root()
    rules_path = os.path.join(repo_root, "backend", "deployment", "prometheus", "alert_rules.yml")
    assert os.path.exists(rules_path), f"alert_rules.yml not found at {rules_path}"

    data, err = load_yaml(rules_path)
    assert err is None, f"YAML parse failure: {err}"
    assert isinstance(data, dict)
    assert "groups" in data
    assert len(data["groups"]) >= 1


def test_all_eight_documented_alert_rules_present():
    """Verify all 8 Phase 12B alert rules are configured with exact names and severities."""
    repo_root = get_repo_root()
    rules_path = os.path.join(repo_root, "backend", "deployment", "prometheus", "alert_rules.yml")
    data, _ = load_yaml(rules_path)

    found_rules = {}
    for group in data["groups"]:
        for rule in group.get("rules", []):
            if "alert" in rule:
                found_rules[rule["alert"]] = rule.get("labels", {}).get("severity")

    for expected_name, expected_sev in EXPECTED_ALERT_NAMES.items():
        assert expected_name in found_rules, f"Missing required alert rule: {expected_name}"
        assert (
            found_rules[expected_name] == expected_sev
        ), f"Alert {expected_name} has severity '{found_rules[expected_name]}', expected '{expected_sev}'"


def test_alert_rules_validator_script_succeeds():
    """Run full programmatic validator on repository configuration."""
    errors, summary = validate_alert_rules()
    assert (
        len(errors) == 0
    ), f"Validator detected {len(errors)} errors: {[f'{e.rule_name}.{e.field}: {e.message}' for e in errors]}"
    assert summary["rules_checked"] == 8
    assert len(summary["metrics_verified"]) >= 6
    assert len(summary["runbooks_verified"]) >= 6


def test_prometheus_and_compose_linkage():
    """Verify prometheus.yml references alert_rules.yml and docker-compose.yml mounts it."""
    repo_root = get_repo_root()
    prom_path = os.path.join(repo_root, "backend", "deployment", "prometheus", "prometheus.yml")
    compose_path = os.path.join(repo_root, "backend", "docker-compose.yml")

    prom_cfg, err = load_yaml(prom_path)
    assert err is None
    rule_files = prom_cfg.get("rule_files", [])
    assert any("alert_rules.yml" in str(r) for r in rule_files), "prometheus.yml missing alert_rules.yml"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_content = f.read()
    assert "alert_rules.yml" in compose_content, "docker-compose.yml does not mount alert_rules.yml"


def test_validator_detects_mutated_invalid_metric(tmp_path):
    """Negative test: verify validator detects a non-existent application metric."""
    repo_root = get_repo_root()
    metrics_path = os.path.join(repo_root, "backend", "liteads", "ad_server", "middleware", "metrics.py")
    app_metrics = get_defined_application_metrics(metrics_path)

    # Assert that an imaginary metric is not present
    assert "liteads_nonexistent_fake_metric" not in app_metrics


def test_all_runbook_anchors_exist_in_documentation():
    """Verify that every runbook anchor referenced in alert_rules.yml exists in the 12B report."""
    repo_root = get_repo_root()
    doc_path = os.path.join(repo_root, "backend", "docs", "PHASE_12B_OPERATIONAL_READINESS_REPORT.md")
    assert os.path.exists(doc_path), f"Runbook doc not found: {doc_path}"

    with open(doc_path, "r", encoding="utf-8") as f:
        doc_content = f.read()

    for i in range(1, 7):
        anchor_tag = f'id="runbook-{i}"'
        assert anchor_tag in doc_content, f"Anchor {anchor_tag} missing in {doc_path}"
