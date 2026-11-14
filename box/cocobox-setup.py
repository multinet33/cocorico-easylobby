#!/usr/bin/env python3
"""CocoBox (host side, systemd): http://cocobox.local on port 80, and the setup Wi-Fi « CocoBox-XXXX ».

- With a network (cable or Wi-Fi): port 80 only redirects to the CocoBox (port 8080), so http://cocobox.local works.
- Without any network for 60 s, on a machine with Wi-Fi and NetworkManager (Raspberry Pi OS): opens an OPEN Wi-Fi
  « CocoBox-XXXX » (like a Ring doorbell). The phone that joins it gets the setup page by itself (captive portal):
  it picks the home Wi-Fi, types its password, the CocoBox joins it and the setup Wi-Fi disappears.
  A cable plugged in meanwhile is enough too: the setup Wi-Fi goes away as soon as there is a network.
Standard library only (python3 of Raspberry Pi OS / Debian).
"""
import html
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

WEB = int(os.environ.get("WEB_PORT", "8080"))
CON = "cocobox-setup"
AP_IP = "10.42.0.1"                                   # NetworkManager "shared" address
DNS_CONF = "/etc/NetworkManager/dnsmasq-shared.d/cocobox-captive.conf"
STATE = "/run/cocobox-setup.json"
GRACE = int(os.environ.get("COCOBOX_SETUP_GRACE", "60"))
COUNTRY = os.environ.get("COCOBOX_COUNTRY", "FR")            # Wi-Fi regulatory country (setup Wi-Fi)
S = {"mode": "normal", "ssid": "", "scan": [], "error": "", "busy": False}
LOCK = threading.Lock()


def sh(*a, timeout=40):
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                            # missing tool, timeout
        return 1, str(e)


def wifi_iface():
    try:
        return next((n for n in sorted(os.listdir("/sys/class/net")) if os.path.isdir(f"/sys/class/net/{n}/wireless")), "")
    except OSError:
        return ""


def setup_ssid(iface):
    try:
        mac = open(f"/sys/class/net/{iface}/address").read().strip().replace(":", "").upper()
    except OSError:
        mac = "0000"
    return "CocoBox-" + mac[-4:]


def online():
    """A default route that is not our own setup Wi-Fi."""
    code, out = sh("ip", "-4", "route", "show", "default", timeout=5)
    return code == 0 and bool(out.strip())


def save():
    try:
        with open(STATE, "w") as f:
            json.dump({k: S[k] for k in ("mode", "ssid", "error")}, f)
    except OSError:
        pass


def scan(iface):
    code, out = sh("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "ifname", iface, "--rescan", "yes")
    seen = {}
    for line in out.splitlines() if code == 0 else []:
        parts = line.replace("\\:", "\0").split(":")
        if len(parts) < 3:
            continue
        name, sig, sec = (p.replace("\0", ":") for p in parts[:3])
        if name and not name.startswith("CocoBox-") and (name not in seen or int(sig or 0) > seen[name][0]):
            seen[name] = (int(sig or 0), sec)
    return sorted(([n, s, sec] for n, (s, sec) in seen.items()), key=lambda x: -x[1])[:25]


def ap_up(iface):
    S["scan"] = scan(iface) or S["scan"]
    os.makedirs(os.path.dirname(DNS_CONF), exist_ok=True)
    with open(DNS_CONF, "w") as f:                    # every name -> the CocoBox: the phone opens the setup page
        f.write(f"address=/#/{AP_IP}\n")
    sh("rfkill", "unblock", "wifi", timeout=5)          # Raspberry Pi OS blocks Wi-Fi until a country is set
    sh("iw", "reg", "set", COUNTRY, timeout=5)
    sh("nmcli", "con", "delete", CON, timeout=10)
    sh("nmcli", "con", "add", "type", "wifi", "ifname", iface, "con-name", CON, "autoconnect", "no", "ssid", S["ssid"],
       "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg", "ipv4.method", "shared", "ipv6.method", "disabled")
    code, out = sh("nmcli", "con", "up", CON)
    S["mode"] = "setup" if code == 0 else "normal"
    print(f"[cocobox-setup] Wi-Fi d'installation {S['ssid']} : {'ouvert' if code == 0 else 'échec ' + out.strip()[:120]}", flush=True)
    save()


def ap_down():
    sh("nmcli", "con", "down", CON, timeout=15)
    sh("nmcli", "con", "delete", CON, timeout=10)
    try:
        os.remove(DNS_CONF)
    except OSError:
        pass
    S["mode"] = "normal"
    save()


def join(iface, ssid, pw):
    """The phone sent the home Wi-Fi: leave the setup Wi-Fi, join it; back to the setup Wi-Fi if it fails."""
    with LOCK:
        S["busy"] = True
        time.sleep(3)                                  # the "connecting" page reaches the phone first
        ap_down()
        args = ["nmcli", "dev", "wifi", "connect", ssid, "ifname", iface] + (["password", pw] if pw else [])
        code, out = sh(*args, timeout=60)
        ok = False
        for _ in range(20):
            if online():
                ok = True
                break
            time.sleep(1.5)
        if ok:
            S["error"] = ""
            print(f"[cocobox-setup] connectée au Wi-Fi « {ssid} »", flush=True)
        else:
            S["error"] = f"Connexion à « {ssid} » impossible : vérifie le mot de passe." if code == 0 or "secret" in out.lower() or "password" in out.lower() \
                else f"Connexion à « {ssid} » impossible ({out.strip()[:80]})."
            sh("nmcli", "con", "delete", ssid, timeout=10)
            ap_up(iface)
        S["busy"] = False
        save()


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CocoBox · Installation</title><style>
body{margin:0;background:#120d24;color:#ece9f7;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:460px;margin:0 auto;padding:28px 16px 40px;display:flex;flex-direction:column;gap:16px}
h1{font-size:26px;margin:0}.c{background:#1b1533;border:1px solid #2c2550;border-radius:16px;padding:18px;display:flex;flex-direction:column;gap:12px}
label{font-size:14px;color:#c9c4e3}select,input{width:100%;box-sizing:border-box;min-height:48px;border-radius:12px;border:1px solid #3b3470;background:#120d24;color:#ece9f7;font-size:17px;padding:10px 12px}
button{min-height:52px;border:0;border-radius:14px;background:#9b84ff;color:#120d24;font-size:17px;font-weight:800}
.m{color:#a59fc4;font-size:14px}.e{color:#ff8fa3;font-weight:600}.k{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:#9b84ff;font-weight:700}
</style></head><body><main>@BODY@</main></body></html>"""


def portal():
    nets = "".join(f'<option value="{html.escape(n)}">{html.escape(n)}{" 🔒" if sec and sec != "--" else ""} · {s} %</option>' for n, s, sec in S["scan"])
    err = f'<div class="e">{html.escape(S["error"])}</div>' if S["error"] else ""
    return PAGE.replace("@BODY@", f"""<span class="k">🐓 CocoBox</span><h1>Connecte la CocoBox à ton Wi-Fi</h1>
<span class="m">Le plus simple et le plus rapide pour jouer : un câble entre la CocoBox et ta box internet. Branche-le et cette page n'est plus utile.</span>
{err}<form class="c" method="post" action="/connect">
<label for="s">Ton Wi-Fi</label><select id="s" name="ssid">{nets}<option value="">Autre (nom caché)…</option></select>
<input name="other" placeholder="Nom du Wi-Fi (s'il n'est pas dans la liste)" aria-label="Nom du Wi-Fi">
<label for="p">Mot de passe du Wi-Fi</label><input id="p" name="pw" type="password" autocomplete="off">
<button type="submit">Connecter la CocoBox</button></form>
<span class="m">CocoBox · {html.escape(S['ssid'])}</span>""")


def connecting(ssid):
    return PAGE.replace("@BODY@", f"""<span class="k">🐓 CocoBox</span><h1>Connexion à « {html.escape(ssid)} »…</h1>
<div class="c"><b>1.</b> Ton téléphone va perdre ce Wi-Fi « {html.escape(S['ssid'])} » : reconnecte-le à ton Wi-Fi habituel.
<b>2.</b> Ouvre <b>http://cocobox.local</b> : la mise en service te guide pour la console.
<span class="m">Si « {html.escape(S['ssid'])} » réapparaît d'ici une minute, le mot de passe était faux : reconnecte-toi et recommence.</span></div>""")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body="", headers=None):
        b = body.encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if b:
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if S["mode"] == "setup":
            if self.path == "/" or self.path.startswith("/?"):
                return self.send(200, portal())
            return self.send(302, headers={"Location": f"http://{AP_IP}/"})      # any probe: open the setup page
        host = (self.headers.get("Host") or "cocobox.local").split(":")[0]
        self.send(302, headers={"Location": f"http://{host}:{WEB}{self.path}"})

    def do_POST(self):
        if S["mode"] != "setup" or S["busy"]:
            return self.send(302, headers={"Location": "/"})
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        f = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        ssid = ((f.get("other") or [""])[0].strip() or (f.get("ssid") or [""])[0]).strip()
        if not ssid:
            S["error"] = "Choisis ton Wi-Fi dans la liste."
            return self.send(302, headers={"Location": "/"})
        self.send(200, connecting(ssid))
        threading.Thread(target=join, args=(IFACE, ssid, (f.get("pw") or [""])[0]), daemon=True).start()


def watch():
    """No network for GRACE seconds -> setup Wi-Fi; a network again (cable, Wi-Fi) -> setup Wi-Fi off."""
    off_since = None
    while True:
        time.sleep(5)
        if S["busy"] or not IFACE:
            continue
        net = online()
        if S["mode"] == "setup":
            if net:
                print("[cocobox-setup] réseau trouvé : Wi-Fi d'installation fermé", flush=True)
                with LOCK:
                    ap_down()
            continue
        off_since = None if net else (off_since or time.time())
        if off_since and time.time() - off_since >= GRACE and subprocess.run(["which", "nmcli"], capture_output=True).returncode == 0:
            with LOCK:
                ap_up(IFACE)
            off_since = None


IFACE = wifi_iface()
S["ssid"] = setup_ssid(IFACE) if IFACE else ""
if __name__ == "__main__":
    if S["mode"] == "normal" and IFACE:
        sh("nmcli", "con", "delete", CON, timeout=10)          # left over from a power cut during setup
    save()
    threading.Thread(target=watch, daemon=True).start()
    try:
        ThreadingHTTPServer(("0.0.0.0", 80), H).serve_forever()
    except OSError as e:                                         # port 80 already used (NAS, web server): the
        print(f"[cocobox-setup] port 80 pris ({e}) : pas d'adresse sans « :8080 »", flush=True)   # rest still works
        while True:
            time.sleep(3600)
