"""Simulierter Zidoo für die Tests.

Bildet die Aufrufe nach, die Eclipse Demo benutzt, einschließlich des Wegs zu
einer SMB-Freigabe (getDevices → getHost → Freigaben → Ordner). Die Dateien im
Demo-Ordner spiegelt er aus einem lokalen Verzeichnis.

Aufruf: python mock_zidoo.py <port> <demo-verzeichnis>
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT, MEDIA = int(sys.argv[1]), sys.argv[2]
SMB_DEVICE = "/tmp/ramfs/smb"
SHARE = "/tmp/ramfs/mnt/192.168.1.88#SMB#Cinema"
FOLDER = SHARE + "/Demos/Preview"
DURATION = 120_000   # jede Datei dauert 2 Minuten
SPEED = 10           # Zeitraffer: 10 s Film pro echter Sekunde

lock = threading.Lock()
player = {"path": None, "pos": 0.0, "playing": False, "t": time.time()}
calls = []


def tick():
    now = time.time()
    if player["path"] and player["playing"]:
        player["pos"] += (now - player["t"]) * 1000 * SPEED
        if player["pos"] >= DURATION:
            player["path"] = None
    player["t"] = now


def local(path):
    """Zidoo-Pfad im Demo-Ordner → lokaler Pfad, sonst None."""
    if path == FOLDER:
        return MEDIA
    if path.startswith(FOLDER + "/"):
        return os.path.join(MEDIA, *path[len(FOLDER) + 1:].split("/"))
    return None


def listing(path):
    target = local(path)
    if target is None or not os.path.isdir(target):
        return {"status": 200, "isExists": False, "filelist": []}
    entries = []
    for name in sorted(os.listdir(target)):
        full = os.path.join(target, name)
        entries.append({"name": name, "path": path + "/" + name,
                        "type": 0 if os.path.isdir(full) else 2})
    return {"status": 200, "isExists": True, "perentPath": path.rsplit("/", 1)[0], "filelist": entries}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        with lock:
            tick()
            if p not in ("/ZidooVideoPlay/getPlayStatus", "/ZidooMusicControl/v2/getState", "/calls"):
                calls.append({"path": p, "query": q})
            out = {"status": 200}
            if p == "/ZidooControlCenter/getModel":
                out.update(model="Z3000 Pro (Test)")
            elif p == "/ZidooVideoPlay/getPlayStatus":
                if player["path"]:
                    out["video"] = {"status": 1 if player["playing"] else 2,
                                    "title": player["path"].rsplit("/", 1)[-1], "path": player["path"],
                                    "duration": DURATION, "currentPosition": int(player["pos"])}
                else:
                    out = {"status": 806}
            elif p == "/ZidooMusicControl/v2/getState":
                out = {"state": 0}
            elif p == "/ZidooFileControl/getDevices":
                out["devices"] = [{"name": "Intern", "path": "/storage/emulated/0", "type": 1000},
                                  {"name": "SMB", "path": SMB_DEVICE, "type": 1005}]
            elif p == "/ZidooFileControl/getHost":
                out["hosts"] = ([{"ip": "192.168.1.88", "type": 1005, "name": "smb://UNAS"}]
                                if q.get("path") == SMB_DEVICE else [])
            elif p == "/ZidooFileControl/getFileList":
                if q.get("path") == "192.168.1.88":
                    out["filelist"] = [{"name": "Cinema", "path": SHARE, "type": 0},
                                       {"name": "Backups", "path": SHARE.replace("Cinema", "Backups"), "type": 0}]
                else:
                    out = listing(q.get("path", ""))
            elif p == "/ZidooFileControl/openFile":
                target = local(q.get("path", ""))
                if target and os.path.isfile(target):
                    player.update(path=q["path"], pos=0.0, playing=True)
                else:
                    out = {"status": 404}
            elif p == "/ZidooVideoPlay/seekTo":
                player["pos"] = float(q["positon"])
            elif p == "/ZidooControlCenter/RemoteControl/sendkey":
                if q["key"] == "Key.MediaStop":
                    player["path"] = None
                elif q["key"] == "Key.MediaPlay.Pause":
                    player["playing"] = not player["playing"]
            elif p == "/calls":
                out = {"calls": list(calls)}
            elif p == "/remote-open":           # Test: jemand startet etwas mit der Fernbedienung
                player.update(path=q["path"], pos=0.0, playing=True)
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
