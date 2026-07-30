#!/usr/local/bin/bash-vuln
# status.sh — benign CGI "system status" wrapper (uptime + memory).
# This script is the VEHICLE, not the vulnerability: it is harmless. The RCE comes
# from its interpreter (/usr/local/bin/bash-vuln) being a Shellshock-vulnerable bash,
# which parses crafted HTTP_* env headers at startup.
echo "Content-Type: text/html"
echo ""
echo "<!DOCTYPE html><html><head><title>SCL Status</title></head><body>"
echo "<h1>SCL Systems &mdash; System Status</h1>"
echo "<h2>Uptime</h2><pre>$(uptime 2>/dev/null || echo 'n/a')</pre>"
echo "<h2>Memory</h2><pre>$(free -m 2>/dev/null | head -3 || echo 'n/a')</pre>"
echo "<p class='muted'>node: $(hostname 2>/dev/null || echo this-host)</p>"
echo "</body></html>"
