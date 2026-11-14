#!/bin/bash
# Cocorico EasyLobby — CocoBox installer (Raspberry Pi 4/5, Odroid, any 64-bit Linux).
#   curl -fsSL https://raw.githubusercontent.com/multinet33/cocorico-easylobby/main/box/install.sh | sudo bash
# What it does (again safely if run twice):
#   1. installs Docker if missing, and avahi (the box answers at http://cocobox.local)
#   2. writes /opt/cocobox/compose.yaml and starts the CocoBox (public image from ghcr.io)
#   3. nightly automatic updates (03:30 to 05:30, never while someone is playing)
#   4. port 80 -> 8080, and on a Raspberry Pi with Wi-Fi: the setup Wi-Fi « CocoBox-XXXX » when it has no network
# Options (environment): COCOBOX_HOSTNAME=0 keeps the machine's name; COCOBOX_NO_UPDATES=1 no nightly updates.
set -euo pipefail

REPO_RAW="${COCOBOX_RAW:-https://raw.githubusercontent.com/multinet33/cocorico-easylobby/main/box}"
DIR=/opt/cocobox
say() { printf '\n\033[1;35m🐓 %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Lance l'installation avec sudo (… | sudo bash)."
[ "$(uname -s)" = Linux ] || die "La CocoBox s'installe sur Linux (Raspberry Pi OS, Debian, Ubuntu…)."
case "$(uname -m)" in aarch64|arm64|x86_64|amd64) ;; *) die "Système 64 bits nécessaire (Raspberry Pi OS 64-bit). Trouvé : $(uname -m)";; esac

# files: from this folder when run from a copy (SD image, git clone), else downloaded
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /nonexistent)"
fetch() {  # $1 = file name, $2 = destination
    if [ -f "$SRC/$1" ]; then install -m "${3:-644}" "$SRC/$1" "$2"
    else curl -fsSL "$REPO_RAW/$1" -o "$2" && chmod "${3:-644}" "$2"; fi
}

say "1/4 · Docker et nom réseau"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "« docker compose » manque (paquet docker-compose-plugin)."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -q >/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q avahi-daemon qrencode python3 >/dev/null || true
fi
CUR="$(hostname)"
# network name: cocobox-ab12 (end of the MAC address, like the setup Wi-Fi « CocoBox-AB12 »), then the name the
# player gives it (Réglages): cocobox-setup renames the machine. Only for a fresh machine's default name.
if [ "${COCOBOX_HOSTNAME:-1}" = 1 ]; then
    case "$CUR" in raspberrypi|odroid|localhost|debian|ubuntu|cocobox|cocobox-*|"")
        mkdir -p /etc/cocobox && touch /etc/cocobox/hostname-managed ;;
    esac
fi

say "2/4 · CocoBox"
mkdir -p "$DIR/data" /var/lib/cocorico
fetch compose.yaml "$DIR/compose.yaml"
cd "$DIR"
docker compose pull -q
docker compose up -d

say "3/4 · Mises à jour automatiques (la nuit, jamais pendant une partie)"
fetch cocobox-update.sh /usr/local/bin/cocobox-update 755
cat > /etc/systemd/system/cocobox-update.service <<'EOF'
[Unit]
Description=CocoBox: nightly update (never during a match)
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/cocobox-update
EOF
cat > /etc/systemd/system/cocobox-update.timer <<'EOF'
[Unit]
Description=CocoBox: nightly update window
[Timer]
OnCalendar=*-*-* 03,04,05:00/30
RandomizedDelaySec=10min
Persistent=false
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
if [ "${COCOBOX_NO_UPDATES:-0}" = 1 ]; then systemctl disable --now cocobox-update.timer 2>/dev/null || true
else systemctl enable --now cocobox-update.timer; fi

say "4/4 · Adresse simple et Wi-Fi d'installation"
fetch cocobox-setup.py /usr/local/bin/cocobox-setup 755
cat > /etc/systemd/system/cocobox-setup.service <<'EOF'
[Unit]
Description=CocoBox: http://cocobox.local (port 80) and setup Wi-Fi when there is no network
After=NetworkManager.service network.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/cocobox-setup
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now cocobox-setup.service

if [ -f /etc/cocobox/hostname-managed ]; then          # cocobox-setup gives the name cocobox-ab12 within seconds
    for _ in $(seq 1 20); do case "$(hostname)" in cocobox-*) break;; esac; sleep 1; done
fi
NAME="$(hostname)"
IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
URL="http://$NAME.local"
say "C'est prêt !"
echo "  Ouvre sur ton téléphone ou ton PC :  $URL   (ou http://${IP:-adresse-de-la-box}:8080)"
echo "  Tu peux la renommer dans Réglages › Compte : son adresse suit le nom."
echo "  La mise en service te guide pour faire passer ta console par la CocoBox."
command -v qrencode >/dev/null 2>&1 && qrencode -t ansiutf8 -m 2 "$URL" || true
