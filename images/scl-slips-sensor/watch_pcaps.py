from __future__ import annotations

import os
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

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
# The entrypoint symlinks SLIPS_BASE/dataset -> the shared /pcaps volume, so this
# default path both watches the router's captures and matches the `dataset/<name>`
# path that slips -f expects (run with cwd=SLIPS_BASE).
DATASET_DIR = SLIPS_BASE / "dataset"
OUTPUT_DIR = SLIPS_BASE / "output"
RUN_ID = os.getenv("RUN_ID", "run_local")
# Fixed cadence and behavior: no active stream snapshots, 5s poll, configurable per-PCAP timeout.
POLL_INTERVAL = 5.0
PROCESS_ACTIVE = False
PROCESS_TIMEOUT = float(os.getenv("SLIPS_PROCESS_TIMEOUT", "240"))
ACTIVE_SNAPSHOT_COOLDOWN = float(os.getenv("SLIPS_ACTIVE_SNAPSHOT_COOLDOWN", "30"))
# How many times a pcap may transiently fail SLIPS analysis before we stop
# retrying it. A failed pcap is NOT marked processed (so it retries next poll);
# only after this many failures does it get given up on.
MAX_FAILURES = int(os.getenv("SLIPS_MAX_FAILURES", "5"))
SKIP_ACTIVE = {"router.pcap", "router_stream.pcap", "switch_stream.pcap"}
SKIP_ACTIVE.add("server.pcap")
SKIP_PREFIXES = ("router_stream", "switch_stream", "server_stream")

print(f"[slips-watch] Detected SLIPS base directory: {SLIPS_BASE}", flush=True)


def _write_sentinel(path: Path, note: str) -> None:
    assurance_dir = OUTPUT_DIR / "_watch_events"
    assurance_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "run_id": RUN_ID,
        "pcap": path.name,
        "source": "watch_pcaps",
        "timestamp": time.time(),
        "note": note,
    }
    line = json.dumps(sentinel)
    with (assurance_dir / "alerts.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    defender_file = Path("/outputs") / RUN_ID / "slips" / "defender_alerts.ndjson"
    defender_file.parent.mkdir(parents=True, exist_ok=True)
    with defender_file.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(f"[slips-watch] wrote sentinel alert for {path.name}: {note}", flush=True)


def _eligible(path: Path) -> tuple[bool, str]:
    if path.name in SKIP_ACTIVE and not PROCESS_ACTIVE:
        return False, "skip_active"
    if path.name.startswith(SKIP_PREFIXES):
        return False, "skip_prefix"
    if not path.is_file():
        return False, "not_file"
    if path.suffix == ".gz":
        return False, "gzip"
    try:
        stat = path.stat()
        if stat.st_size == 0:
            return False, "empty"
    except FileNotFoundError:
        return False, "missing"
    return True, ""


def _cleanup_leftover_slips() -> None:
    """Kill stray SLIPS worker processes and the redis-server a prior (possibly
    timeout-killed) run left behind. Without this, the next run sees "the redis
    server on port 6379 is currently being used - overwrite? [y/n]" and, with no
    stdin, crashes with EOFError. The sensor container runs nothing but SLIPS +
    these two scripts, so matching these patterns is safe. Sensor-only."""
    for pat in ("redis-server", "StratosphereLinuxIPS/slips.py"):
        subprocess.run(
            ["pkill", "-9", "-f", pat],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _process(path: Path) -> None:
    print(f"[slips-watch] processing {path.name}", flush=True)
    _write_sentinel(path, "queued")
    _cleanup_leftover_slips()
    process_path = path

    # Feed the capture straight to Slips. Zeek reads Linux SLL/SLL2 (from
    # `tcpdump -i any`) natively, so no editcap normalization is needed — and
    # `editcap -T ether` only retags the global header without re-framing the
    # 20-byte SLL per-packet header, which corrupts the capture (0 decodable
    # packets). See SOC_GOD_E2E.md "Live-test caveats".
    subprocess.run(
        ["python3", str(SLIPS_BASE / "slips.py"), "-f", f"dataset/{process_path.name}"],
        cwd=str(SLIPS_BASE),
        timeout=PROCESS_TIMEOUT,
        check=True,
    )
    _write_sentinel(path, "completed")
    _cleanup_leftover_slips()


def _pcap_bounds(path: Path) -> tuple[datetime, datetime] | None:
    """Return (first_ts, last_ts) in UTC from a libpcap file."""
    # libpcap global header: magic (4), version_major (2), version_minor (2),
    # thiszone (4), sigfigs (4), snaplen (4), network (4)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(data) < 24:
        return None
    magic = int.from_bytes(data[0:4], "little")
    if magic == 0xA1B2C3D4:
        endian = "little"
    elif magic == 0xD4C3B2A1:
        endian = "big"
    else:
        return None
    offset = 24
    first = last = None
    while offset + 16 <= len(data):
        ts_sec = int.from_bytes(data[offset : offset + 4], endian)
        ts_usec = int.from_bytes(data[offset + 4 : offset + 8], endian)
        incl_len = int.from_bytes(data[offset + 8 : offset + 12], endian)
        # orig_len not needed for bounds
        ts = datetime.fromtimestamp(ts_sec + ts_usec / 1_000_000, tz=timezone.utc)
        if first is None:
            first = ts
        last = ts
        offset += 16 + incl_len
        if offset > len(data):
            break
    if first is None or last is None:
        return None
    return first, last


def _rename_output_dir(pcap_path: Path) -> None:
    bounds = _pcap_bounds(pcap_path)
    if not bounds:
        return
    start_ts, end_ts = bounds
    start_str = start_ts.strftime("%Y-%m-%d_%H-%M-%S")
    end_str = end_ts.strftime("%Y-%m-%d_%H-%M-%S")
    prefix = f"{pcap_path.name}_"
    candidates = [p for p in OUTPUT_DIR.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    if not candidates:
        return
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    new_name = f"{pcap_path.stem}_{start_str}_to_{end_str}"
    dest = OUTPUT_DIR / new_name
    if dest.exists():
        return
    try:
        latest.rename(dest)
    except OSError:
        return


def main() -> None:
    processed: set[str] = set()
    failed_counts: dict[str, int] = {}
    last_snapshot: dict[str, float] = {}
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[slips-watch] starting; watching {DATASET_DIR}", flush=True)
    last_heartbeat = 0.0
    while True:
        paths = []
        for path in DATASET_DIR.glob("*.pcap*"):
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            paths.append((mtime, path))
        for _, path in sorted(paths, key=lambda t: t[0]):
            print(f"[slips-watch] candidate discovered: {path.name}", flush=True)
            ok, reason = _eligible(path)
            if not ok:
                print(f"[slips-watch] skip {path.name}: {reason}", flush=True)
                continue
            try:
                marker = f"{path.name}:{int(path.stat().st_mtime)}:{path.stat().st_size}"
            except FileNotFoundError:
                continue

            target_path = path
            if PROCESS_ACTIVE and path.name in SKIP_ACTIVE:
                now = time.time()
                last = last_snapshot.get(path.name, 0.0)
                if now - last < ACTIVE_SNAPSHOT_COOLDOWN:
                    print(f"[slips-watch] skip {path.name}: snapshot_cooldown", flush=True)
                    continue
                snapshot = path.with_name(f"{path.stem}_{int(now)}{path.suffix}")
                try:
                    shutil.copy2(path, snapshot)
                except FileNotFoundError:
                    continue
                target_path = snapshot
                last_snapshot[path.name] = now
                try:
                    marker = f"{target_path.name}:{int(target_path.stat().st_mtime)}:{target_path.stat().st_size}"
                except FileNotFoundError:
                    continue

            if marker in processed:
                print(f"[slips-watch] already processed {path.name} ({marker})", flush=True)
                continue
            try:
                _process(target_path)
                _rename_output_dir(target_path)
                processed.add(marker)
                failed_counts.pop(marker, None)
            except subprocess.TimeoutExpired:
                # Transient: don't mark processed so the pcap retries next poll.
                n = failed_counts.get(marker, 0) + 1
                failed_counts[marker] = n
                if n >= MAX_FAILURES:
                    print(f"[slips-watch] giving up on {path.name} after {n} timeouts", flush=True)
                    _write_sentinel(path, "given_up")
                    processed.add(marker)
                else:
                    print(f"[slips-watch] SLIPS timed out for {path.name} after {PROCESS_TIMEOUT}s (attempt {n}/{MAX_FAILURES}, will retry)", flush=True)
                    _write_sentinel(path, "timeout")
            except subprocess.CalledProcessError as exc:
                # Transient: don't mark processed so the pcap retries next poll.
                n = failed_counts.get(marker, 0) + 1
                failed_counts[marker] = n
                if n >= MAX_FAILURES:
                    print(f"[slips-watch] giving up on {path.name} after {n} failures", flush=True)
                    _write_sentinel(path, "given_up")
                    processed.add(marker)
                else:
                    print(f"[slips-watch] SLIPS failed for {path.name}: {exc} (attempt {n}/{MAX_FAILURES}, will retry)", flush=True)
                    _write_sentinel(path, "failed")
        now = time.time()
        if now - last_heartbeat >= 10:
            _write_sentinel(Path("heartbeat.pcap"), "heartbeat")
            last_heartbeat = now
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
