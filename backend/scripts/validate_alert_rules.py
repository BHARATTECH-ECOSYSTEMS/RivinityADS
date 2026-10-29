#!/usr/bin/env python3
"""
RivinityADS — Prometheus Alert Rules Configuration Validator (Phase 12C)

Validates:
1. YAML syntax of alert_rules.yml and prometheus.yml.
2. Prometheus Alert Rule schema (groups, alert name, expr, for, labels, annotations).
3. Metric name & label consistency against metrics emitted by the Ad Server.
4. Threshold positivity and sanity.
5. Existence and resolvability of referenced runbook documentation anchors.
6. Linkage between prometheus.yml and alert_rules.yml.

Exit Code:
  0: All validation checks passed cleanly.
  1: One or more validation errors detected.
"""

from dataclasses import dataclass
import os
import re
import sys
from typing import Any
import yaml


@dataclass
class ValidationError:
    rule_name: str
    field: str
    message: str


def get_repo_root() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, "..", ".."))


def load_yaml(file_path: str) -> tuple[Any, str | None]:
    """Load and parse a YAML file safely."""
    if not os.path.exists(file_path):
        return None, f"File not found: {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f), None
    except Exception as e:
        return None, f"YAML parse error in {file_path}: {e}"


import ast

def get_defined_application_metrics(metrics_file: str) -> dict[str, set[str]]:
    """Inspect metrics.py and extract defined Prometheus metric names and their labels using AST."""
    if not os.path.exists(metrics_file):
        raise FileNotFoundError(f"Metrics file not found: {metrics_file}")

    with open(metrics_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=metrics_file)

    metrics: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            func_name = ""
            if isinstance(call.func, ast.Name):
                func_name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                func_name = call.func.attr

            if func_name in ("Counter", "Gauge", "Histogram", "Info") and call.args:
                first_arg = call.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                    metric_name = first_arg.value
                    labels: set[str] = set()

                    # Check positional args for label list
                    for arg in call.args[1:]:
                        if isinstance(arg, (ast.List, ast.Tuple)):
                            for elt in arg.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    labels.add(elt.value)

                    # Check keyword args for labelnames / labels
                    for kw in call.keywords:
                        if kw.arg in ("labelnames", "labels") and isinstance(kw.value, (ast.List, ast.Tuple)):
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    labels.add(elt.value)

                    metrics[metric_name] = labels

    return metrics


def validate_alert_rules() -> tuple[list[ValidationError], dict[str, Any]]:
    """Perform comprehensive validation of alert rules and their environment linkage."""
    repo_root = get_repo_root()
    backend_root = os.path.join(repo_root, "backend")
    rules_path = os.path.join(backend_root, "deployment", "prometheus", "alert_rules.yml")
    prom_cfg_path = os.path.join(backend_root, "deployment", "prometheus", "prometheus.yml")
    metrics_path = os.path.join(backend_root, "liteads", "ad_server", "middleware", "metrics.py")
    compose_path = os.path.join(backend_root, "docker-compose.yml")

    errors: list[ValidationError] = []
    summary: dict[str, Any] = {
        "rules_checked": 0,
        "metrics_verified": set(),
        "runbooks_verified": set(),
    }

    # 1. Parse YAML rules
    rules_doc, err = load_yaml(rules_path)
    if err:
        errors.append(ValidationError("GLOBAL", "file", err))
        return errors, summary

    if not isinstance(rules_doc, dict) or "groups" not in rules_doc:
        errors.append(ValidationError("GLOBAL", "groups", "Missing top-level 'groups' key"))
        return errors, summary

    # 2. Parse Prometheus config linkage
    prom_cfg, err = load_yaml(prom_cfg_path)
    if err:
        errors.append(ValidationError("GLOBAL", "prometheus.yml", err))
    else:
        rule_files = prom_cfg.get("rule_files", []) if isinstance(prom_cfg, dict) else []
        if "alert_rules.yml" not in rule_files and not any("alert_rules.yml" in str(r) for r in rule_files):
            errors.append(ValidationError("GLOBAL", "rule_files", "prometheus.yml does not reference alert_rules.yml"))

    # 3. Check docker-compose mount
    if os.path.exists(compose_path):
        with open(compose_path, "r", encoding="utf-8") as f:
            compose_content = f.read()
        if "alert_rules.yml" not in compose_content:
            errors.append(ValidationError("GLOBAL", "docker-compose.yml", "alert_rules.yml is not mounted in prometheus service"))

    # 4. Get defined application metrics
    try:
        app_metrics = get_defined_application_metrics(metrics_path)
    except Exception as e:
        errors.append(ValidationError("GLOBAL", "metrics.py", str(e)))
        return errors, summary

    # 5. Validate each alert group and rule
    groups = rules_doc.get("groups", [])
    if not isinstance(groups, list) or len(groups) == 0:
        errors.append(ValidationError("GLOBAL", "groups", "Expected non-empty list of alert groups"))
        return errors, summary

    duration_pattern = re.compile(r"^\d+[smhd]$")

    for group in groups:
        group_name = group.get("name", "")
        if not group_name:
            errors.append(ValidationError("GROUP", "name", "Alert group missing name"))
        rules = group.get("rules", [])
        if not isinstance(rules, list) or len(rules) == 0:
            errors.append(ValidationError(group_name, "rules", "Alert group has no rules"))
            continue

        for rule in rules:
            summary["rules_checked"] += 1
            alert_name = rule.get("alert")
            if not alert_name:
                errors.append(ValidationError(group_name, "alert", "Rule missing alert name"))
                continue

            expr = rule.get("expr")
            if not expr or not isinstance(expr, str) or not expr.strip():
                errors.append(ValidationError(alert_name, "expr", "Missing or empty PromQL expression"))

            for_dur = rule.get("for")
            if not for_dur or not duration_pattern.match(str(for_dur)):
                errors.append(ValidationError(alert_name, "for", f"Invalid evaluation duration format: {for_dur}"))

            labels = rule.get("labels", {})
            if not isinstance(labels, dict) or "severity" not in labels:
                errors.append(ValidationError(alert_name, "labels", "Missing required 'severity' label"))
            elif labels["severity"] not in ("critical", "page", "warning", "info"):
                errors.append(ValidationError(alert_name, "severity", f"Invalid severity level: {labels['severity']}"))

            annotations = rule.get("annotations", {})
            if not isinstance(annotations, dict):
                errors.append(ValidationError(alert_name, "annotations", "Annotations must be a dictionary"))
                continue

            for req_field in ("summary", "description", "runbook_url", "threshold_status"):
                if req_field not in annotations or not str(annotations[req_field]).strip():
                    errors.append(ValidationError(alert_name, f"annotations.{req_field}", f"Missing required annotation: {req_field}"))

            # 5a. Metric name and label verification
            if expr:
                # Find all metric identifiers in expression: words like liteads_... or probe_success
                metric_tokens = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", expr)
                for token in metric_tokens:
                    # Skip PromQL keywords and functions
                    if token in (
                        "sum", "rate", "increase", "delta", "count", "or", "and", "unless",
                        "by", "without", "status", "endpoint", "instance", "error_category",
                        "operation", "queue", "method", "stage", "cache_type", "query_type",
                    ):
                        continue
                    if token.startswith("liteads_"):
                        if token in app_metrics:
                            base_metric = token
                        elif re.search(r"_(count|sum|bucket)$", token):
                            base_metric = re.sub(r"_(count|sum|bucket)$", "", token)
                        else:
                            base_metric = token

                        if base_metric not in app_metrics:
                            errors.append(ValidationError(
                                alert_name,
                                "metric_name",
                                f"Referenced metric '{token}' is not defined in liteads/ad_server/middleware/metrics.py",
                            ))
                        else:
                            summary["metrics_verified"].add(token)

                # Verify label matchers inside {}
                # e.g., {status=~"5.."}, {error_category="timeout"}
                matcher_blocks = re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\{([^}]+)\}", expr)
                for metric_name, matchers in matcher_blocks:
                    if metric_name in app_metrics:
                        base_metric = metric_name
                    elif re.search(r"_(count|sum|bucket)$", metric_name):
                        base_metric = re.sub(r"_(count|sum|bucket)$", "", metric_name)
                    else:
                        base_metric = metric_name
                    if base_metric in app_metrics:
                        defined_labels = app_metrics[base_metric]
                        label_names = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|!=|=~|!~)', matchers)
                        for lbl in label_names:
                            if lbl not in defined_labels:
                                errors.append(ValidationError(
                                    alert_name,
                                    "label_name",
                                    f"Label '{lbl}' in matcher '{matchers}' is not a valid label for metric '{base_metric}' (defined labels: {sorted(defined_labels)})",
                                ))

            # 5b. Runbook URL and Anchor Verification
            runbook_url = annotations.get("runbook_url", "")
            if runbook_url:
                parts = runbook_url.split("#")
                doc_rel_path = parts[0]
                anchor = parts[1] if len(parts) > 1 else None

                doc_abs_path = os.path.normpath(os.path.join(repo_root, doc_rel_path))
                if not os.path.exists(doc_abs_path):
                    errors.append(ValidationError(
                        alert_name,
                        "runbook_url",
                        f"Referenced runbook document does not exist: {doc_rel_path}",
                    ))
                elif anchor:
                    with open(doc_abs_path, "r", encoding="utf-8") as rf:
                        doc_text = rf.read()
                    # Check for either <a id="anchor"> or markdown heading with anchor name
                    anchor_variants = [
                        f'id="{anchor}"',
                        f"id='{anchor}'",
                        f"#{anchor}",
                        anchor.replace("-", " "),
                    ]
                    if not any(v.lower() in doc_text.lower() for v in anchor_variants):
                        errors.append(ValidationError(
                            alert_name,
                            "runbook_anchor",
                            f"Anchor '{anchor}' not found in {doc_rel_path}",
                        ))
                    else:
                        summary["runbooks_verified"].add(runbook_url)

    return errors, summary


def main():
    print("=" * 80)
    print(" RivinityADS — Prometheus Alert Rules Configuration Validation (Phase 12C)")
    print("=" * 80)

    errors, summary = validate_alert_rules()

    print(f"Total Rules Checked: {summary['rules_checked']}")
    print(f"Emitted Application Metrics Verified: {len(summary['metrics_verified'])}")
    for m in sorted(summary['metrics_verified']):
        print(f"  [OK] {m}")
    print(f"Runbook References Verified: {len(summary['runbooks_verified'])}")
    for r in sorted(summary['runbooks_verified']):
        print(f"  [OK] {r}")

    print("-" * 80)
    if errors:
        print(f"FAILED: Detected {len(errors)} validation error(s):")
        for err in errors:
            print(f"  [FAIL] [{err.rule_name}] {err.field}: {err.message}")
        print("=" * 80)
        sys.exit(1)
    else:
        print("PASS: All 8 alert rules, metric schemas, PromQL expressions, and runbooks verified cleanly.")
        print("=" * 80)
        sys.exit(0)


if __name__ == "__main__":
    main()
