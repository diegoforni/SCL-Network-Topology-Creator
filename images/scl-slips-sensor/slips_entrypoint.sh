#!/bin/bash
set -euo pipefail

# SCL SLIPS sensor entrypoint.
# Adapted from Trident's slips_entrypoint.sh:
#   - DROPPED: setup_ssh_keys.sh, known_hosts clearing (no SSH exec path).
#   - DROPPED: in-container defender_api / planner / auto_responder uvicorns
#     (the response side now lives in the agent-manager plugin).
#   - KEPT:    SLIPS base detection, HTTP-analyzer patch application, Zeek HTTP
#              enable, forward_alerts + watch_pcaps (the sensor pipeline).
#   - CHANGED: pcaps come from the shared /pcaps volume (PCAP_DIR); DEFENDER_URL
#              is NOT hardcoded here — forward_alerts reads it from the container
#              env (set by the network-topology compose to the agent-manager URL).

: "${RUN_ID:=run_local}"
: "${SLIPS_PROCESS_TIMEOUT:=240}"

# Dynamic SLIPS base detection
SLIPS_BASE="/StratosphereLinuxIPS"
if [ ! -f "$SLIPS_BASE/slips.py" ]; then
    if [ -f "/opt/slips/slips.py" ]; then
        SLIPS_BASE="/opt/slips"
    elif [ -f "/usr/local/slips/slips.py" ]; then
        SLIPS_BASE="/usr/local/slips"
    fi
fi

SLIPS_OUTPUT_DIR="$SLIPS_BASE/output"

mkdir -p "/outputs/${RUN_ID}/pcaps" "/outputs/${RUN_ID}/slips" "/pcaps" "${SLIPS_OUTPUT_DIR}"
# Point SLIPS's dataset dir at the shared /pcaps volume. This aligns three paths:
# the router writes /pcaps, watch_pcaps watches SLIPS_BASE/dataset, and slips -f
# is invoked with `dataset/<name>` (cwd=SLIPS_BASE) — all resolve to the same files.
# (SLIPS ships dataset as a real dir; replace it with the symlink.)
rm -rf "$SLIPS_BASE/dataset"
ln -sfn /pcaps "$SLIPS_BASE/dataset"

# Sync local ports_info overrides into the Slips runtime path (if directory exists)
if [ -d "$SLIPS_BASE/slips_files/ports_info" ]; then
    cp -f /opt/lab/slips_files/ports_info/services.csv "$SLIPS_BASE/slips_files/ports_info/services.csv" || true
fi

# Clear stale pcaps so SLIPS processes fresh captures promptly
find /pcaps -maxdepth 1 -type f -name "*.pcap*" -delete || true

export RUN_ID
export SLIPS_PROCESS_TIMEOUT
export SLIPS_OUTPUT_DIR
# DEFENDER_URL is inherited from the container env (set by compose to the
# agent-manager /api/defender/alerts URL); forward_alerts.py reads it directly.

# Tune SLIPS's module set for fast, deterministic per-pcap analysis on this
# sensor. The base image default leaves heavy modules enabled that make each run
# exceed PROCESS_TIMEOUT (240s) and get killed mid-shutdown:
#   - update_manager : re-downloads ~42 TI feeds from the internet (~6 min/run)
#   - rnn_cc_detection / flowmldetection : load ML models (slow, memory-heavy)
#   - virustotal : needs an API key (absent -> stalls)
#   - timeline / threat_intelligence / blocking : slow post-detection work
#   - ip_info / risk_iq / leak_detector : external-query enrichment modules
#     that hang during shutdown (SLIPS waits on them -> run exceeds timeout)
# Disabling them keeps exactly the detectors we want for internal attack
# detection - flowalerts, network_discovery (port scans), http_analyzer
# (password-guessing), brute_force_detector, anomaly_detection - and lets a run
# finish in ~60-90s instead of >240s. This is the SLIPS sensor's OWN module
# selection; it does NOT change how the agents access the internet. Idempotent.
echo "🔧 Tuning SLIPS module disable list for sensor use..."
python3 - "$SLIPS_BASE/config/slips.yaml" <<'PY'
import re, sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    print(f"⚠️ {path} not found - module list not tuned")
    sys.exit(0)
text = path.read_text(encoding="utf-8")
INTENDED = ["template", "rnn_cc_detection", "flowmldetection", "threat_intelligence",
            "update_manager", "virustotal", "timeline", "blocking", "ip_info",
            "risk_iq", "leak_detector"]
wanted = "  disable: [" + ", ".join(INTENDED) + "]"
m = re.search(r'^[ \t]*disable:[ \t]*\[[^\]]*\]', text, re.MULTILINE)
if not m:
    print(f"⚠️ no disable: list found in {path}")
elif m.group(0).strip() == wanted.strip():
    print(f"✅ SLIPS disable list already tuned in {path}")
else:
    path.write_text(text[:m.start()] + wanted + text[m.end():])
    print(f"✅ Tuned SLIPS disable list -> [{', '.join(INTENDED)}]")
PY

cd /opt/lab

if [[ "${DNS_ONLY_DEFENSE:-}" =~ ^(true|1|yes|on)$ ]]; then
    echo "✅ DNS_ONLY_DEFENSE enabled: tuning SLIPS for DNS-only analysis"
    python3 - <<'PY'
import re
from pathlib import Path

path = Path("/StratosphereLinuxIPS/config/slips.yaml")
text = path.read_text(encoding="utf-8")

replacements = {
    r"^(\s*time_window_width:)\s*.*$": "\\1 60",
    r"^(\s*analysis_direction:)\s*.*$": "\\1 out",
    r"^(\s*pcapfilter:)\s*.*$": "\\1 'port 53'",
    r"^(\s*disable:)\s*.*$": "\\1 [template, rnn_cc_detection, flowmldetection, threat_intelligence, update_manager, virustotal, timeline, blocking, networkdiscovery]",
}

for pattern, replacement in replacements.items():
    text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

path.write_text(text, encoding="utf-8")
PY
fi

# Apply HTTP analyzer password-guessing detection patches
echo "🔧 Applying HTTP analyzer patches..."
HTTP_ANALYZER_DIR=""
if [ -d "/StratosphereLinuxIPS/modules/http_analyzer" ]; then
    HTTP_ANALYZER_DIR="/StratosphereLinuxIPS/modules/http_analyzer"
elif [ -d "/StratosphereLinuxIPS/slips_modules/http_analyzer" ]; then
    HTTP_ANALYZER_DIR="/StratosphereLinuxIPS/slips_modules/http_analyzer"
elif [ -d "/opt/slips/modules/http_analyzer" ]; then
    HTTP_ANALYZER_DIR="/opt/slips/modules/http_analyzer"
fi

if [ -n "$HTTP_ANALYZER_DIR" ]; then
    if [ -f "/opt/lab/patches/http_analyzer/http_analyzer.py" ]; then
        cp /opt/lab/patches/http_analyzer/http_analyzer.py "$HTTP_ANALYZER_DIR/http_analyzer.py"
        echo "✅ Applied http_analyzer.py patch to $HTTP_ANALYZER_DIR"
    else
        echo "⚠️ http_analyzer.py patch not found, skipping"
    fi

    if [ -f "/opt/lab/patches/http_analyzer/set_evidence.py" ]; then
        cp /opt/lab/patches/http_analyzer/set_evidence.py "$HTTP_ANALYZER_DIR/set_evidence.py"
        echo "✅ Applied set_evidence.py patch to $HTTP_ANALYZER_DIR"
    else
        echo "⚠️ set_evidence.py patch not found, skipping"
    fi
else
    echo "⚠️ Could not find http_analyzer module directory - patches not applied"
fi

# Ensure HTTP protocol is enabled in Zeek
echo "🔧 Ensuring HTTP protocol analysis is enabled..."
ZEEK_LOAD_FILE=""
if [ -f "/StratosphereLinuxIPS/zeek-scripts/__load__.zeek" ]; then
    ZEEK_LOAD_FILE="/StratosphereLinuxIPS/zeek-scripts/__load__.zeek"
elif [ -f "/StratosphereLinuxIPS/zeek/__load__.zeek" ]; then
    ZEEK_LOAD_FILE="/StratosphereLinuxIPS/zeek/__load__.zeek"
elif [ -f "/usr/local/zeek/share/zeek/__load__.zeek" ]; then
    ZEEK_LOAD_FILE="/usr/local/zeek/share/zeek/__load__.zeek"
fi

if [ -n "$ZEEK_LOAD_FILE" ]; then
    if ! grep -q "@load base/protocols/http/main" "$ZEEK_LOAD_FILE"; then
        sed -i '/^@load \.\/slips-conf\.zeek/a @load base/protocols/http/main' "$ZEEK_LOAD_FILE"
        echo "✅ Enabled HTTP protocol analysis in Zeek ($ZEEK_LOAD_FILE)"
    else
        echo "✅ HTTP protocol analysis already enabled"
    fi
else
    echo "⚠️ Could not find Zeek __load__.zeek file - HTTP analysis may not be enabled"
fi

# Patch SLIPS observer crash (stratosphereips/slips:latest regression):
# redis_manager.start_redis_cache_if_not_running passes logger="" which becomes
# a str observer; the newer printer.print() in _start_redis_server then crashes
# with "'str' object has no attribute 'update'" -> SLIPS exits 1 on every pcap ->
# zero detections. Make notify_observers skip any observer lacking .update
# (defensive choke-point fix, survives future base-image bumps). Sensor-only.
echo "🔧 Patching SLIPS notify_observers (str-observer crash)..."
IOBSERVER=""
for cand in \
    "/StratosphereLinuxIPS/slips_files/common/abstracts/iobserver.py" \
    "/opt/slips/slips_files/common/abstracts/iobserver.py" \
    "/usr/local/slips/slips_files/common/abstracts/iobserver.py"; do
    [ -f "$cand" ] && IOBSERVER="$cand" && break
done
if [ -z "$IOBSERVER" ]; then
    echo "⚠️ iobserver.py not found - str-observer patch skipped"
else
    python3 - "$IOBSERVER" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
if 'hasattr(observer, "update")' in text:
    print(f"✅ notify_observers guard already present ({path})")
else:
    old = "        for observer in self.observers:\n            observer.update(msg)"
    new = "        for observer in self.observers:\n            if hasattr(observer, \"update\"):\n                observer.update(msg)"
    if old not in text:
        print(f"⚠️ notify_observers body not matched in {path} - SLIPS layout changed, patch skipped")
    else:
        path.write_text(text.replace(old, new, 1))
        print(f"✅ Patched notify_observers str-observer guard ({path})")
PY
fi

# Sensor pipeline: forward SLIPS alerts to the agent-manager + watch /pcaps.
python3 /opt/lab/forward_alerts.py &
TAIL_PID=$!

python3 /opt/lab/watch_pcaps.py &
WATCH_PID=$!

cleanup() {
    [ -n "${TAIL_PID}" ] && kill "${TAIL_PID}" >/dev/null 2>&1 || true
    [ -n "${WATCH_PID}" ] && kill "${WATCH_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# Wait for either sensor process to exit.
wait -n "${TAIL_PID}" "${WATCH_PID}"
EXIT_CODE=$?
cleanup
wait || true
exit "${EXIT_CODE}"
