#!/bin/bash
# install-tools.sh (Kali base) — install the underlying CLIs the 150 HexStrike MCP
# tools wrap. Each package is installed INDIVIDUALLY so one missing/renamed package
# never aborts the rest (an atomic `apt-get install a b c` fails entirely if any one
# is absent). Essentials first so pip3/jdk/ruby exist for the later steps.
set -u
export DEBIAN_FRONTEND=noninteractive
export GOPATH=/root/go PATH="/usr/local/go/bin:/root/go/bin:/root/.cargo/bin:${PATH}"

echo "=== apt update ==="
apt-get update -qq

echo "=== [0] essentials (reliable single batch — all present on kali-rolling) ==="
apt-get install -y --no-install-recommends \
  openssh-server sudo curl wget jq git iproute2 procps ca-certificates passwd \
  python3 python3-dev python3-pip python3-venv ruby build-essential pkg-config \
  libffi-dev libssl-dev golang-go cargo \
  >/tmp/apt_ess.log 2>&1 || { echo "[warn] essentials partial"; tail -6 /tmp/apt_ess.log; }

echo "=== [1] per-tool install (each independent; missing ones skipped) ==="
TOOLS="
default-jdk-headless openjdk-17-jdk-headless
nmap masscan rustscan arp-scan nbtscan netdiscover traceroute netcat-traditional netcat-openbsd whois
smbclient enum4linux enum4linux-ng onesixtyone
hydra john hashcat medusa patator
nikto dirb dirsearch gobuster ffuf feroxbuster sqlmap whatweb wafw00f
dnsenum dnsrecon dnstwist fierce theharvester recon-ng
metasploit-framework ghidra zaproxy
radare2 gdb binutils binwalk foremost steghide exiftool libimage-exiftool-perl xxd vim-common seclists
nuclei subfinder httpx katana amass dnsx dalfox gau waybackurls hakrawler xsser
ROPgadget ropper checksec volatility3 python3-pwntools one_gadget dotdotpwn
testdisk photorec
"
ok=0; miss=0
for p in $TOOLS; do
  if apt-get install -y --no-install-recommends "$p" >/tmp/apt_inst_$p.log 2>&1; then
    ok=$((ok+1))
  else
    miss=$((miss+1)); echo "  [miss] $p"
  fi
done
echo "apt tools: ok=$ok miss=$miss"

echo "=== [2] pip fallbacks (python tools not via apt) ==="
pip3 install --break-system-packages --no-cache-dir \
  arjun paramspider x8 uro anew qsreplace hashpump wfuzz \
  ROPgadget ropper volatility3 \
  >/tmp/pip.log 2>&1 && echo "pip ok" || { echo "[warn] pip partial"; tail -4 /tmp/pip.log; }

echo "=== [3] gem fallbacks ==="
gem install --no-document one_gadget wpscan >/tmp/gem.log 2>&1 && echo "gem ok" || true

echo "=== [4] go fallbacks (any still-missing projectdiscovery/tomnomnom tools) ==="
if command -v go >/dev/null 2>&1; then
  for mod in \
    "github.com/projectdiscovery/dnsx/cmd/dnsx@latest" \
    "github.com/projectdiscovery/httpx/cmd/httpx@latest" \
    "github.com/projectdiscovery/katana/cmd/katana@latest" \
    "github.com/tomnomnom/waybackurls@latest" \
    "github.com/lc/gau/v2/cmd/gau@latest" \
    "github.com/hahwul/dalfox/v2@latest" \
    "github.com/hakluke/hakrawler@latest" \
    "github.com/ffuf/ffuf/v2@latest" ; do
    (go install "$mod" >/tmp/go.log 2>&1 && echo "go+ ${mod%%@*}") || true
  done
fi
for b in nuclei httpx katana subfinder dnsx ffuf gau waybackurls dalfox hakrawler amass; do
  [ -x "/root/go/bin/$b" ] && ln -sf "/root/go/bin/$b" "/usr/local/bin/$b" 2>/dev/null || true
done

echo "=== [5] rustscan via cargo (not packaged on kali arm64) ==="
if command -v cargo >/dev/null 2>&1; then
  (cargo install rustscan --locked >/tmp/cargo.log 2>&1 && ln -sf /root/.cargo/bin/rustscan /usr/local/bin/rustscan && echo "cargo+ rustscan") || true
fi

rm -rf /var/lib/apt/lists/*
# reclaim multi-GB build caches (go/cargo compile artifacts) so the layer stays small
rm -rf /root/.cache /root/go/pkg /root/.cargo/registry/cache /root/.cargo/registry/src 2>/dev/null || true
echo "=== install-tools.sh done (ok=$ok miss=$miss) ==="
