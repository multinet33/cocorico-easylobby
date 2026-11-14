#!/bin/bash
# CocoBox nightly update (systemd timer, every 30 min from 03:00 to 05:30): never while someone is playing.
# Downloads the new image, and restarts the CocoBox only when it changed and nobody is in a match, a search or
# an end screen. Manual run: sudo cocobox-update  (COCOBOX_FORCE=1: even if someone is playing).
set -uo pipefail
DIR=/opt/cocobox
API="http://127.0.0.1:${WEB_PORT:-8080}/api/busy"
log() { echo "[cocobox-update] $*"; }
cd "$DIR" 2>/dev/null || { log "pas de $DIR : rien à faire"; exit 0; }

busy() {   # 0 = someone is playing (or the box does not answer during a match: we play safe only if it answers)
    local r; r="$(curl -fsS -m 5 "$API" 2>/dev/null)" || return 1
    case "$r" in *'"busy":true'*) return 0;; esac
    return 1
}
if [ "${COCOBOX_FORCE:-0}" != 1 ] && busy; then log "quelqu'un joue : mise à jour remise à plus tard"; exit 0; fi

IMG="$(docker compose config --images 2>/dev/null | head -1)"
[ -n "$IMG" ] || { log "image introuvable dans compose.yaml"; exit 1; }
docker compose pull -q || { log "téléchargement impossible (internet ?)"; exit 1; }
# compare with the image the CocoBox RUNS (a version downloaded on an earlier pass, while someone played, counts)
new="$(docker image inspect -f '{{.Id}}' "$IMG" 2>/dev/null || true)"
cid="$(docker compose ps -q | head -1)"
run="$( [ -n "$cid" ] && docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)"
if [ -n "$run" ] && [ "$run" = "$new" ]; then log "déjà à jour"; exit 0; fi
# the download can take minutes: check again just before restarting
if [ "${COCOBOX_FORCE:-0}" != 1 ] && busy; then log "nouvelle version prête, quelqu'un joue : redémarrage au prochain passage"; exit 0; fi
docker compose up -d && log "CocoBox mise à jour"
docker image prune -f >/dev/null 2>&1 || true
