#!/bin/bash
# =============================================================================
# rdp-provision.sh — idempotent first-boot provisioning for the windows-client host.
#
# Built from scl-rdp-host:0.1 (xrdp + pwsh). Marker-gated so re-invocation within one
# container lifetime is a no-op. Mirrors ad-provision.sh's shape (own the secrets +
# win-condition at runtime so the image stays reusable across hostnames/IPs).
#
# Brings up:
#   * the weak-cred RDP account (aadmin) — the takeover entry
#   * the planted root SSH key (id_root) in aadmin's profile — readable only as
#     aadmin, so you must BE aadmin (via the weak cred) to reach it
#   * sshd config (root key-only; aadmin password auth on for the cred-reuse pivot)
#   * the win-condition /root/proof.txt
# =============================================================================
set -Eeuo pipefail

# --- host constants (NOT shipped in any skill/goal) ------------------------------
WEAK_USER='aadmin'       # the low-priv RDP account (the foothold identity)
WEAK_PASS='Welcome1!'    # a globally-iconic weak default (guessed, no wordlist)

FLAG='FLAG{rdp-weak-cred-to-ssh-pivot-2c5e8a1f}'
MARKER=/srv/samlab/.provisioned

log(){ echo "[rdp-provision] $*"; }
err(){ echo "[rdp-provision][ERROR] $*" >&2; }
trap 'err "provisioning failed at line $LINENO (exit $?)"' ERR

# --- bootstrap dir layout --------------------------------------------------------
mkdir -p /srv/samlab /run/sshd /root/.ssh

if [ -f "$MARKER" ]; then
  log "already provisioned ($MARKER present) — skipping."
  exit 0
fi

# --- 1. The weak-credential RDP account ------------------------------------------
# xrdp's sesman authenticates against the local PAM/shadow database, so a normal local
# account with a password can RDP in. This is the misconfiguration: a user-facing RDP
# login guarded by a trivially-predictable password.
if id "$WEAK_USER" >/dev/null 2>&1; then
  log "account $WEAK_USER already exists."
else
  useradd -m -s /bin/bash "$WEAK_USER"
  log "created account $WEAK_USER."
fi
echo "${WEAK_USER}:${WEAK_PASS}" | chpasswd
# Make sure the account is unlocked + does not expire mid-engagement.
passwd -u "$WEAK_USER" >/dev/null 2>&1 || true
chage -E -1 "$WEAK_USER" >/dev/null 2>&1 || true
log "RDP account $WEAK_USER armed (weak, predictable password)."

# --- 2. Shell bridge: ed25519 keypair -> aadmin profile (aadmin-only) ----------
# Mirror of the AD host's [backups]/id_root trick. PUBLIC -> root authorized_keys;
# PRIVATE (id_root) -> aadmin's own profile, readable only as aadmin. You must first
# become aadmin (via the weak RDP cred) to read it, then ssh in as root with it.
log "building shell bridge: ed25519 keypair -> $WEAK_USER profile (aadmin-only)..."
KEYDIR="$(mktemp -d)"
ssh-keygen -q -t ed25519 -N '' -C "root@$(hostname 2>/dev/null || echo rdp-host)" -f "$KEYDIR/id_root" >/dev/null

install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
if ! grep -qF "$(cat "$KEYDIR/id_root.pub")" /root/.ssh/authorized_keys 2>/dev/null; then
  cat "$KEYDIR/id_root.pub" >> /root/.ssh/authorized_keys
fi

KEYDIR_DROP="/home/${WEAK_USER}/.local/share/backup-keys"
install -d -m 0755 "$KEYDIR_DROP"
install -m 0600 "$KEYDIR/id_root"     "$KEYDIR_DROP/id_root"
install -m 0644 "$KEYDIR/id_root.pub" "$KEYDIR_DROP/id_root.pub" 2>/dev/null || true
chown -R "${WEAK_USER}:${WEAK_USER}" "/home/${WEAK_USER}"
# A breadcrumb so a foothold session finds the key without guessing the exact path —
# realistic sloppy admin artifact, not a hint about the technique.
cat > "/home/${WEAK_USER}/.local/share/backup-keys/README.txt" <<EOF
Offline root backup key for $(hostname 2>/dev/null || echo this host).
Keep id_root private — it is authorized for root login.
EOF
chown "${WEAK_USER}:${WEAK_USER}" "$KEYDIR_DROP/README.txt"

shred -u "$KEYDIR/id_root" "$KEYDIR/id_root.pub" 2>/dev/null || rm -f "$KEYDIR/id_root" "$KEYDIR/id_root.pub"
rmdir "$KEYDIR" 2>/dev/null || true

# --- 3. sshd (root key-only; aadmin password auth on) ---------------------------
log "configuring sshd (root prohibit-password + pubkey; password auth on)..."
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
  ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1 || true
fi
mkdir -p /etc/ssh/sshd_config.d
{
  echo 'PermitRootLogin prohibit-password'   # root is SSH-key-only (the id_root payoff)
  echo 'PubkeyAuthentication yes'
  echo 'PasswordAuthentication yes'          # aadmin's weak cred reuses here (the pivot)
  echo 'KbdInteractiveAuthentication no'
  echo 'MaxStartups 1000:30:2000'            # don't throttle parallel SSH brute (the intended weak-cred attack)
  echo 'LoginGraceTime 120'                  # tolerate slow handshakes under load
} > /etc/ssh/sshd_config.d/00-scl-rdp.conf
for opt in 'PermitRootLogin prohibit-password' 'PubkeyAuthentication yes' 'PasswordAuthentication yes'; do
  k="${opt%% *}"
  if ! grep -qiE "^\s*${k}\b" /etc/ssh/sshd_config 2>/dev/null; then
    printf '\n%s\n' "$opt" >> /etc/ssh/sshd_config
  fi
done

# --- 4. Win-condition ------------------------------------------------------------
log "writing win-condition /root/proof.txt ..."
install -m 0600 /dev/stdin /root/proof.txt <<EOF
$FLAG
EOF

touch "$MARKER"
log "provisioning complete."
