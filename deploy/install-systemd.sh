#!/usr/bin/env bash
# Safe, opt-in installation of NEW units. No enabling, no deletion, no secrets.
set -euo pipefail
if (( EUID != 0 )); then
  echo "Run as root inside guardian LXC 111" >&2
  exit 1
fi
if [[ ! -f /opt/guardian/nodalia_guardian/operations.py ]]; then
  echo "Update /opt/guardian to the merged and tested version first" >&2
  exit 1
fi
src="/opt/guardian/deploy/systemd"
for unit in guardian-maintenance.service guardian-maintenance.timer guardian-fixes.service guardian-fixes.timer; do
  if [[ -e "/etc/systemd/system/$unit" ]]; then
    echo "Refusing to overwrite existing /etc/systemd/system/$unit" >&2
    exit 1
  fi
done
if [[ ! -e /etc/guardian/maintenance.env ]]; then
  umask 027
  printf '%s\n' 'GUARDIAN_MAINTAINER_IDS=' 'GUARDIAN_ENABLE_CHECK_ACTIONS=0' > /etc/guardian/maintenance.env
  chown root:guardian /etc/guardian/maintenance.env
  chmod 640 /etc/guardian/maintenance.env
fi
for unit in guardian-maintenance.service guardian-maintenance.timer guardian-fixes.service guardian-fixes.timer; do
  install -m 0644 "$src/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/guardian-maintenance.service /etc/systemd/system/guardian-fixes.service
echo "Installed but not enabled. Configure App permissions, operator IDs and Claude Console cap first."
echo "Then explicitly enable the desired timers with systemctl enable --now."
