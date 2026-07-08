from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict

import requests

# Dynamic SLIPS path detection to handle different versions
def _find_slips_base() -> Path:
    """Find the SLIPS installation directory across different versions."""
    candidates = [
        Path("/StratosphereLinuxIPS"),
        Path("/opt/slips"),
        Path("/usr/local/slips"),
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "slips.py").exists():
            return candidate
    # Default to original path if none found
    return Path("/StratosphereLinuxIPS")

SLIPS_BASE = _find_slips_base()
RUN_ID = os.getenv("RUN_ID", "run_local")
OUTPUT_ROOT = Path(os.getenv("SLIPS_OUTPUT_DIR", str(SLIPS_BASE / "output")))
DEFENDER_URL = os.getenv("DEFENDER_URL", "http://127.0.0.1:8000/alerts")
POLL_INTERVAL = float(os.getenv("SLIPS_ALERT_INTERVAL", "2"))


def _log_files() -> list[Path]:
    if not OUTPUT_ROOT.exists():
        return []
    return sorted(OUTPUT_ROOT.glob("**/alerts.log"))


def _parse_slips_alert(raw: str) -> Dict[str, object]:
    """Extract structured fields from SLIPS plain-text alert lines.

    SLIPS alert format examples:
      Src IP 10.10.0.11 . Detected HTTP password guessing ... Threat level: high.
      Src IP 10.10.0.11 . Detected Connecting to private IP: 10.20.0.11 ... Threat level: info.
    """
    out: Dict[str, object] = {"raw": raw, "run_id": RUN_ID}

    # Source IP
    m = re.search(r"Src IP\s+(\d+\.\d+\.\d+\.\d+)", raw)
    if m:
        out["sourceip"] = m.group(1)
        out["srcip"] = m.group(1)

    # Destination IP (multiple patterns)
    for pattern in [
        r"to\s+(\d+\.\d+\.\d+\.\d+)",
        r"destination\s+(?:IP|ip):?\s+(\d+\.\d+\.\d+\.\d+)",
        r"ip daddr\s+(\d+\.\d+\.\d+\.\d+)",
    ]:
        m = re.search(pattern, raw)
        if m:
            out["destip"] = m.group(1)
            out["dstip"] = m.group(1)
            break

    # Threat level
    m = re.search(r"Threat level:\s*(\w+)", raw)
    if m:
        out["threat_level"] = m.group(1).lower()

    # Attack type / description
    m = re.search(r"Detected\s+(.+?)\.(?:\s+Src IP|$)", raw)
    if m:
        out["description"] = m.group(1).strip()
        # Derive a simple attack_type from the description
        desc_lower = out["description"].lower()
        if "password guessing" in desc_lower or "brute" in desc_lower:
            out["attack_type"] = "brute_force"
            out["attackid"] = "PASSWORD_GUESSING"
        elif "port scan" in desc_lower or "scan" in desc_lower:
            out["attack_type"] = "port_scan"
            out["attackid"] = "PORT_SCAN"
        elif "denial of service" in desc_lower or "ddos" in desc_lower:
            out["attack_type"] = "ddos"
            out["attackid"] = "DDOS"
        elif "unencrypted http" in desc_lower:
            out["attack_type"] = "unencrypted_http"
            out["attackid"] = "UNENCRYPTED_HTTP"
        else:
            out["attack_type"] = "unknown"
            out["attackid"] = "UNKNOWN"

    # Confidence: high threat = 1.0, medium = 0.7, low/info = 0.5
    tl = out.get("threat_level", "").lower()
    if tl == "high":
        out["confidence"] = 1.0
    elif tl == "medium":
        out["confidence"] = 0.7
    elif tl in ("low", "info"):
        out["confidence"] = 0.5
    else:
        out["confidence"] = 0.5

    return out


def _post_alert(payload: Dict[str, object]) -> bool:
    try:
        requests.post(DEFENDER_URL, json=payload, timeout=5).raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[slips-forward] failed to POST alert: {exc}", flush=True)
        return False


def main() -> None:
    positions: Dict[Path, int] = {}
    while True:
        for log_path in _log_files():
            try:
                current_size = log_path.stat().st_size
            except FileNotFoundError:
                continue
            previous = positions.get(log_path, 0)
            if current_size < previous:
                previous = 0
            if current_size == previous:
                continue
            start_pos = previous
            try:
                with log_path.open("r", encoding="utf-8") as handle:
                    handle.seek(previous)
                    success = True
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            payload = _parse_slips_alert(line)
                        if not _post_alert(payload):
                            success = False
                            break
                    if success:
                        positions[log_path] = handle.tell()
                    else:
                        positions[log_path] = start_pos
            except FileNotFoundError:
                continue
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
