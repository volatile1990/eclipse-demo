# Eclipse Demo

Demo-Szenen und Musik fürs Heimkino per Kachel am Zidoo abspielen, bedienbar
vom Tablet oder Handy: **https://demo.home.arpa** (Heimnetz und VPN).

## So funktioniert es

- **Auswahl = Ordner.** Jede Video- oder Musikdatei in
  `\\unas.home.arpa\Cinema\Demos\Preview` erscheint nach wenigen Sekunden als
  Kachel. Unterordner werden zu Kategorien (z. B. `Preview\Musik`); Dateien
  direkt im Ordner landen unter „Film“ bzw. „Musik“.
- **Der Zidoo spielt direkt von der UNAS.** Eclipse Demo sagt ihm nur, welche
  Datei er öffnen soll; Bild und Ton laufen nicht über den Heimserver.
- **Pro Datei einstellbar** (Bearbeiten → Kachel): Titel, Untertitel,
  Kategorie, Start- und Endzeit sowie optional die Lautstärke der StormAudio.
  „Anspielen“ startet die Datei, „Jetzt“ übernimmt die aktuelle Stelle als
  Start oder Ende. Ohne Ende läuft die Datei bis zum Schluss.
- **Alle abspielen** spielt die Auswahl (oder eine Kategorie) in der
  angezeigten Reihenfolge; „Früher“/„Später“ im Bearbeiten-Modus ändert sie.
- **Fernbedienung hat Vorrang.** Startet jemand am Zidoo etwas anderes, mischt
  sich Eclipse Demo nicht mehr ein.
- Das Einschalten des Kinos bleibt bei Home Assistant. Ist der Zidoo aus, zeigt
  die Seite einen Link dorthin.

### Den Demo-Ordner auf dem Zidoo finden

Damit der Zidoo eine Datei öffnen kann, braucht Eclipse Demo den Pfad, unter
dem der Zidoo denselben Ordner sieht. Beim ersten Kontakt sucht der Dienst ihn
selbst: Er geht die Netzwerkfreigaben des Zidoo durch, sucht die UNAS, darin
die Freigabe `Cinema` und darunter `Demos/Preview`, und vergleicht die
Dateinamen. Voraussetzung ist, dass die Freigabe `Cinema` der UNAS im
Dateimanager des Zidoo gespeichert ist. Klappt die Suche nicht, lässt sich der
Ordner unter Einstellungen → „Von Hand wählen“ auswählen.

## Aufbau auf dem Heimserver

| Teil | Ort |
| --- | --- |
| Container `eclipse-demo` (Python-Standardbibliothek, UID 10050, nur lesend) | `deploy/compose.yaml`, Netz `nexus_default`, kein eigener Port |
| Demo-Ordner, nur lesend eingehängt | `//unas.home.arpa/Cinema/Demos/Preview` → `/srv/eclipse-demo/media/preview` (`deploy/systemd/eclipse-demo-preview.mount`) |
| Einstellungen und Reihenfolge | `/srv/eclipse-demo/data/library.json` |
| HTTPS über die NEXUS-CA | Caddy `nexus-proxy`, Block aus `deploy/Caddyfile.eclipse-demo-site` |
| DNS | `demo.home.arpa` → `192.168.1.175` (UniFi, `deploy/configure_dns.py`) |
| Releases | `/opt/eclipse-demo/releases/<commit>`, `current` zeigt auf das aktive |

Geräte: Zidoo `192.168.1.56:9529`, StormAudio `192.168.1.230:23` (nur wenn
eine Demo eine Lautstärke hat; nie lauter als `STORM_MAX_VOLUME`, Vorgabe −15 dB).

### Neues Release einspielen

```sh
# auf dem Windows-Rechner im Repository
git archive --format=tar.gz -o eclipse-demo.tar.gz HEAD
scp eclipse-demo.tar.gz heimserver:/tmp/
# auf dem Heimserver
REL=/opt/eclipse-demo/releases/$(git rev-parse --short=12 HEAD)   # Commit vorher notieren
sudo mkdir -p "$REL" && sudo tar -xzf /tmp/eclipse-demo.tar.gz -C "$REL"
sudo ln -sfn "$REL" /opt/eclipse-demo/current
cd /opt/eclipse-demo/current/deploy
sudo EDEMO_IMAGE=eclipse-demo:<commit> docker compose -p eclipse-demo up -d --build
```

## Entwicklung

```sh
python -m unittest discover -s tests -v
```

Die Tests starten den Dienst gegen einen simulierten Zidoo (`tests/mock_zidoo.py`)
samt SMB-Freigabeweg, Zeitraffer-Wiedergabe und Fernbedienungsübernahme.
Lokal starten: `EDEMO_DATA=./data EDEMO_MEDIA=<ordner> ZIDOO_HOST=<ip> python app/server.py`.
