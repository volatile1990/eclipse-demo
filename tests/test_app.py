"""Ende-zu-Ende-Tests: Eclipse Demo gegen einen simulierten Zidoo.

python -m unittest discover -s tests -v
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "/tmp/ramfs/mnt/192.168.1.88#SMB#Cinema/Demos/Preview"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EclipseDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        media = os.path.join(cls.tmp.name, "media")
        os.makedirs(os.path.join(media, "Musik", "Atmos"))
        os.makedirs(os.path.join(media, ".intern"))
        for name in ("01 Größter Film & Co.mkv", "02_Zweite_Szene.mp4", "Musik/Lied.flac",
                     "Musik/Atmos/Tief.flac", "notizen.txt", ".versteckt.mkv", ".intern/geheim.mkv"):
            open(os.path.join(media, *name.split("/")), "wb").close()
        cls.media = media
        cls.zport, cls.port = free_port(), free_port()
        env = dict(os.environ, EDEMO_DATA=os.path.join(cls.tmp.name, "data"), EDEMO_MEDIA=media,
                   EDEMO_PORT=str(cls.port), ZIDOO_HOST="127.0.0.1", ZIDOO_PORT=str(cls.zport),
                   PYTHONIOENCODING="utf-8")
        cls.procs = [
            subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_zidoo.py"), str(cls.zport), media]),
            subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "server.py")], env=env,
                             stdout=subprocess.DEVNULL),
        ]
        cls.wait(lambda s: s["zidoo"]["folder"] == FOLDER, 30, "Ordnersuche")

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.terminate()
            p.wait()
        cls.tmp.cleanup()

    # ---------------------------------------------------------- Helfer
    @classmethod
    def call(cls, path, body=None, base=None, ctype="application/json"):
        url = (base or f"http://127.0.0.1:{cls.port}") + path
        req = Request(url, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": ctype})
        for attempt in range(40):
            try:
                with urlopen(req, timeout=15) as r:
                    return r.status, json.loads(r.read()), dict(r.headers)
            except HTTPError as e:
                return e.code, json.loads(e.read()), dict(e.headers)
            except OSError:
                time.sleep(0.25)
        raise AssertionError("Server nicht erreichbar")

    @classmethod
    def state(cls):
        return cls.call("/api/state")[1]

    @classmethod
    def wait(cls, cond, timeout=20, what=""):
        end = time.time() + timeout
        while time.time() < end:
            s = cls.state()
            if cond(s):
                return s
            time.sleep(0.2)
        raise AssertionError(f"Zeitüberschreitung: {what} | {json.dumps(cls.state(), ensure_ascii=False)}")

    def zcalls(self):
        return self.call("/calls", base=f"http://127.0.0.1:{self.zport}")[1]["calls"]

    def demos(self):
        return self.call("/api/demos")[1]

    def demo(self, title):
        return next(d for d in self.demos() if d["title"] == title)

    def stop(self):
        self.call("/api/stop", {})
        self.wait(lambda s: s["playing"] is None and s["active"] is None, what="Stopp")

    # ----------------------------------------------------------- Tests
    def test_01_folder_scan(self):
        demos = self.demos()
        self.assertEqual([(d["title"], d["category"], d["kind"], d["folder"]) for d in demos], [
            ("01 Größter Film & Co", "Film", "video", ""),
            ("02 Zweite Szene", "Film", "video", ""),
            ("Lied", "Musik", "music", "Musik"),
            ("Tief", "Musik", "music", "Musik/Atmos")])

    def test_02_scene_with_start_and_end(self):
        a = self.demo("01 Größter Film & Co")
        code, body, _ = self.call("/api/demos", {"id": a["id"], "title": "Szene A", "start": 30, "end": 60})
        self.assertEqual(code, 200, body)
        self.wait(lambda s: any(d["title"] == "Szene A" for d in self.demos()), what="Titel")
        n = len(self.zcalls())
        self.assertEqual(self.call("/api/play", {"id": a["id"]})[0], 200)
        s = self.wait(lambda s: s["active"] and s["active"]["phase"] == "running", what="läuft")
        self.assertGreaterEqual(s["playing"]["position"], 27000)
        self.assertEqual(s["playing"]["path"], FOLDER + "/01 Größter Film & Co.mkv")
        self.wait(lambda s: s["active"] is None and s["playing"] is None, what="Ende")
        new = [c["path"] + json.dumps(c["query"], ensure_ascii=False) for c in self.zcalls()[n:]]
        self.assertTrue(any('seekTo{"positon": "30000"}' in c for c in new), new)
        self.assertIn("Key.MediaStop", new[-1])

    def test_03_play_all_in_order(self):
        ids = [d["id"] for d in self.demos()]
        self.call("/api/playall", {})
        seen, end = [], time.time() + 80
        while time.time() < end:
            s = self.state()
            if s["active"] and (not seen or seen[-1] != s["active"]["id"]):
                seen.append(s["active"]["id"])
                self.assertEqual(s["active"]["count"], 4)
            if seen == ids and s["active"] is None:
                break
            time.sleep(0.2)
        self.assertEqual(seen, ids)

    def test_04_remote_takes_over(self):
        self.call("/api/playall", {"folder": "Musik/Atmos"})
        s = self.wait(lambda s: s["active"] and s["active"]["phase"] == "running", what="Atmos-Ordner")
        self.assertEqual(s["active"]["count"], 1)
        self.assertEqual(s["playing"]["path"], FOLDER + "/Musik/Atmos/Tief.flac")
        other = FOLDER + "/02_Zweite_Szene.mp4"
        self.call("/remote-open?path=" + other.replace(" ", "%20").replace("#", "%23"),
                  base=f"http://127.0.0.1:{self.zport}")
        self.wait(lambda s: s["active"] is None, what="gibt ab")
        time.sleep(2)
        self.assertEqual(self.state()["playing"]["path"], other)
        self.stop()

    def test_05_pause_next_stop(self):
        self.call("/api/playall", {"category": "Film"})
        first = self.demos()[0]["id"]
        self.wait(lambda s: s["active"] and s["active"]["id"] == first, what="erste")
        self.call("/api/pause", {})
        self.wait(lambda s: s["playing"] and s["playing"]["state"] == "paused", what="Pause")
        self.assertEqual(self.call("/api/next", {})[0], 200)
        self.wait(lambda s: s["active"] and s["active"]["index"] == 1 and s["active"]["phase"] == "running",
                  what="nächste")
        self.assertEqual(self.call("/api/next", {})[0], 400)
        self.stop()

    def test_06_reorder_and_new_file(self):
        before = [d["title"] for d in self.demos()]
        # Einziger Eintrag in seinem Ordner: Verschieben ändert nichts
        self.call("/api/demos/move", {"id": self.demo("Tief")["id"], "dir": 1})
        self.assertEqual([d["title"] for d in self.demos()], before)
        # Innerhalb der obersten Ebene tauschen die beiden Filme
        self.call("/api/demos/move", {"id": self.demo("02 Zweite Szene")["id"], "dir": -1})
        self.assertEqual([d["id"] for d in self.demos()][:2], ["02_Zweite_Szene.mp4", "01 Größter Film & Co.mkv"])
        self.call("/api/demos/move", {"id": self.demo("02 Zweite Szene")["id"], "dir": 1})
        open(os.path.join(self.media, "03 Neu.mkv"), "wb").close()
        self.wait(lambda s: any(d["title"] == "03 Neu" for d in self.demos()), 15, "neue Datei")
        root = [d["title"] for d in self.demos() if d["folder"] == ""]
        self.assertEqual(root[-1], "03 Neu")   # neue Datei: ans Ende ihres Ordners
        os.remove(os.path.join(self.media, "03 Neu.mkv"))
        self.wait(lambda s: not any(d["title"] == "03 Neu" for d in self.demos()), 15, "Datei weg")
        code, body, _ = self.call("/api/play", {"id": "03 Neu.mkv"})
        self.assertEqual(code, 400, body)

    def test_07_validation_and_headers(self):
        a = self.demos()[0]
        self.assertEqual(self.call("/api/demos", {"id": a["id"], "start": 50, "end": 40})[0], 400)
        self.assertEqual(self.call("/api/demos", {"id": a["id"], "volume": 5})[0], 400)
        self.assertEqual(self.call("/api/play", {"id": a["id"]}, ctype="text/plain")[0], 415)
        self.assertEqual(self.call("/api/folder", {"path": "/tmp/ramfs/smb"})[0], 400)
        with urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            self.assertIn(b"Eclipse Demo", r.read())

    def test_09_play_folder_recursive(self):
        self.call("/api/playall", {"folder": "Musik"})
        s = self.wait(lambda s: s["active"] and s["active"]["phase"] == "running", what="Musik-Ordner")
        self.assertEqual(s["active"]["count"], 2)
        # Eigene Dateien vor dem Unterordner, obwohl "Atmos/..." alphabetisch vor "Lied" liegt
        self.assertEqual(s["active"]["id"], "Musik/Lied.flac")
        self.stop()
        self.assertEqual(self.call("/api/playall", {"folder": "Gibt/Es/Nicht"})[0], 400)

    def test_08_browse_to_share(self):
        root = self.call("/api/browse?path=")[1]["entries"]
        smb = next(e for e in root if e["via"] == "host")
        hosts = self.call(f"/api/browse?path={smb['path']}&via=host&type={smb['type']}")[1]["entries"]
        self.assertEqual(hosts[0]["path"], "192.168.1.88")
        shares = self.call(f"/api/browse?path=192.168.1.88&via=share&type={hosts[0]['type']}")[1]["entries"]
        self.assertIn("Cinema", [s["name"] for s in shares])


if __name__ == "__main__":
    unittest.main()
