"""
Deterministic Documentation, Runbook, Script CLI & Compose Consistency Tests (Prompt 4: P1-01 & P1-06).

Verifies:
1. No deployment doc (README.md, docs/runbooks/*.md) instructs copying a nonexistent root `.env.example` (`cp .env.example`).
2. Every `docker compose` command targeting `backend/docker-compose.prod.yml` in README.md and runbooks includes `--env-file`.
3. Every Compose service targeted by `docker compose ... <subcommand> <service>` in README.md and runbooks exists in `backend/docker-compose.prod.yml` (catching drift such as `proxy` vs `nginx`).
4. Every CLI flag passed to `backup_db.py` or `restore_db.py` in README.md and Runbooks 01/03 is supported by the actual argparse parser of each script.
5. `backend/docker-compose.prod.yml` and `backend/deployment/nginx/nginx.prod.conf` agree on TLS certificate paths and ACME webroot mount paths, and `README.md` + Runbook 01 document pre-startup TLS bootstrap.
"""

from pathlib import Path
import re
import shlex
import subprocess
import sys
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
README_PATH = REPO_ROOT / "README.md"
RUNBOOKS_DIR = REPO_ROOT / "docs" / "runbooks"
PROD_COMPOSE_PATH = REPO_ROOT / "backend" / "docker-compose.prod.yml"
NGINX_CONF_PATH = REPO_ROOT / "backend" / "deployment" / "nginx" / "nginx.prod.conf"
ENV_EXAMPLE_PATH = REPO_ROOT / "backend" / ".env.example"
BACKUP_SCRIPT_PATH = REPO_ROOT / "backend" / "scripts" / "backup_db.py"
RESTORE_SCRIPT_PATH = REPO_ROOT / "backend" / "scripts" / "restore_db.py"


def _iter_doc_files() -> list[Path]:
    files = [README_PATH]
    files.extend(sorted(RUNBOOKS_DIR.glob("*.md")))
    return files


def _extract_multiline_commands(text: str, prefix_regex: str) -> list[str]:
    """Join backslash-continued shell lines matching prefix_regex into full command strings."""
    lines = text.splitlines()
    commands: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Strip leading markdown list bullet or shell prompt if any
        if re.search(prefix_regex, line) and not line.startswith("#"):
            buf = [line.rstrip("\\").strip()]
            while lines[i].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                buf.append(lines[i].strip().rstrip("\\").strip())
            commands.append(" ".join(buf))
        i += 1
    return commands


def _get_argparse_flags(script_path: Path) -> set[str]:
    """Run script --help and extract all supported --long and -short CLI flags."""
    proc = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(re.findall(r"(?<!\w)(--[a-zA-Z0-9][a-zA-Z0-9-]*|-[a-zA-Z])", proc.stdout))


def test_no_nonexistent_root_env_example_copy():
    """Verify docs reference `backend/.env.example` and never nonexistent root `.env.example`."""
    assert ENV_EXAMPLE_PATH.exists(), "backend/.env.example must exist"
    assert not (REPO_ROOT / ".env.example").exists(), "Root .env.example is expected not to exist"

    for doc_path in _iter_doc_files():
        content = doc_path.read_text(encoding="utf-8")
        # Catch `cp .env.example` or `cp ./.env.example` without `backend/`
        bad_matches = re.findall(r"\bcp\s+(?:\./)?\.env\.example\b", content)
        assert not bad_matches, f"{doc_path.name} instructs copying nonexistent root .env.example: {bad_matches}"
        # Also verify no reference to nonexistent release_migration.py
        assert "release_migration.py" not in content, (
            f"{doc_path.name} references nonexistent release_migration.py"
        )


def test_all_prod_compose_commands_include_env_file():
    """Verify every `docker compose ... docker-compose.prod.yml` invocation includes `--env-file`."""
    for doc_path in _iter_doc_files():
        content = doc_path.read_text(encoding="utf-8")
        cmds = _extract_multiline_commands(content, r"\bdocker\s+compose\b.*docker-compose\.prod\.yml")
        for cmd in cmds:
            assert "--env-file" in cmd, (
                f"Command in {doc_path.name} targets docker-compose.prod.yml without --env-file: {cmd}"
            )


def test_all_documented_compose_services_exist():
    """Verify every Compose service targeted in docs/runbooks exists in `backend/docker-compose.prod.yml`."""
    compose_data = yaml.safe_load(PROD_COMPOSE_PATH.read_text(encoding="utf-8"))
    defined_services = set(compose_data.get("services", {}).keys())
    assert "nginx" in defined_services
    assert "proxy" not in defined_services

    subcommands_taking_services = {"up", "run", "exec", "restart", "logs", "stop", "start", "rm", "ps"}
    flags_with_value = {
        "-f", "--file", "--env-file", "--profile", "-p", "--project-name",
        "-v", "--volume", "--entrypoint", "-w", "-u", "--user", "-e", "--env",
        "--tail", "-n", "--since", "--until",
    }

    for doc_path in _iter_doc_files():
        content = doc_path.read_text(encoding="utf-8")
        cmds = _extract_multiline_commands(content, r"\bdocker\s+compose\b.*docker-compose\.prod\.yml")
        for cmd in cmds:
            # Strip trailing shell chaining after && or ;
            first_cmd = re.split(r"\s*(?:&&|;)\s*", cmd)[0]
            try:
                tokens = shlex.split(first_cmd)
            except ValueError:
                tokens = first_cmd.split()

            if len(tokens) < 3 or tokens[0] != "docker" or tokens[1] != "compose":
                continue

            # Find subcommand
            idx = 2
            subcmd = None
            while idx < len(tokens):
                tok = tokens[idx]
                if tok in flags_with_value:
                    idx += 2
                    continue
                if tok.startswith("-"):
                    idx += 1
                    continue
                subcmd = tok
                idx += 1
                break

            if subcmd not in subcommands_taking_services:
                continue

            # Parse tokens after subcommand to find service name(s)
            services_referenced: list[str] = []
            expect_value = False
            for tok in tokens[idx:]:
                if expect_value:
                    expect_value = False
                    continue
                if tok in flags_with_value:
                    expect_value = True
                    continue
                if tok.startswith("-"):
                    continue
                services_referenced.append(tok)
                # For `run` or `exec`, first non-flag token is the service name; remaining tokens are container command
                if subcmd in ("run", "exec"):
                    break

            for svc in services_referenced:
                assert svc in defined_services, (
                    f"Nonexistent Compose service '{svc}' referenced in {doc_path.name} command: {cmd}"
                )


def test_documented_backup_and_restore_cli_flags_match_scripts():
    """Verify every CLI flag passed to backup_db.py / restore_db.py in README & Runbooks 01/03 is valid."""
    backup_flags = _get_argparse_flags(BACKUP_SCRIPT_PATH)
    restore_flags = _get_argparse_flags(RESTORE_SCRIPT_PATH)

    assert {"--db-url", "--backup-dir", "--encrypt", "--passphrase"}.issubset(backup_flags)
    assert {"--input", "--db-url", "--passphrase", "--verify", "--verify-only", "--allow-production-target"}.issubset(restore_flags)

    target_docs = [
        README_PATH,
        RUNBOOKS_DIR / "01_deployment.md",
        RUNBOOKS_DIR / "03_database_restore.md",
    ]
    for doc_path in target_docs:
        content = doc_path.read_text(encoding="utf-8")
        for script_name, valid_flags in (
            ("backup_db.py", backup_flags),
            ("restore_db.py", restore_flags),
        ):
            cmds = _extract_multiline_commands(content, rf"python\s+.*{re.escape(script_name)}")
            for cmd in cmds:
                used_flags = re.findall(r"(?<!\w)(--[a-zA-Z0-9][a-zA-Z0-9-]*)", cmd)
                for flag in used_flags:
                    assert flag in valid_flags, (
                        f"Unsupported flag '{flag}' for {script_name} in {doc_path.name}: {cmd}"
                    )


def test_compose_and_nginx_tls_mounts_and_bootstrap_docs_consistent():
    """Verify Compose, Nginx config, README, and Runbook 01 agree on TLS paths and pre-startup bootstrap."""
    compose_data = yaml.safe_load(PROD_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose_data["services"]
    volumes = compose_data["volumes"]

    assert "certs_data" in volumes
    assert "certbot_webroot" in volumes
    assert "letsencrypt_data" in volumes

    nginx_vols = services["nginx"]["volumes"]
    assert "certs_data:/etc/nginx/certs:ro" in nginx_vols
    assert "certbot_webroot:/var/www/certbot:ro" in nginx_vols

    assert "certbot" in services
    certbot_svc = services["certbot"]
    assert ":latest" not in certbot_svc["image"]
    assert "tls" in certbot_svc.get("profiles", [])
    assert "certs_data:/etc/nginx/certs" in certbot_svc["volumes"]
    assert "certbot_webroot:/var/www/certbot" in certbot_svc["volumes"]
    assert "letsencrypt_data:/etc/letsencrypt" in certbot_svc["volumes"]

    nginx_conf = NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert "ssl_certificate /etc/nginx/certs/fullchain.pem;" in nginx_conf
    assert "ssl_certificate_key /etc/nginx/certs/privkey.pem;" in nginx_conf
    assert "location /.well-known/acme-challenge/" in nginx_conf
    assert "root /var/www/certbot;" in nginx_conf

    # Verify README and Runbook 01 document TLS bootstrap, verification, and reload
    readme_text = README_PATH.read_text(encoding="utf-8")
    assert "certs_data" in readme_text
    assert "/etc/nginx/certs/fullchain.pem" in readme_text
    assert "/etc/nginx/certs/privkey.pem" in readme_text

    runbook_01_text = (RUNBOOKS_DIR / "01_deployment.md").read_text(encoding="utf-8")
    assert "/etc/nginx/certs/fullchain.pem" in runbook_01_text
    assert "/etc/nginx/certs/privkey.pem" in runbook_01_text
    assert "nginx -s reload" in runbook_01_text
    assert "certbot renew" in runbook_01_text
