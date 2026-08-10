#!/bin/bash
# gap-fix.sh — close the residual gaps from the 150-tool sweep:
#  - install the 2 genuinely-missing binaries (responder, falco)
#  - provide the wordlist files hexstrike hard-codes at specific paths (the seclists
#    package is installed but at /usr/share/seclists, not the paths hexstrike expects)
# Fault-tolerant throughout.
set -u
export DEBIAN_FRONTEND=noninteractive
echo "=== gap-fix: install responder + falco ==="
apt-get update -qq 2>/dev/null
for p in responder falco; do
  apt-get install -y --no-install-recommends "$p" >/tmp/gap_$p.log 2>&1 && echo "gap+ $p" || echo "gap-miss $p"
done
rm -rf /var/lib/apt/lists/* 2>/dev/null

# OWASP ZAP: the Kali package installs `zaproxy`, but HexStrike's availability dict
# references the legacy `zap.sh` launcher (and some tool paths use it). Symlink so both
# names resolve — makes /health report owasp-zap available and any zap.sh call work.
if command -v zaproxy >/dev/null 2>&1 && ! command -v zap.sh >/dev/null 2>&1; then
  ln -sf "$(command -v zaproxy)" /usr/local/bin/zap.sh && echo "gap+ zap.sh -> zaproxy"
fi

echo "=== gap-fix: wordlists at hexstrike's expected paths ==="
SL=/usr/share/seclists
WL=/usr/share/wordlists
mkdir -p "$WL/dirb" "$WL/api"

# rockyou.txt (hashcat/john) — real if the wordlists pkg has it, else a small list
if [ ! -f "$WL/rockyou.txt" ]; then
  if [ -f "$WL/rockyou.txt.gz" ]; then
    gunzip -k "$WL/rockyou.txt.gz" 2>/dev/null && echo "rockyou: gunzipped"
  else
    printf 'password\n123456\n12345678\nqwerty\nadmin\nletmein\nwelcome\nmonkey\ndragon\nmaster\n' > "$WL/rockyou.txt"
    echo "rockyou: small fallback list"
  fi
fi

# dirb/common.txt (ffuf/gobuster) — symlink to seclists, else small list
if [ -f "$SL/Discovery/Web-Content/common.txt" ]; then
  ln -sf "$SL/Discovery/Web-Content/common.txt" "$WL/dirb/common.txt" && echo "dirb/common.txt -> seclists"
else
  printf 'admin\nlogin\ntest\napi\nindex.html\nrobots.txt\nconfig\nbackup\n' > "$WL/dirb/common.txt"
  echo "dirb/common.txt: small fallback"
fi

# api/api-endpoints.txt (api_fuzzer/comprehensive_api_audit)
if [ -f "$SL/Discovery/Web-Content/api/api-endpoints.txt" ]; then
  ln -sf "$SL/Discovery/Web-Content/api/api-endpoints.txt" "$WL/api/api-endpoints.txt" && echo "api-endpoints -> seclists"
else
  printf '/api/v1/users\n/api/login\n/api/health\n/swagger.json\n/openapi.json\n/api/v1/admin\n' > "$WL/api/api-endpoints.txt"
  echo "api-endpoints: small fallback"
fi
echo "=== gap-fix done ==="
