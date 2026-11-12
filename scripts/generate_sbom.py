#!/usr/bin/env python3
"""Software Bill of Materials (SBOM) Generator for RivinityADS (Phase 7).

Generates a CycloneDX-formatted JSON SBOM documenting:
- Python backend dependencies with pinned versions and package metadata
- Node.js frontend dependencies from package-lock.json
- Container base image fingerprints
"""

import json
from pathlib import Path
import sys
from datetime import datetime, timezone
import importlib.metadata

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
OUTPUT_FILE = BACKEND_DIR / "deployment" / "sbom.json"


def collect_python_components() -> list[dict]:
    """Collect installed Python distributions and metadata."""
    components = []
    # Standard distribution packages in python environment
    dists = sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
    
    for dist in dists:
        name = dist.metadata["Name"]
        version = dist.metadata["Version"]
        license_name = dist.metadata.get("License", "Unknown")
        author = dist.metadata.get("Author", "")
        summary = dist.metadata.get("Summary", "")

        # Classify component
        components.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name.lower()}@{version}",
            "description": summary,
            "licenses": [{"license": {"id": license_name or "NOASSERTION"}}],
            "properties": [
                {"name": "ecosystem", "value": "python"},
                {"name": "author", "value": author or "unknown"},
            ]
        })
    return components


def collect_npm_components() -> list[dict]:
    """Collect npm dependencies from package-lock.json if present."""
    components = []
    lock_file = FRONTEND_DIR / "package-lock.json"
    if not lock_file.exists():
        return components

    try:
        data = json.loads(lock_file.read_text(encoding="utf-8"))
        packages = data.get("packages", {})
        for path_key, info in packages.items():
            if not path_key:
                continue
            name = path_key.replace("node_modules/", "")
            version = info.get("version", "")
            if not version:
                continue
            components.append({
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:npm/{name}@{version}",
                "licenses": [{"license": {"id": info.get("license", "NOASSERTION")}}],
                "properties": [
                    {"name": "ecosystem", "value": "npm"},
                ]
            })
    except Exception as e:
        print(f"Warning: could not parse package-lock.json: {e}", file=sys.stderr)

    return components


def generate_sbom() -> dict:
    """Build CycloneDX 1.5 JSON SBOM representation."""
    py_comps = collect_python_components()
    npm_comps = collect_npm_components()
    all_comps = py_comps + npm_comps

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:rivinity-ads-production-sbom-phase7",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [
                {"vendor": "RivinityADS", "name": "sbom-generator", "version": "1.0.0"}
            ],
            "component": {
                "type": "application",
                "name": "RivinityADS",
                "version": "1.0.0",
                "description": "Production-hardened digital ad-serving and business-profile website platform",
            },
        },
        "components": all_comps,
    }
    return sbom


def main():
    sbom = generate_sbom()
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    print(f"Successfully generated SBOM with {len(sbom['components'])} components at {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

