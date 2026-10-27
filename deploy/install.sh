#!/usr/bin/env bash
# Installiert oder aktualisiert die Model Gallery in einem Debian-/Ubuntu-LXC
# (Proxmox CT oder Incus-Container). Als root im Projektverzeichnis ausführen:
#   ./deploy/install.sh
set -euo pipefail

APP_DIR=/opt/model-gallery
DATA_DIR=/var/lib/model-gallery
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Bitte als root ausführen." >&2; exit 1; fi

echo "==> Pakete installieren"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip rsync >/dev/null

echo "==> Systembenutzer"
id gallery &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin gallery

echo "==> Programmdateien nach $APP_DIR"
mkdir -p "$APP_DIR" "$DATA_DIR"
rsync -a --delete --exclude venv --exclude data --exclude '__pycache__' "$SRC_DIR/app" "$SRC_DIR/requirements.txt" "$APP_DIR/"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R gallery:gallery "$DATA_DIR"

if [[ ! -f /etc/model-gallery.env ]]; then
  cp "$SRC_DIR/deploy/model-gallery.env" /etc/model-gallery.env
  chmod 640 /etc/model-gallery.env
  chgrp gallery /etc/model-gallery.env
fi
cp "$SRC_DIR/deploy/model-gallery.service" /etc/systemd/system/model-gallery.service

echo "==> Dienst starten"
systemctl daemon-reload
systemctl enable --now model-gallery
systemctl restart model-gallery
sleep 2
systemctl --no-pager --lines=5 status model-gallery || true

if [[ -f "$DATA_DIR/initial-admin-password.txt" ]]; then
  echo
  echo "==> Erstes Admin-Konto:"
  cat "$DATA_DIR/initial-admin-password.txt"
  echo "(Datei nach dem ersten Login löschen: rm $DATA_DIR/initial-admin-password.txt)"
fi
IP=$(hostname -I | awk '{print $1}')
echo
echo "Fertig: http://$IP:$(. /etc/model-gallery.env; echo "$GALLERY_PORT")"
