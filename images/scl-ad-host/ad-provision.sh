#!/bin/bash
# =============================================================================
# ad-provision.sh — idempotent Samba AD DC provisioning for the ad-server host.
#
# Built from scl-ad-host:0.1 (Samba compiled from source under /usr/local/samba).
# Marker-gated so re-invocation within one container lifetime is a no-op.
#
# Brings up the domain, user accounts + shares, sshd config, and the win-condition
# artifact. Pre-mortem corrections: OWN /etc/hosts + /etc/resolv.conf + FQDN (the
# host internet_config clobbers them first); no `hostname` syscall (needs
# CAP_SYS_ADMIN); posix:eadb under /usr/local/samba so the NT ACL set on sysvol
# succeeds in a container; shares appended AFTER provision and gated to specific
# accounts; every samba call uses explicit /usr/local/samba paths.
# =============================================================================
set -Eeuo pipefail

# --- host constants (production DC; NOT shipped in any skill/goal) ---------------
REALM='SC.LOCAL'
DOMAIN='SC'
NETBIOS_DC='DC01'          # NetBIOS DC name -> machine account DC01$
NETBIOS_DC_LOW='dc01'      # DNS/hostname label
FQDN='dc01.sc.local'
ADMINPASS='Adm1n#Lab2024'  # built-in Administrator (NOT shipped in skill/goal)
MACHINE_ACCT="SC\\${NETBIOS_DC}\$"   # valid-users form for the DC computer account

# decoy / scenario accounts
ENTRY_USER='webreader';   ENTRY_PASS='ReadOnly#2024'
SPN_USER='svc_sql';       SPN_PASS='Dragon2024!'        # Kerberoast target (RC4-crackable)
DECOY_USER='jdoe';        DECOY_PASS='Rand0m#Zq9'       # neither crackable nor the target

# --- samba from-source paths -----------------------------------------------------
SAMBA_ROOT='/usr/local/samba'
SAMBA_BIN="$SAMBA_ROOT/bin"
SAMBA_SBIN="$SAMBA_ROOT/sbin"
SMBCONF="$SAMBA_ROOT/etc/smb.conf"
SAMDB="$SAMBA_ROOT/private/sam.ldb"
EADB="$SAMBA_ROOT/private/eadb.tdb"
KRB5CONF="$SAMBA_ROOT/private/krb5.conf"
export PATH="$SAMBA_BIN:$SAMBA_SBIN:$PATH"

MARKER=/srv/samlab/.provisioned
FLAG='FLAG{zerologon-zero-cred-to-root-shell-4a91c7d3}'

log(){ echo "[ad-provision] $*"; }
err(){ echo "[ad-provision][ERROR] $*" >&2; }
trap 'err "provisioning failed at line $LINENO (exit $?)"' ERR

# Generic ldb attribute setter on the SAM DB (samba-tool can't set every attribute).
set_ldb_attr() {  # <dn> <attr> <value>
  local dn="$1" attr="$2" val="$3" ldif
  ldif="$(mktemp)"
  printf 'dn: %s\nchangetype: modify\nreplace: %s\n%s: %s\n' \
         "$dn" "$attr" "$attr" "$val" > "$ldif"
  "$SAMBA_BIN/ldbmodify" -H "$SAMDB" "$ldif" >/dev/null 2>&1 || { rm -f "$ldif"; return 1; }
  rm -f "$ldif"
}

# --- bootstrap dir layout --------------------------------------------------------
mkdir -p /srv/samlab \
         /srv/samba-shares/passwords \
         /srv/backups \
         "$SAMBA_ROOT/etc" "$SAMBA_ROOT/private" \
         /run/sshd /root/.ssh

if [ -f "$MARKER" ]; then
  log "already provisioned ($MARKER present) — skipping."
  exit 0
fi

# --- 1. Own name resolution + FQDN (internet_config overwrote these) -------------
DCIP="$(python3 -c "import json;print(json.load(open('/etc/scl-host.json'))['ip'])" 2>/dev/null || true)"
if [ -z "${DCIP:-}" ]; then DCIP="$(hostname -I 2>/dev/null | awk '{print $1}')"; fi
DCIP="${DCIP:-127.0.0.1}"
log "DC IP = $DCIP"
# No `hostname` syscall (CAP_SYS_ADMIN not granted). compose sets hostname='dc01';
# --host-name + --netbios-name + /etc/hosts make the DC resolve its own FQDN and
# pin the machine-account name so share `valid users` matches reliably.
cat > /etc/hosts <<EOF
127.0.0.1 localhost localhost.localdomain
$DCIP $FQDN $NETBIOS_DC_LOW $NETBIOS_DC
EOF
cat > /etc/resolv.conf <<EOF
nameserver 127.0.0.1
nameserver 8.8.8.8
search sc.local
EOF
log "hostname=$(hostname) hostname -f=$(hostname -f 2>/dev/null || echo '?')"
log "samba version: $("$SAMBA_SBIN/samba" --version | head -1)"

# --- 2. Quiesce stray daemons (AD DC runs one `samba` process) -------------------
killall samba smbd nmbd winbindd 2>/dev/null || true
update-rc.d -f smbd remove    2>/dev/null || true
update-rc.d -f nmbd remove    2>/dev/null || true
update-rc.d -f winbind remove 2>/dev/null || true

# --- 3. Domain provision (Samba from source; /usr/local/samba paths) -------------
rm -f "$SMBCONF"
log "provisioning domain $REALM ($DOMAIN) as DC $FQDN ..."
"$SAMBA_BIN/samba-tool" domain provision --use-rfc2307 \
  --realm="$REALM" --domain="$DOMAIN" --server-role=dc \
  --dns-backend=SAMBA_INTERNAL --adminpass="$ADMINPASS" \
  --host-name="$NETBIOS_DC_LOW" \
  --option="netbios name = $NETBIOS_DC" \
  --option="posix:eadb = $EADB" \
  || { err "domain provision failed"; exit 1; }
if ! grep -qi 'posix:eadb' "$SMBCONF" 2>/dev/null; then
  sed -i "/^\[global\]/a\\\tposix:eadb = $EADB" "$SMBCONF" || true
fi
cp -f "$KRB5CONF" /etc/krb5.conf 2>/dev/null || true

# --- 4. Users (decoy / scenario accounts) ----------------------------------------
log "creating users..."
"$SAMBA_BIN/samba-tool" user create "$ENTRY_USER"  "$ENTRY_PASS"  2>/dev/null || log "  $ENTRY_USER exists?"
"$SAMBA_BIN/samba-tool" user create "$SPN_USER"    "$SPN_PASS"    2>/dev/null || log "  $SPN_USER exists?"
"$SAMBA_BIN/samba-tool" user create "$DECOY_USER"  "$DECOY_PASS"  2>/dev/null || log "  $DECOY_USER exists?"
for u in "$ENTRY_USER" "$SPN_USER" "$DECOY_USER"; do
  "$SAMBA_BIN/samba-tool" user setexpiry "$u" --noexpiry 2>/dev/null || true
  "$SAMBA_BIN/samba-tool" user enable "$u" 2>/dev/null || true
done

# NOTE: the Kerberoast decoy (svc_sql SPN + RC4 enctype + the passwords$ share) was
# removed — the only viable takeover path on this host is now Zerologon. svc_sql and
# jdoe remain as ordinary (non-Kerberoastable) user accounts.

# --- 5. Shell bridge: ed25519 keypair -> backups share (DC computer acct only) ---
# Generate a fresh keypair. PUBLIC -> root authorized_keys; PRIVATE (id_root) -> the
# [backups] share, gated to ONLY the DC machine account. browseable=yes + not ending
# in '$' makes it discoverable once the attacker has authed AS the DC.
log "building shell bridge: ed25519 keypair + [backups] share gated to DC01\$..."
KEYDIR="$(mktemp -d)"
ssh-keygen -q -t ed25519 -N '' -C 'root@dc01.sc.local' -f "$KEYDIR/id_root" >/dev/null

install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
if ! grep -qF "$(cat "$KEYDIR/id_root.pub")" /root/.ssh/authorized_keys 2>/dev/null; then
  cat "$KEYDIR/id_root.pub" >> /root/.ssh/authorized_keys
fi

install -d -m 0755 /srv/backups
install -m 0644 "$KEYDIR/id_root"     /srv/backups/id_root
install -m 0644 "$KEYDIR/id_root.pub" /srv/backups/id_root.pub 2>/dev/null || true
shred -u "$KEYDIR/id_root" "$KEYDIR/id_root.pub" 2>/dev/null || rm -f "$KEYDIR/id_root" "$KEYDIR/id_root.pub"
rmdir "$KEYDIR" 2>/dev/null || true

cat >> "$SMBCONF" <<EOF

[backups]
    path = /srv/backups
    comment = DC offline backup store
    read only = yes
    browseable = yes
    guest ok = no
    valid users = $MACHINE_ACCT
EOF

# --- 6. sshd (PermitRootLogin prohibit-password + pubkey) ------------------------
log "configuring sshd (PermitRootLogin prohibit-password, PubkeyAuthentication yes)..."
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
  ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1 || true
fi
mkdir -p /etc/ssh/sshd_config.d
{
  echo 'PermitRootLogin prohibit-password'
  echo 'PubkeyAuthentication yes'
  echo 'PasswordAuthentication no'
  echo 'KbdInteractiveAuthentication no'
  echo 'UsePAM no'
} > /etc/ssh/sshd_config.d/00-scl-ad.conf
for opt in 'PermitRootLogin prohibit-password' 'PubkeyAuthentication yes' 'PasswordAuthentication no'; do
  k="${opt%% *}"
  if ! grep -qiE "^\s*${k}\b" /etc/ssh/sshd_config 2>/dev/null; then
    printf '\n%s\n' "$opt" >> /etc/ssh/sshd_config
  fi
done

# --- 7. Win-condition ------------------------------------------------------------
log "writing win-condition /root/proof.txt ..."
install -m 0600 /dev/stdin /root/proof.txt <<EOF
$FLAG
EOF

touch "$MARKER"
log "provisioning complete."
