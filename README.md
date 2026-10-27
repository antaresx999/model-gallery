# Model Gallery

Selbst gehostete Fotogalerie, über die du Bilder für deine Kunden in Projekten und Alben bereitstellst.
Die Kunden sehen ihre Galerien, markieren Favoriten und laden Originale einzeln oder als ZIP herunter.
Die Funktionen sind an Moodcase angelehnt, das Layout an die DPreview-Galerie
(Album-Kacheln, Thumbnail-Raster, dunkler Bildbetrachter mit EXIF-Spalte und Filmstreifen).

## Funktionen

### Galerie & Auslieferung
- **Projekte und Ordner**: Ein Projekt ist das organisatorische Gefäss (z. B. ein Kunde oder Auftrag). Darin
  gliederst du mit Ordnern beliebig tief. Alben sind immer die unterste Ebene und enthalten die Bilder.
  Kunden sehen nur Projekte und Ordner, in denen ein für sie freigegebenes Album liegt. Wer nur ein Projekt hat,
  landet direkt darin
- **Alben** mit Titel, Datum, Ort, Beschreibung, Titelbild, Entwurf/veröffentlicht und optionalem Ablaufdatum
- **Upload** per Drag & Drop, mehrere Dateien parallel mit Fortschritt. Die Originale bleiben unverändert,
  Vorschauen (640 px / 2048 px) werden automatisch erzeugt (EXIF-Ausrichtung wird beachtet)
- **Versionen**: Lädst du ein gleichnamiges Bild erneut hoch (z. B. den bearbeiteten Export), ersetzt es das alte
  als neue Version. Auswahl und Kommentare bleiben erhalten
- **Downloads**: Einzelbild (Original), ganzes Album oder nur die eigene Auswahl als ZIP, direkt gestreamt
- **Wasserzeichen** (Text oder Logo, Deckkraft einstellbar) pro Album oder pro Permalink, z. B. für Proofing.
  Mit Wasserzeichen sind Downloads gesperrt
- **Bildbetrachter** im DPreview-Stil: Aufnahmedaten, Zoom (Z / Doppelklick, verschieben mit der Maus),
  Blättern mit ← → oder Wischen, Esc zurück

### Auswahl & Feedback (angelehnt an Moodcase Studio)
- **Auswahl** per Herz, Filter „Meine Auswahl“, optionales Limit („höchstens 10 Bilder“)
- **Auswahl abschliessen** mit Nachricht an dich. Danach ist sie gesperrt, bis du sie wieder öffnest
- **Kommentare direkt am Bild**: Punkt setzen oder Bereich aufziehen, Typ *Kommentar*, *Zuschnitt*, *Retusche*
  oder *Korrektur*. Du antwortest und hakst Punkte als erledigt ab. Jede Person sieht nur die eigenen Threads
- **Gäste ohne Konto**: Wer einen Permalink öffnet, gibt einmal Name und E-Mail an und kann dann auswählen und
  kommentieren. Mit derselben E-Mail findet die Person ihre Auswahl auch auf einem anderen Gerät wieder
- **Export für Lightroom / Capture One**: Dateinamenliste zum Kopieren (Lightroom-Bibliotheksfilter
  „Dateiname enthält“) und CSV mit Kommentaren

### Studio-Verwaltung
- **Workflow-Board** (Kanban): In Vorbereitung → Auswahl läuft → In Bearbeitung → Geliefert → Archiv, per Drag & Drop
- **Neuigkeiten** im Dashboard (abgeschlossene Auswahlen, Kommentare) mit Zähler in der Navigation,
  optional zusätzlich per **E-Mail**
- **Protokoll** aller Aktionen: Anmeldungen, Uploads, Downloads, Löschungen, Links, Auswahlen
- **Userverwaltung**: Rollen *Administrator*, *Kunde* und *Lightroom-Plugin* (nur Upload, siehe unten). Kunden bekommen Zugriff auf einzelne Alben oder auf ein
  ganzes Projekt (inkl. künftiger Alben); dazu eine Gästeübersicht
- **Permalinks** pro Album, pro Projekt oder pro Ordner (inkl. Unterordner): Passwort, Ablaufdatum,
  Auswahl/Kommentare/Download/Wasserzeichen einzeln schaltbar, Aufruf- und Gästezähler, jederzeit widerrufbar.
  Projekt- und Ordnerlinks zeigen alle veröffentlichten Alben darin, auch später hinzugefügte; die Einstellungen
  der einzelnen Alben gelten zusätzlich
- **Branding**: Name, Logo und Akzentfarbe der Galerie
- Sicherheit: Jeder Bildabruf prüft die Berechtigung, CSRF-Schutz, scrypt-Passwörter, Login-Drosselung, `noindex`

- **RAW-Dateien**: Zu jedem Bild kann die Original-RAW-Datei (CR3, NEF, ARW, RAF, DNG …) gespeichert werden.
  Sie wird dem gleichnamigen Bild angehängt (`IMG_1234.jpg` ↔ `IMG_1234.CR3`). Kunden können sie nur laden,
  wenn im Album «Auch RAW-Dateien zum Download anbieten» aktiv ist

Nicht enthalten sind ein Capture-One-Plugin, KI-Verschlagwortung und Video.
Eigene Domains richtest du über den Reverse Proxy ein (siehe unten).

Technik: Python 3.12 · FastAPI · SQLite · Pillow. Alle Daten liegen in **einem** Verzeichnis
(`gallery.db`, `originals/`, `derived/`, `branding/`, `secret.key`). Für ein Backup genügt es, dieses Verzeichnis zu sichern.

## Lightroom-Plugin

Mit dem Plugin exportierst du Fotos aus **Lightroom Classic** direkt in ein bestehendes Album – als JPEG und
auf Wunsch zusätzlich mit der Original-RAW-Datei. (Das Cloud-basierte «Lightroom» unterstützt keine Plugins.)

1. In der Galerie unter **Benutzer → Neuer Benutzer** die Rolle **Lightroom-Plugin** wählen. Der API-Schlüssel
   wird danach **einmal** angezeigt – kopieren. Optional den Zugang auf bestimmte Projekte beschränken.
2. Auf derselben Seite **Plugin herunterladen**, entpacken und den Ordner `ModelGallery.lrplugin` an einen festen
   Ort legen (das Plugin liegt auch im Quellcode unter `app/lightroom/`).
3. Lightroom Classic → *Datei → Zusatzmodul-Manager → Hinzufügen* → Ordner wählen.
4. Fotos auswählen → *Datei → Exportieren* → oben *Exportieren auf: Model Gallery*. Server-Adresse und
   API-Schlüssel eintragen, **Verbinden** klicken, Album wählen. Bildgrösse, Qualität, Schärfen und Wasserzeichen
   stellst du wie gewohnt im Export-Dialog ein. Am besten als Export-Vorgabe speichern.

Details:
- Das JPEG trägt den Namen des Originals (`IMG_1234.jpg`); virtuelle Kopien erhalten den Kopienamen als Zusatz.
  Ein erneuter Export ersetzt das Bild als **neue Version** – Auswahl und Kommentare bleiben erhalten.
- Der Plugin-Zugang kann **nur** Alben auflisten und hochladen. Er kann sich nicht im Web anmelden und sieht
  keine Kunden, Auswahlen oder Kommentare. Der Schlüssel wird nur als Hash gespeichert und im Schlüsselbund von
  Lightroom abgelegt; bei Verlust erzeugst du einen neuen (der alte wird sofort ungültig).
- Bei Problemen schreibt das Plugin ein Logfile (ohne Schlüssel): macOS
  `~/Library/Logs/Adobe/Lightroom/LrClassicLogs/ModelGallery.log`.
- Schnittstelle für eigene Werkzeuge: `GET /api/plugin/info`, `GET /api/plugin/alben`,
  `POST /api/plugin/alben/{id}/bilder` (Multipart-Feld `file`, optional `?ersetzen=0`), jeweils mit
  `Authorization: Bearer <schlüssel>`.

## Betrieb

### Variante A: LXC unter Proxmox oder Incus (systemd, empfohlen)

Debian-12- oder Ubuntu-24.04-Container anlegen, z. B.:

```bash
# Proxmox
pct create 120 local:vztmpl/debian-12-standard_<version>_amd64.tar.zst --hostname galerie --memory 1024 --cores 2 --rootfs local-lvm:8 --mp0 /tank/galerie,mp=/var/lib/model-gallery --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --start 1
```

```bash
# Incus
incus launch images:debian/12 galerie
```

```bash
incus config device add galerie data disk source=/tank/galerie path=/var/lib/model-gallery
```

Projekt in den Container holen und installieren – entweder direkt von GitHub:

```bash
incus exec galerie -- sh -c "apt-get update -qq && apt-get install -y -qq git && git clone https://github.com/antaresx999/model-gallery /root/model-gallery"
```

oder eine lokale Kopie hineinschieben:

```bash
incus file push -r model-gallery galerie/root/   # bzw. scp / pct push
```

```bash
incus exec galerie -- /root/model-gallery/deploy/install.sh
```

Das Skript installiert Python, legt den Benutzer `gallery` an, richtet `/opt/model-gallery` und den
systemd-Dienst `model-gallery` ein und zeigt das erste Admin-Passwort an.
Für ein Update holst du die neue Version (`git -C /root/model-gallery pull`) und führst dasselbe Skript erneut aus.
Die Konfiguration liegt in `/etc/model-gallery.env`.

> Bei unprivilegierten Containern muss der Host-Ordner für das Datenverzeichnis der UID von `gallery`
> im Container gehören (Proxmox: Host-UID 100000 + Container-UID).

### Variante B: OCI-Container (Docker / Podman / Incus ≥ 6.3)

```bash
docker compose up -d --build
```

Incus kann OCI-Images direkt starten. Dazu pushst du das Image in eine Registry (z. B. `registry.example.ch`)
und bindest diese als Remote ein:

```bash
incus remote add myreg https://registry.example.ch --protocol=oci
```

```bash
incus launch myreg:model-gallery:latest galerie -c environment.GALLERY_BASE_URL=https://galerie.example.ch
```

Die Daten liegen im Volume `/data`.

### Erste Anmeldung

Benutzer `admin`. Ist `GALLERY_ADMIN_PASSWORD` nicht gesetzt, wird beim ersten Start ein Passwort generiert
und in `<Datenverzeichnis>/initial-admin-password.txt` abgelegt. Lösche die Datei nach der ersten Anmeldung.

### HTTPS / Reverse Proxy

Betreibe die Galerie hinter einem Reverse Proxy mit TLS (Caddy, nginx, Traefik, Nginx Proxy Manager)
und setze dann `GALLERY_BASE_URL=https://…` und `GALLERY_COOKIE_SECURE=1`.
Grosse Uploads brauchen ein ausreichendes Limit, z. B. bei nginx `client_max_body_size 200m;` – RAW-Dateien
aus Mittelformatkameras können über 150 MB gross sein.
Minimalbeispiel für Caddy:

```
galerie.example.ch {
    reverse_proxy 10.0.0.120:8080
}
```

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `GALLERY_DATA` | `/var/lib/model-gallery` | Datenverzeichnis |
| `GALLERY_SITE_NAME` | `Studio Galerie` | Name in Kopfzeile und Titel |
| `GALLERY_BASE_URL` | – | Öffentliche URL, wird für Permalinks verwendet |
| `GALLERY_COOKIE_SECURE` | `0` | `1` = Session-Cookie nur über HTTPS |
| `GALLERY_ADMIN_USER` / `GALLERY_ADMIN_PASSWORD` | `admin` / generiert | Erstes Admin-Konto (nur wenn noch keine Benutzer existieren) |
| `GALLERY_SECRET_KEY` | wird erzeugt | Schlüssel für die Session-Signatur |
| `GALLERY_SESSION_DAYS` | `14` | Gültigkeit der Anmeldung |
| `GALLERY_THUMB_SIZE` / `GALLERY_PREVIEW_SIZE` | `640` / `2048` | Vorschaugrössen (lange Kante) |
| `GALLERY_JPEG_QUALITY` | `85` | Qualität der Vorschaubilder |
| `GALLERY_SMTP_HOST` / `GALLERY_SMTP_PORT` | – / `587` | SMTP-Server für E-Mail-Benachrichtigungen |
| `GALLERY_SMTP_USER` / `GALLERY_SMTP_PASSWORD` | – | SMTP-Anmeldung (optional) |
| `GALLERY_SMTP_FROM` | = `GALLERY_SMTP_USER` | Absenderadresse |
| `GALLERY_SMTP_TLS` | `starttls` | `starttls`, `ssl` oder `none` |

Name, Logo, Akzentfarbe, Wasserzeichen und die Empfängeradresse für Benachrichtigungen stellst du in der
Oberfläche unter **Einstellungen** ein.

### Update von einer älteren Version

Die Datenbank wird beim Start automatisch migriert. Alben, Bilder, Benutzer und Favoriten bleiben erhalten,
bisherige Favoriten werden zu Auswahlen. Bestehende Alben landen im Projekt «Allgemein», das du danach umbenennen
oder aufteilen kannst. Sichere vorher trotzdem das Datenverzeichnis.

## Lokal entwickeln

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

```bash
GALLERY_DATA=./data GALLERY_ADMIN_PASSWORD=admin12345 venv/bin/uvicorn app.main:app --reload --port 8080
```

## Lizenz

GNU General Public License v3.0 – siehe [LICENSE](LICENSE).
