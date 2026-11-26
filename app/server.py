#!/usr/bin/env python3
"""Eclipse Demo: Demo-Szenen und Musik per Kachel am Zidoo abspielen.

Die Auswahl ergibt sich aus einem Ordner (auf dem Heimserver als UNAS-Freigabe
eingehängt). Jede Datei dort ist eine Demo; Unterordner werden zu Kategorien.
Der Zidoo spielt die Dateien direkt von derselben Freigabe; dieser Dienst sagt
ihm nur, welche Datei er öffnen soll, springt zur Startzeit und hält am Ende an.

Nur Python-Standardbibliothek. Konfiguration über Umgebungsvariablen (README).
"""
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import urlopen

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DATA = os.environ.get("EDEMO_DATA", "/data")
MEDIA = os.environ.get("EDEMO_MEDIA", "/media")
REQUIRE_MOUNT = os.environ.get("EDEMO_REQUIRE_MOUNT", "") == "1"
PORT = int(os.environ.get("EDEMO_PORT", "8080"))
ZIDOO_HOST = os.environ.get("ZIDOO_HOST", "")
ZIDOO_PORT = int(os.environ.get("ZIDOO_PORT", "9529"))
UNAS_HOSTS = [h.strip().lower() for h in
              os.environ.get("UNAS_HOSTS", "192.168.1.88,unas.home.arpa,unas").split(",") if h.strip()]
UNAS_SHARE = os.environ.get("UNAS_SHARE", "Cinema")
UNAS_SUBPATH = os.environ.get("UNAS_SUBPATH", "Demos/Preview").strip("/")
FOLDER_LABEL = os.environ.get("EDEMO_FOLDER_LABEL", r"\\unas.home.arpa\Cinema\Demos\Preview")
STORM_HOST = os.environ.get("STORM_HOST", "")
STORM_MAX_VOLUME = float(os.environ.get("STORM_MAX_VOLUME", "-15"))
HA_URL = os.environ.get("HA_URL", "https://homeassistant.home.arpa")
LIBRARY_FILE = os.path.join(DATA, "library.json")

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".mov", ".ts", ".m2ts", ".mts", ".avi", ".webm", ".iso", ".wmv"}
AUDIO_EXT = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".dsf", ".dff", ".ogg", ".opus",
             ".wv", ".ape", ".aiff", ".aif", ".mka", ".ac3", ".eac3", ".dts", ".thd", ".mlp"}


class ZidooError(Exception):
    pass


def log(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), text, flush=True)


def decode_zidoo_path(value):
    """FileControl liefert absolute Pfade teils roh, teils mitsamt Schrägstrichen URL-kodiert."""
    path = value or ""
    if path.lower().startswith(("%2f", "%5c", "smb%3a%2f%2f", "nfs%3a%2f%2f", "file%3a%2f%2f")):
        return unquote(path)
    return path


# ---------------------------------------------------------------- Speicher

def load_library():
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except ValueError as e:
        sys.exit(f"{LIBRARY_FILE} ist kein gültiges JSON: {e}")
    return {"order": list(data.get("order") or []), "meta": dict(data.get("meta") or {}),
            "zidooFolder": decode_zidoo_path(data.get("zidooFolder"))}


def save_library():
    tmp = LIBRARY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIBRARY_FILE)


library = load_library()
state_lock = threading.RLock()
command_lock = threading.Lock()
status = {"online": False, "np": None, "model": ""}
files = []            # gescannte Dateien: {"id", "kind", "folder"}
media_ok = True
active = None         # laufende, von hier gestartete Demo
generation = 0        # steigt mit jedem Start/Stopp; entwertet ältere Aktionen
notice = {"id": 0, "text": ""}
version = 1           # steigt, wenn sich Dateien oder Einstellungen ändern
folder_search = {"running": False, "last": 0.0, "result": ""}


def bump():
    global version
    version += 1


def set_notice(text):
    with state_lock:
        notice["id"] += 1
        notice["text"] = text
    log(text)


# ------------------------------------------------------------- Dateiordner

def scan_media():
    found = []
    for root, dirs, names in os.walk(MEDIA):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "@", "#")))
        for name in names:
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in VIDEO_EXT and ext not in AUDIO_EXT:
                continue
            rel = os.path.relpath(os.path.join(root, name), MEDIA).replace(os.sep, "/")
            folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
            found.append({"id": rel, "kind": "music" if ext in AUDIO_EXT else "video", "folder": folder})
    found.sort(key=lambda f: f["id"].lower())
    return found


def nice_title(rel):
    base = os.path.splitext(rel.rsplit("/", 1)[-1])[0]
    return " ".join(base.replace("_", " ").split())


def demo_view(f):
    m = library["meta"].get(f["id"], {})
    default_cat = "Musik" if f["kind"] == "music" else "Film"
    return {
        "id": f["id"],
        "title": m.get("title") or nice_title(f["id"]),
        "subtitle": m.get("subtitle") or "",
        "category": m.get("category") or default_cat,
        "folder": f["folder"],
        "kind": f["kind"],
        "start": float(m.get("start") or 0),
        "end": m.get("end"),
        "volume": m.get("volume"),
    }


def ordered_demos():
    rank = {rel: i for i, rel in enumerate(library["order"])}
    known = [f for f in files if f["id"] in rank]
    known.sort(key=lambda f: rank[f["id"]])
    rest = [f for f in files if f["id"] not in rank]
    ordered = known + rest
    # Erst die Dateien eines Ordners, dann seine Unterordner alphabetisch (rekursiv);
    # innerhalb eines Ordners gilt die eingestellte Reihenfolge (sort ist stabil).
    ordered.sort(key=lambda f: tuple(c.casefold() for c in f["folder"].split("/") if c))
    return [demo_view(f) for f in ordered]


def media_loop():
    global files, media_ok
    signature = None
    while True:
        try:
            # Ist die Freigabe nicht eingehängt, wäre der Ordner nur leer; das soll man sehen.
            ok = os.path.ismount(MEDIA) if REQUIRE_MOUNT else os.path.isdir(MEDIA)
            found = scan_media() if ok else []
        except OSError as e:
            found, ok = [], False
            log(f"Demo-Ordner nicht lesbar: {e}")
        sig = json.dumps(found)
        with state_lock:
            if sig != signature or ok != media_ok:
                signature, files, media_ok = sig, found, ok
                bump()
        time.sleep(10)


# ------------------------------------------------------------------- Zidoo

def zidoo_get(path, timeout=2.5):
    if not ZIDOO_HOST:
        raise ZidooError("Für den Zidoo ist keine Adresse eingerichtet (ZIDOO_HOST).")
    try:
        with urlopen(f"http://{ZIDOO_HOST}:{ZIDOO_PORT}/{path}", timeout=timeout) as r:
            raw = r.read()
    except (OSError, URLError):
        raise ZidooError("Der Zidoo antwortet nicht. Ist das Kino eingeschaltet?")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def file_devices():
    devices = zidoo_get("ZidooFileControl/getDevices", timeout=8).get("devices") or []
    for device in devices:
        device["path"] = decode_zidoo_path(device.get("path"))
    return devices


def file_hosts(path, ftype):
    hosts = zidoo_get(f"ZidooFileControl/getHost?path={quote(path, safe='/')}&type={int(ftype)}",
                      timeout=10).get("hosts") or []
    for host in hosts:
        host["ip"] = decode_zidoo_path(host.get("ip"))
    return hosts


def file_list(path, ftype=0, timeout=10):
    res = zidoo_get(f"ZidooFileControl/getFileList?path={quote(str(path), safe='/')}&type={int(ftype)}",
                    timeout)
    for entry in res.get("filelist") or []:
        entry["path"] = decode_zidoo_path(entry.get("path"))
    return res


def playing_path(value):
    if value and value.lower().startswith(("file://", "smb://", "nfs://")):
        return unquote(value)
    return decode_zidoo_path(value)


def now_playing():
    """Was läuft gerade? None = nichts. Wirft ZidooError, wenn der Zidoo aus ist."""
    v = zidoo_get("ZidooVideoPlay/getPlayStatus")
    if v.get("status") == 804:          # Zidoo wechselt gerade die Datei
        return status["np"]
    video = v.get("video")
    if v.get("status") == 200 and isinstance(video, dict):
        return {"kind": "video", "state": "playing" if video.get("status") == 1 else "paused",
                "title": video.get("title") or "", "path": playing_path(video.get("path")),
                "position": int(video.get("currentPosition") or 0),
                "duration": int(video.get("duration") or 0)}
    try:
        m = zidoo_get("ZidooMusicControl/v2/getState")
    except ZidooError:
        return None
    music = m.get("playingMusic")
    if m.get("state") and isinstance(music, dict):
        return {"kind": "music", "state": "playing" if m.get("state") == 3 else "paused",
                "title": music.get("title") or "", "path": playing_path(music.get("uri")),
                "position": int(m.get("position") or 0), "duration": int(m.get("duration") or 0)}
    return None


def zidoo_key(key):
    zidoo_get("ZidooControlCenter/RemoteControl/sendkey?key=" + quote(key))


def zidoo_seek(kind, ms):
    if kind == "music":
        zidoo_get(f"ZidooMusicControl/seekTo?time={int(ms)}")
    else:
        # "positon" ist kein Tippfehler von uns: so heißt der Parameter beim Zidoo.
        zidoo_get(f"ZidooVideoPlay/seekTo?positon={int(ms)}")


def norm(path):
    return (path or "").replace("\\", "/").rstrip("/").lower()


def same_file(playing, wanted):
    a, b = norm(playing), norm(wanted)
    if not a or not b:
        return False
    return a == b or a.endswith("/" + b.rsplit("/", 1)[-1]) or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def zidoo_path(rel):
    return library["zidooFolder"].rstrip("/") + "/" + rel


# ----------------------------------------------- Demo-Ordner auf dem Zidoo

def folder_matches(path):
    """Ist `path` auf dem Zidoo derselbe Ordner wie unser Demo-Ordner?"""
    try:
        res = file_list(path)
    except ZidooError:
        return False
    if res.get("status") != 200 or res.get("isExists") is False:
        return False
    theirs = {(e.get("name") or "").lower() for e in res.get("filelist") or []}
    with state_lock:
        ours = {f["id"].split("/", 1)[0].lower() for f in files}
    if not ours:
        return "filelist" in res
    return len(ours & theirs) >= max(1, min(3, len(ours)) // 2 + 1) or ours <= theirs


def is_unas(text):
    text = (text or "").lower()
    return any(h in text for h in UNAS_HOSTS)


def find_folder():
    sub = UNAS_SUBPATH
    candidates = []
    for dev in file_devices():
        path, dtype = dev["path"], dev.get("type")
        if not path:
            continue
        if dtype in (1004, 1005) or is_unas(path) or UNAS_SHARE.lower() in path.lower():
            candidates += [f"{path}/{UNAS_SHARE}/{sub}", f"{path}/{sub}"]
        if dtype in (1004, 1005):
            for host in file_hosts(path, dtype):
                host_path = host["ip"]
                if not (is_unas(host_path) or is_unas(host.get("name"))):
                    continue
                shares = file_list(host_path, host.get("type") or dtype).get("filelist") or []
                for share in shares:
                    name = (share.get("name") or "").rsplit("/", 1)[-1].lower()
                    spath = share["path"]
                    if spath and (name == UNAS_SHARE.lower() or norm(spath).endswith(UNAS_SHARE.lower())):
                        candidates.insert(0, f"{spath}/{sub}")
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            if folder_matches(c):
                return c
    return ""


def search_folder(reason):
    with state_lock:
        if folder_search["running"]:
            return
        folder_search["running"] = True
    try:
        found = find_folder()
        with state_lock:
            folder_search["result"] = "found" if found else "missing"
            if found and found != library["zidooFolder"]:
                library["zidooFolder"] = found
                save_library()
                bump()
                log(f"Demo-Ordner auf dem Zidoo: {found}")
        if not found and reason == "manual":
            set_notice("Eclipse Demo hat den Demo-Ordner auf dem Zidoo nicht gefunden.")
    except ZidooError as e:
        if reason == "manual":
            set_notice(str(e))
    finally:
        with state_lock:
            folder_search["running"] = False
            folder_search["last"] = time.time()


def browse(path, via, ftype):
    if not path:
        return {"path": "", "entries": [
            {"name": d.get("name") or d["path"], "path": d["path"], "dir": True,
             "via": "host" if d.get("type") in (1004, 1005) else "list", "type": d.get("type") or 0}
            for d in file_devices() if d["path"]]}
    if via == "host":
        return {"path": path, "entries": [
            {"name": h.get("name") or h["ip"], "path": h["ip"], "dir": True,
             "via": "share", "type": h.get("type") or ftype}
            for h in file_hosts(path, ftype) if h["ip"]]}
    res = file_list(path, ftype if via == "share" else 0)
    entries = [{"name": f.get("name"), "path": f["path"], "dir": True, "via": "list", "type": 0}
               for f in res.get("filelist") or []
               if f.get("type") == 0 and f.get("path") and not (f.get("isBDMV") or f.get("isBluray"))]
    entries.sort(key=lambda e: (e["name"] or "").lower())
    return {"path": path, "entries": entries}


# -------------------------------------------------------------- StormAudio

def storm_volume(db):
    db = min(float(db), STORM_MAX_VOLUME)
    with socket.create_connection((STORM_HOST, 23), 2) as s:
        s.sendall(f"ssp.vol.[{db:.1f}]\n".encode("ascii"))
        s.settimeout(0.3)
        try:
            s.recv(1024)
        except OSError:
            pass


# -------------------------------------------------------------- Wiedergabe

def snapshot(demo):
    if not library["zidooFolder"]:
        raise ValueError("Der Demo-Ordner auf dem Zidoo ist noch nicht gefunden. "
                         "Schalte das Kino ein und warte einen Moment.")
    return {**demo, "path": zidoo_path(demo["id"])}


def start_demo(demo, queue=None, index=0):
    global active, generation
    with command_lock:
        with state_lock:
            active = None
            generation += 1
        if demo.get("volume") is not None and STORM_HOST:
            try:
                storm_volume(demo["volume"])
            except OSError as e:
                set_notice(f"StormAudio nicht erreichbar, Lautstärke unverändert ({e}).")
        res = zidoo_get("ZidooFileControl/openFile?path=" + quote(demo["path"], safe="/")
                        + "&videoplaymode=0", timeout=8)
        if res.get("status") != 200:
            raise ZidooError(f"Der Zidoo konnte „{demo['title']}“ nicht öffnen "
                             f"(Antwort: {res.get('status', 'keine')}).")
        with state_lock:
            active = {"demo": demo, "phase": "starting", "since": time.time(), "seeks": 0,
                      "seekAt": 0.0, "lastPos": 0, "lastDur": 0, "idle": 0, "offline": 0,
                      "queue": queue or [], "index": index}


def stop_playback():
    global active, generation
    with state_lock:
        active = None
        generation += 1
    zidoo_key("Key.MediaStop")


def _advance(a, stop):
    """Demo ist zu Ende: nächste aus der Reihe starten oder anhalten."""
    global active
    active = None
    q, i = a["queue"], a["index"]
    if q and i + 1 < len(q):
        return ("play", generation, q[i + 1], q, i + 1)
    return ("stop", generation) if stop else None


def engine_step():
    global active
    try:
        np, online = now_playing(), True
    except ZidooError:
        np, online = None, False
    with state_lock:
        was_online = status["online"]
        status["online"], status["np"] = online, np
        if online and not was_online:
            bump()
        a = active
        if not a:
            return None
        now = time.time()
        if not online:
            a["offline"] += 1
            if a["offline"] > 8:
                active = None
                set_notice("Der Zidoo ist nicht mehr erreichbar.")
            return None
        a["offline"] = 0
        demo = a["demo"]
        start_ms = int(float(demo.get("start") or 0) * 1000)
        end_ms = int(float(demo["end"]) * 1000) if demo.get("end") else 0
        mine = np is not None and same_file(np["path"], demo["path"])

        def seek():
            a["seeks"] += 1
            a["seekAt"] = now
            return ("seek", generation, np["kind"], start_ms)

        if a["phase"] == "starting":
            if mine and np["duration"] > 0:
                if start_ms > 0:
                    a["phase"] = "seeking"
                    return seek()
                a["phase"] = "running"
            elif now - a["since"] > 30:
                active = None
                set_notice(f"„{demo['title']}“ ist nicht gestartet. Zeigt der Zidoo eine Rückfrage?")
            return None

        if a["phase"] == "seeking":
            if mine and np["position"] >= start_ms - 3000:
                a["phase"] = "running"
            elif now - a["seekAt"] > 3:
                if a["seeks"] >= 4:
                    a["phase"] = "running"
                    set_notice(f"„{demo['title']}“: Sprung zur Startzeit hat nicht geklappt.")
                else:
                    return seek()
            return None

        if mine:
            a["idle"] = 0
            # Zidoo hat die Datei nach unserem Sprung noch einmal von vorn begonnen
            if (start_ms and np["position"] < start_ms - 5000
                    and now - a["since"] < 40 and a["seeks"] < 4):
                return seek()
            a["lastPos"], a["lastDur"] = np["position"], np["duration"]
            if end_ms and np["position"] >= end_ms:
                return _advance(a, stop=True)
            return None
        a["idle"] += 1
        if np is None and a["idle"] < 3:
            return None                  # kurze Lücke, noch abwarten
        if a["lastDur"] > 0 and a["lastPos"] >= a["lastDur"] - 6000:
            # Datei lief bis zum Schluss; spielt der Zidoo von selbst weiter, anhalten
            return _advance(a, stop=np is not None)
        active = None                    # jemand hat mit der Fernbedienung übernommen
        return None


def run_action(action):
    kind, gen = action[0], action[1]
    with state_lock:
        if gen != generation:
            return
    try:
        if kind == "seek":
            zidoo_seek(action[2], action[3])
        elif kind == "stop":
            zidoo_key("Key.MediaStop")
        elif kind == "play":
            start_demo(action[2], action[3], action[4])
    except ZidooError as e:
        set_notice(str(e))


def engine_loop():
    while True:
        try:
            action = engine_step()
            if action:
                run_action(action)
        except Exception as e:  # der Taktgeber darf nie sterben
            log(f"Fehler im Taktgeber: {e!r}")
        with state_lock:
            busy = active is not None
        time.sleep(0.5 if busy else 1.5)


def folder_loop():
    """Sucht den Demo-Ordner auf dem Zidoo, sobald der erreichbar ist und ihn noch nicht kennt."""
    while True:
        time.sleep(5)
        with state_lock:
            due = (status["online"] and not library["zidooFolder"]
                   and time.time() - folder_search["last"] > 60)
        if due:
            search_folder("auto")
        if status["online"] and not status["model"]:
            try:
                status["model"] = zidoo_get("ZidooControlCenter/getModel").get("model") or ""
                bump()
            except ZidooError:
                pass


# ------------------------------------------------------------------ API

def find_demo(demo_id):
    for d in ordered_demos():
        if d["id"] == demo_id:
            return d
    raise ValueError("Diese Datei liegt nicht mehr im Demo-Ordner.")


def api_save_demo(body):
    def number(key):
        v = body.get(key)
        return None if v in (None, "") else float(v)

    demo = find_demo(str(body.get("id") or ""))
    title = str(body.get("title") or "").strip()
    start, end, volume = number("start") or 0.0, number("end"), number("volume")
    if start < 0:
        raise ValueError("Die Startzeit darf nicht negativ sein.")
    if end is not None and end <= start:
        raise ValueError("Das Ende muss nach dem Start liegen.")
    if volume is not None and not -100 <= volume <= 0:
        raise ValueError("Die Lautstärke muss zwischen -100 und 0 dB liegen.")
    meta = {"title": title if title and title != nice_title(demo["id"]) else None,
            "subtitle": str(body.get("subtitle") or "").strip() or None,
            "category": str(body.get("category") or "").strip() or None,
            "start": start or None, "end": end, "volume": volume}
    meta = {k: v for k, v in meta.items() if v is not None}
    with state_lock:
        if meta:
            library["meta"][demo["id"]] = meta
        else:
            library["meta"].pop(demo["id"], None)
        save_library()
        bump()


def in_folder(demo, folder):
    """Liegt die Demo im Ordner `folder` oder einem seiner Unterordner? ("" = alles)"""
    return not folder or demo["folder"] == folder or demo["folder"].startswith(folder + "/")


def api_move(body):
    """Verschiebt eine Demo um eine Stelle, aber nur innerhalb ihres eigenen Ordners."""
    with state_lock:
        demos = ordered_demos()
        ids = [d["id"] for d in demos]
        demo = find_demo(str(body.get("id")))
        i = ids.index(demo["id"])
        step = 1 if int(body.get("dir", 1)) > 0 else -1
        j = i + step
        while 0 <= j < len(ids) and demos[j]["folder"] != demo["folder"]:
            j += step
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            library["order"] = ids
            save_library()
            bump()


def api_play(body):
    start_demo(snapshot(find_demo(str(body.get("id")))))


def api_play_all(body):
    category = body.get("category")
    folder = str(body.get("folder") or "").strip("/")
    with state_lock:
        queue = [snapshot(d) for d in ordered_demos()
                 if in_folder(d, folder) and (not category or d["category"] == category)]
    if not queue:
        raise ValueError("Hier gibt es noch keine Demos.")
    start_demo(queue[0], queue, 0)


def api_next(body):
    with state_lock:
        a = active
    if not a or a["index"] + 1 >= len(a["queue"]):
        raise ValueError("Es gibt keine nächste Demo.")
    start_demo(a["queue"][a["index"] + 1], a["queue"], a["index"] + 1)


def api_folder(body):
    if body.get("path"):
        path = str(body["path"]).rstrip("/")
        if not folder_matches(path):
            raise ValueError("In diesem Ordner liegen nicht die Dateien aus dem Demo-Ordner.")
        with state_lock:
            library["zidooFolder"] = path
            save_library()
            bump()
        return {"folder": path}
    search_folder("manual")
    return {"folder": library["zidooFolder"]}


def public_state():
    with state_lock:
        a = active
        return {
            "zidoo": {"configured": bool(ZIDOO_HOST), "online": status["online"],
                      "model": status["model"], "folder": library["zidooFolder"],
                      "searching": folder_search["running"]},
            "storm": {"configured": bool(STORM_HOST), "maxVolume": STORM_MAX_VOLUME},
            "media": {"ok": media_ok, "label": FOLDER_LABEL},
            "haUrl": HA_URL,
            "playing": status["np"],
            "active": None if not a else {
                "id": a["demo"]["id"], "phase": a["phase"], "start": a["demo"]["start"],
                "end": a["demo"]["end"], "index": a["index"], "count": len(a["queue"])},
            "notice": dict(notice),
            "version": version,
        }


ROUTES = {
    "/api/demos": api_save_demo,
    "/api/demos/move": api_move,
    "/api/play": api_play,
    "/api/playall": api_play_all,
    "/api/next": api_next,
    "/api/stop": lambda body: stop_playback(),
    "/api/pause": lambda body: zidoo_key("Key.MediaPlay.Pause"),
    "/api/folder": api_folder,
}

STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
                "/icon.svg": ("icon.svg", "image/svg+xml")}

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                               "base-uri 'self'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "eclipse-demo"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send_body(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code=200):
        self.send_body(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    def handle_api(self, fn):
        try:
            self.send_json(fn())
        except ZidooError as e:
            self.send_json({"error": str(e)}, 502)
        except (ValueError, KeyError, TypeError) as e:
            self.send_json({"error": str(e)}, 400)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in STATIC_FILES:
            name, ctype = STATIC_FILES[url.path]
            with open(os.path.join(STATIC, name), "rb") as f:
                return self.send_body(200, f.read(), ctype)
        if url.path == "/api/health":
            return self.send_json({"ok": True, "media": media_ok})
        if url.path == "/api/state":
            return self.handle_api(public_state)
        if url.path == "/api/demos":
            return self.handle_api(ordered_demos)
        if url.path == "/api/browse":
            q = lambda k, d="": query.get(k, [d])[0]
            return self.handle_api(lambda: browse(q("path"), q("via", "list"), int(q("type", "0"))))
        self.send_json({"error": "Nicht gefunden"}, 404)

    def do_POST(self):
        # Nur JSON: fremde Webseiten können ohne CORS-Freigabe keine solchen Anfragen senden.
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self.send_json({"error": "Nur JSON-Anfragen"}, 415)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 65536:
            return self.send_json({"error": "Anfrage zu groß"}, 413)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send_json({"error": "Ungültige Anfrage"}, 400)
        route = ROUTES.get(urlparse(self.path).path)
        if not route:
            return self.send_json({"error": "Nicht gefunden"}, 404)
        self.handle_api(lambda: route(body) or {"ok": True})


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    os.makedirs(DATA, exist_ok=True)
    for target in (media_loop, engine_loop, folder_loop):
        threading.Thread(target=target, daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        sys.exit(f"Port {PORT} ist belegt.")
    server.daemon_threads = True
    log(f"Eclipse Demo läuft auf Port {PORT}; Demo-Ordner {MEDIA}, Zidoo {ZIDOO_HOST or '-'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
