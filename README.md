# Buchhaltung für KMU

Lokale Software für kleine Unternehmen und Selbstständige: Kunden, Leistungen, Angebote und
Rechnungen als XRechnung (mit PDF), dazu **Belegerfassung** und **Einnahmen-Überschuss-Rechnung (EÜR)**.
Basis ist eine SQLite-Datenbank (auf Wunsch verschlüsselt); die Bedienung erfolgt im Browser.

Das Programm ist aus „Kundenverwaltung mit X-Rechnung“ (Programmname „X-Rechnung für KMU“) entstanden,
dessen vollständige Historie dieses Repository enthält. Abgeschlossene Rechnungen landen automatisch
in der Belegerfassung und damit in der EÜR.

## Stand

| Modul | Status |
|---|---|
| Kundenverwaltung | ✅ fertig |
| Firmenstammdaten (mit Logo) | ✅ fertig |
| Leistungsverzeichnis (mit Import/Export und Änderungsprotokoll) | ✅ fertig |
| Angebote (mit PDF) | ✅ fertig |
| Rechnungen mit XRechnung (XML) + PDF | ✅ fertig |
| Stornorechnungen und Rechnungskorrekturen | ✅ fertig |
| Windows-Installationsprogramm | ✅ fertig |
| Lizenzprüfung und Testversion | ✅ fertig |
| Verschlüsselung der Datenbank mit Passwort | ✅ fertig |
| **Belegerfassung** (Einnahmen/Ausgaben, Belegdateien, GoBD-Festschreibung) | ✅ fertig |
| **Einnahmen-Überschuss-Rechnung** (mit AfA, Anlagenverzeichnis, PDF/CSV) | ✅ fertig |
| **Tags** für Belege, Auswertung und CSV nach Tags | ✅ fertig (1.1) |
| Lizenzpflicht | vorerst abgeschaltet (1.1) – XRechnung ohne Lizenznummer |
| **Netzwerkversion** (Docker, Benutzer und Rollen, HTTPS) | ✅ fertig (1.2) |
| **Netzwerkversion für Windows** (Server-PC, Daten auf Netzlaufwerk, Arbeitsplätze im Browser) | ✅ fertig (1.3) |
| **Datensicherung** (Knopf oben rechts, beliebiges Laufwerk oder Download, optional AES-256-Passwort) | ✅ fertig (1.4) |
| **Wiederherstellung** aus einer Sicherung (geprüft, bisheriger Stand wird aufgehoben) | ✅ fertig (1.5) |
| Setup: Datenordner muss für den Benutzer erreichbar und beschreibbar sein (Prüfung) | ✅ fertig (1.5) |

## Datensicherung

Knopf **💾 Datensicherung** oben rechts (Netzwerkversion: nur Administratoren) → `app/sicherung/`.

- Inhalt: der ganze Datenordner – Datenbank als konsistenter Stand (Lesetransaktion während der
  Kopie, auch für die SQLCipher-verschlüsselte Datenbank) und alle Dokumente (Belege, Rechnungs-PDF/-XML,
  Angebote, Logos). Dazu `sicherung.json` (Version, Zeitpunkt, alle Dateien mit SHA-256) und
  `LIESMICH.txt` (Wiederherstellen). Nicht enthalten: Sitzungsschlüssel, Sperr-/Zwischendateien,
  Startprotokolle, Einrichtungscode.
- Ziel: ein beliebiges Laufwerk (Liste aller Laufwerke mit freiem Platz, Unterordner oder eigener Pfad,
  auch `\\NAS\Freigabe`) oder als Download im Browser. Ein Ziel im Datenordner selbst wird abgelehnt.
  Windows-Netzwerkversion: Laufwerke des Server-PCs; Docker: nur Download.
- Optional mit Passwort: ZIP mit AES-256 (WinZip-AES, `pyzipper`) – zu öffnen mit 7-Zip, WinRAR,
  WinZip; ohne Passwort eine normale ZIP-Datei.
- Die Datei entsteht als `.teil`, wird vollständig zurückgelesen und geprüft (CRC/AES, Abgleich mit
  `sicherung.json`) und erst dann umbenannt. Jede Sicherung steht im Änderungsprotokoll; ist die letzte
  älter als 30 Tage, wird der Knopf hervorgehoben.

### Wiederherstellen (seit 1.5)

Auf derselben Seite, Abschnitt **♻ Wiederherstellen**: eine Sicherung aus den bisherigen Zielordnern
wählen (Liste, 🔒 = mit Passwort), einen Pfad angeben oder die ZIP-Datei hochladen; Bestätigung nötig.

1. Prüfen: Programm und Version (keine Sicherung einer neueren Version), sichere Dateinamen (kein
   „Zip-Slip“), Passwort, SHA-256 jeder Datei laut `sicherung.json`, `PRAGMA integrity_check` der Datenbank
   (bei SQLCipher nach dem Entsperren). Bei jedem Fehler bleibt alles unverändert.
2. Austauschen: neue Dateien in `.wiederherstellung-neu` entpacken, bisherige nach `.wiederherstellung-alt`
   verschieben, neue an ihre Stelle – schlägt das fehl, wird sofort zurückverschoben. Sitzungsschlüssel,
   Sperrdatei und Startprotokolle bleiben stehen. Während des Austauschs antworten andere Anfragen mit 503.
3. Der bisherige Stand wird als `.sicherungen-vor-wiederherstellung/Vor-Wiederherstellung_<Zeit>.zip`
   aufgehoben (die letzten 5; nicht Teil neuer Sicherungen). Ältere Datenbanken werden auf das aktuelle
   Schema gebracht. War die Datenbank verschlüsselt, wird danach das damalige Passwort abgefragt.

## Netzwerkversion (Docker auf Linux-Server oder NAS)

Eigene Auslieferung aus demselben Code: Der Schalter `BUCHHALTUNG_NETZWERK=1` (bzw.
`create_app(..., netzwerk=True)`) schaltet Anmeldung, Benutzer mit Rollen (Administrator,
Buchhaltung, Nur lesen), den Benutzernamen im Änderungsprotokoll und die Beschränkung auf das
lokale Netz ein. `python -m app.netzwerk.server` startet den HTTPS-Server (gunicorn, ein Prozess
mit mehreren Threads) mit selbst erzeugter Zertifizierungsstelle und Serverzertifikat
(`app/netzwerk/zertifikat.py`). Die Einzelplatzversion bleibt unverändert.

| Datei | Inhalt |
|---|---|
| `netzwerk/Dockerfile`, `netzwerk/entrypoint.sh` | Image auf Basis `python:3.12-slim`, läuft als `PUID`/`PGID` statt root |
| `netzwerk/docker-compose.yml` | Vorlage für Server/NAS |
| `netzwerk/ANLEITUNG.md` | Installation, Einrichtung, Zertifikat importieren, Sicherung, Umzug |
| `.github/workflows/netzwerk-image.yml` | baut bei jeder Änderung auf `main` das Image (amd64 + arm64, Version aus `__version__`) nach `ghcr.io/enemenemedia/buchhaltung` |
| `werkzeuge/netzwerk_paket.py` | erzeugt `dist/Buchhaltung-fuer-KMU-Netzwerk-<Version>.zip` |

```sh
docker build -f netzwerk/Dockerfile -t buchhaltung-kmu:1.3.0 .
docker run -d -p 8443:8443 -v ./daten:/daten -e BUCHHALTUNG_HOSTNAMEN=nas,192.168.1.10 buchhaltung-kmu:1.3.0
docker logs <container>     # Einrichtungscode für den ersten Administrator
```

Rechte: *Nur lesen* darf ausschließlich GET-Anfragen (ansehen, PDF/CSV); *Buchhaltung* alles außer
Benutzerverwaltung, Einstellungen und dem Ändern der Stammdaten. Die Prüfung läuft zentral vor jeder
Anfrage (`app/netzwerk/__init__.py`, `erlaubt()`).

## Netzwerkversion für Windows (LAN, Daten auf einem Netzlaufwerk)

Eigenes Setup `Buchhaltung-fuer-KMU-Netzwerk-Setup-<Version>.exe` (`windows/installer_netzwerk.nsi`,
Starter `windows/netzwerk_starter.py`, Anleitung `windows/ANLEITUNG-NETZWERK.md`). Ein Setup, zwei
Installationsarten:

- **Server** (ein PC): startet die Anwendung im Netzwerkmodus (Anmeldung, Rollen, HTTPS über
  cheroot, Port 8443) mit dem Datenordner `<Freigabe>\Buchhaltung für KMU\Daten`. Eine exklusiv
  gesperrte Datei auf dem Netzlaufwerk verhindert einen zweiten Server; nur ein Prozess öffnet die
  SQLite-Datenbank. Der Server legt `netzwerk-server.json` (Adresse, Lebenszeichen alle 30 s) und das
  öffentliche CA-Zertifikat in `<Freigabe>\Buchhaltung für KMU` ab. Sitzungsschlüssel,
  Zertifikatsschlüssel und Einrichtungscode bleiben lokal (`%LOCALAPPDATA%\Buchhaltung-KMU-Netzwerk`);
  am Server-PC selbst (127.0.0.1) geht die Einrichtung des ersten Administrators ohne Code.
- **Arbeitsplatz**: liest die Server-Adresse vom Netzlaufwerk, importiert das CA-Zertifikat einmalig in
  den Zertifikatspeicher des Benutzers (`certutil -user`) und öffnet den Browser.

Das Setup weist das Netzlaufwerk zu: UNC-Pfad prüfen, optional Zugangsdaten (`cmdkey`) und
Laufwerksbuchstabe (`net use … /persistent:yes`), Schreibtest (Server). Dazu Autostart (Server) und eine
Firewall-Regel nur für private/Domänen-Netze (einmalige UAC-Abfrage). Unbeaufsichtigt:
`/S /MODUS=server|arbeitsplatz /FREIGABE=\\NAS\Buero [/LAUFWERK=N:] [/PORT=8443]`.

Bauen: `python3 windows/bauen.py --variante netzwerk` (oder `alle` für Einzelplatz + Netzwerk).

## Installation unter Windows

**Seit Version 1.1 ist die Lizenzpflicht vorerst abgeschaltet** (`app/lizenz.py`: `LIZENZPFLICHT = False`).
Es gibt dann nur eine Setup-Datei, `Buchhaltung-fuer-KMU-Setup-<Version>.exe`, ohne Lizenzseite; alle
Funktionen einschließlich XRechnung sind nutzbar. `python3 windows/bauen.py` baut standardmäßig diese
Variante („frei“).

Mit eingeschalteter Lizenzpflicht (`LIZENZPFLICHT = True`, `windows/bauen.py --variante beide`) gibt es
zwei Setup-Dateien – Python und alle Komponenten sind jeweils enthalten, Administratorrechte sind nicht nötig:

| | Vollversion | Testversion |
|---|---|---|
| Datei | `Buchhaltung-fuer-KMU-Setup-<Version>.exe` | `Buchhaltung-fuer-KMU-Testversion-Setup-<Version>.exe` |
| Installation | nur mit gültiger Lizenznummer | ohne Lizenznummer |
| XRechnung (XML) | ✅ | ❌ – Hinweis „Testversion“ im Programm |
| Belegerfassung und EÜR | ✅ | ✅ (voll nutzbar) |
| alles andere | ✅ | ✅ |

Lizenznummern von „X-Rechnung für KMU“ (`XRK1-…`) gelten unverändert.

- **Vollversion:** Der Installer fragt Lizenznehmer und Lizenznummer ab. Er entpackt die
  Programmdateien zunächst in einen Zwischenordner und prüft die Lizenz mit dem mitgelieferten
  Python. Ist sie ungültig, wird nichts installiert und eine vorhandene Installation bleibt
  unverändert. Bei einer Aktualisierung wird die vorhandene Lizenz vorgeschlagen.
  Unbeaufsichtigt: `Setup.exe /S /LIZENZNEHMER="Firma GmbH" /LIZENZNUMMER=XRK1-…`
- **Testversion:** Eine Lizenznummer kann jederzeit im Programm unter **Einstellungen → Lizenz**
  eingegeben werden – ohne Neuinstallation und ohne Datenverlust.

- **Programmordner:** `%LOCALAPPDATA%\Programs\Buchhaltung für KMU` (änderbar)
- **Datenordner:** `Dokumente\Buchhaltung für KMU` (im Installer änderbar; darf nicht im Programmordner liegen).
  Findet der Installer Daten von „X-Rechnung für KMU“ (`Dokumente\X-Rechnung`), weist er darauf hin:
  Wird dieser Ordner gewählt, arbeitet das Programm mit den vorhandenen Kunden, Rechnungen und der
  Lizenz weiter (die Datenbank wird um die Buchhaltungstabellen ergänzt). Beide Programme dann nicht
  gleichzeitig verwenden.
- Verknüpfungen im Startmenü (Programm, „Datenordner öffnen“, Deinstallation) und auf dem Desktop
- Das Programm startet einen lokalen Server (nur `127.0.0.1`, Port 8766 oder ein freier Port – so kann es
  neben „X-Rechnung für KMU“ auf Port 8765 laufen) und
  öffnet den Browser. Ein zweiter Start öffnet nur den Browser. Beendet wird über **Beenden** oben rechts.
- **Update:** neue Setup-Datei einfach ausführen – ein laufendes Programm wird beendet,
  Datenordner und Einstellungen bleiben erhalten.
- **Deinstallation** (Startmenü oder „Apps & Features“) entfernt nur das Programm. Die Daten bleiben
  wegen der gesetzlichen Aufbewahrungsfristen erhalten.
- Protokoll für die Fehlersuche: `<Datenordner>\protokoll\programm.log`
- Die Lizenz liegt als `lizenz.ini` im Datenordner und bleibt bei Deinstallation und Update erhalten.
- INI-Dateien (`einstellungen.ini`, `lizenz.ini`) werden als UTF-16 geschrieben und in jeder Kodierung
  gelesen – Umlaute im Datenordner oder im Namen des Lizenznehmers sind kein Problem.
- Die Setup-Datei ist nicht signiert. Windows SmartScreen meldet daher „Der Computer wurde durch
  Windows geschützt“; weiter über „Weitere Informationen“ → „Trotzdem ausführen“.

### Installer bauen

Der Installer wird unter Linux gebaut (kein Windows nötig):

```bash
sudo apt install nsis
python3 werkzeuge/pruefung_einrichten.py build/pruefung         # Prüfartefakte (optional)
python3 windows/bauen.py --pruefung build/pruefung                # → dist/…-Setup-<Version>.exe und …-Testversion-Setup-<Version>.exe
```

Mit `--variante voll` oder `--variante test` wird nur eine der beiden Setup-Dateien gebaut.

`bauen.py` lädt Python 3.12 für Windows (conda-forge, per micromamba) und die Pakete als
Windows-Wheels von PyPI. Danach fügt es Anwendung, Starter (`windows/starter.py`) und Prüfregeln hinzu
und erzeugt den Installer mit NSIS (`windows/installer.nsi`). Die Version kommt aus `app/__init__.py`.

## Installation unter macOS

Ebenfalls zwei Pakete, jeweils ein ZIP mit dem Programm „Buchhaltung für KMU.app“ – für
Apple Silicon und Intel-Macs (macOS 10.15 oder neuer), Python und alle Komponenten sind enthalten:

| | Vollversion | Testversion |
|---|---|---|
| Datei | `Buchhaltung-fuer-KMU-<Version>-macOS.zip` | `Buchhaltung-fuer-KMU-Testversion-<Version>-macOS.zip` |
| Lizenz | wird beim ersten Start abgefragt; ohne gültige Lizenz startet das Programm nicht | keine – XRechnung (XML) wird nicht erstellt |

- **Installation:** ZIP öffnen, „Buchhaltung für KMU“ in den Ordner „Programme“ ziehen.
  Ein Mac-Programm hat keinen Installer; die Lizenzabfrage des Windows-Installers erfolgt deshalb
  beim ersten Start (Dialog für Lizenznehmer und Lizenznummer, Prüfung wie unter Windows).
- **Datenordner:** wird beim ersten Start gewählt (Vorschlag `~/Documents/Buchhaltung für KMU`), gespeichert in
  `~/Library/Application Support/Buchhaltung für KMU/einstellungen.ini`. Die Lizenz liegt wie unter Windows als
  `lizenz.ini` im Datenordner. Liegt dort bereits eine Lizenz (auch eine abgelaufene), startet das
  Programm immer – ohne gültige Lizenz als Testversion, damit die Daten zugänglich bleiben.
- Das Programm läuft im Browser (nur `127.0.0.1`), ohne Symbol im Dock; beendet wird es über **Beenden**.
  Ein zweiter Start öffnet nur den Browser.
- **Nicht notarisiert:** Die Programmdateien sind ad hoc signiert (nötig für Apple Silicon), aber nicht mit
  einer Apple-Developer-ID. macOS meldet daher beim ersten Öffnen, dass der Entwickler nicht überprüft
  werden kann: unter macOS 15 einmal öffnen, dann *Systemeinstellungen → Datenschutz & Sicherheit →
  Dennoch öffnen*; unter macOS 14 und älter mit ctrl-Klick → *Öffnen*. Abhilfe schafft eine
  Developer-ID (Apple Developer Program) mit Notarisierung – das geht auch unter Linux mit `rcodesign`.

### macOS-Pakete bauen

Ebenfalls unter Linux:

```bash
python3 macos/bauen.py --pruefung build/pruefung      # → dist/…-macOS.zip (Voll- und Testversion)
```

`bauen.py` lädt Python 3.12 für macOS (conda-forge, `osx-arm64` und `osx-64`) und die Pakete als
macOS-Wheels, signiert alle Programmdateien ad hoc mit `rcodesign` (wird bei Bedarf geladen) und
prüft die Signaturen. Mit `--variante voll` oder `--variante test` wird nur ein Paket gebaut.

## Starten (Entwicklung)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Danach im Browser `http://127.0.0.1:5000` öffnen.

Alle Daten liegen im Ordner `daten/` (änderbar über die Umgebungsvariable `XRECHNUNG_DATEN`;
der Windows-Starter liest zusätzlich `BUCHHALTUNG_DATEN`):

```
daten/
├── xrechnung.db             SQLite-Datenbank (auch Belege; Name aus Kompatibilitätsgründen)
├── belege/
│   └── 2026/7d/7d4a…f67db.pdf   Belegdateien, unveränderlich unter ihrer SHA-256-Prüfsumme
├── stammdaten/
│   ├── logo.png             aktuelles Firmenlogo
│   └── logo_archiv/         frühere Logos
└── kunden/
    └── K00001/              ein Ordner je Kundennummer
        ├── angebote/        z. B. AN-2026-0001.pdf
        ├── rechnungen/      z. B. 2026-10-25_RE-2026-0001_K00001.xml + .pdf
        │                         2026-10-26_ST-2026-0001_K00001_storno_zu_RE-2026-0001.xml + .pdf
        │                         2026-10-26_KR-2026-0001_K00001_korrektur_zu_RE-2026-0002.xml + .pdf
        └── dokumente/
```

## Lizenz und Testversion

- **Lizenznummer** – eine digitale Signatur (Ed25519) über den Lizenznehmer, eine Seriennummer,
  das Ausstellungsdatum und ein optionales Ablaufdatum, z. B. `XRK1-AEAQA-AAAAE-…`.
  Das Programm enthält nur den öffentlichen Schlüssel und prüft offline – gültige Nummern
  erzeugen kann nur, wer den privaten Schlüssel hat. Der Lizenznehmer erscheint in der Fußzeile.
- **Prüfung** im Vollversions-Installer (vor der Installation) und bei jedem Programmstart.
- **Ohne gültige Lizenz** (Testversion, abgelaufene oder fehlende Lizenz) läuft das Programm als
  Testversion: Ein Band oben weist darauf hin, alles ist nutzbar, nur die **XRechnung (XML) wird
  nicht erstellt**. Beim Abschließen einer Rechnung entsteht das PDF; die Rechnung wird als
  „XRechnung ausstehend“ markiert. Die Daten bleiben immer zugänglich (Aufbewahrungspflicht).
- **Nach dem Lizenzieren** lässt sich die XRechnung zu diesen Rechnungen mit
  „XRechnung jetzt erstellen“ nachholen – aus den beim Abschluss eingefrorenen Angaben, also
  inhaltlich genau so, wie sie beim Abschließen entstanden wäre.

### Lizenzen ausstellen (nur für den Hersteller)

```bash
pip install cryptography
# einmalig: Schlüsselpaar erzeugen – trägt den öffentlichen Schlüssel in app/lizenz.py ein
python werkzeuge/lizenz_erzeugen.py schluessel --privat ~/xrechnung-lizenz/lizenz-privat.pem
# Lizenz für einen Kunden (Seriennummer fortlaufend, optional --bis 2027-12-31)
python werkzeuge/lizenz_erzeugen.py erzeugen --privat ~/xrechnung-lizenz/lizenz-privat.pem --name "Firma GmbH"
# Lizenz prüfen
python werkzeuge/lizenz_erzeugen.py pruefen --name "Firma GmbH" --nummer XRK1-…
```

Alle ausgestellten Lizenzen werden in `lizenzen.csv` neben dem privaten Schlüssel protokolliert.
Der private Schlüssel gehört **nicht** ins Repository (`.gitignore` schließt `*.pem` aus) und nicht
in den Installer. Ein neues Schlüsselpaar macht alle bisher ausgegebenen Lizenznummern ungültig.

Hinweis: Das Programm liegt als Python-Quelltext vor. Wer gezielt den Programmcode verändert, kann
jede lokale Lizenzprüfung umgehen – die Prüfung schützt vor einfacher Weitergabe, nicht vor
Manipulation.

## Verschlüsselung der Datenbank

Unter **Einstellungen → Verschlüsselung der Datenbank** kann die Datenbank mit einem selbst gewählten
Passwort (mindestens 8 Zeichen) verschlüsselt werden.

- **SQLCipher** (AES-256, Schlüssel per PBKDF2-HMAC-SHA512 aus dem Passwort abgeleitet). Ohne
  Passwort lässt sich die Datei weder mit diesem Programm noch mit anderen SQLite-Werkzeugen öffnen.
- **Beim Start** fragt das Programm nach dem Passwort; bis dahin führt jede Seite zur Passworteingabe.
  Das Passwort wird nicht gespeichert, nur im Arbeitsspeicher gehalten, solange das Programm läuft.
  **Sperren** (oben rechts) sperrt sofort, z. B. beim Verlassen des Arbeitsplatzes.
- **Passwort ändern** und **Verschlüsselung aufheben** sind jederzeit möglich (mit dem bisherigen Passwort).
- Die Datenbank wird dabei vollständig in eine neue Datei geschrieben, geprüft und erst dann ersetzt –
  bei einem Fehler bleibt die bisherige Datei unverändert. Schutz-Trigger und Fremdschlüssel bleiben erhalten.
- **Ohne Passwort sind die Daten verloren** – es gibt keine Wiederherstellung.
- PDFs und XRechnungen in den Kundenordnern sowie ältere Sicherungskopien bleiben unverschlüsselt.

## Kundenverwaltung

- **Firma und Sitz** – Felder entsprechen den Käuferangaben der XRechnung
  (Name, Anschrift, Ländercode, USt-IdNr., Leitweg-ID für öffentliche Auftraggeber).
- **Ansprechpartner** – beliebig viele je Kunde, jeweils mit eigener E-Mail, Telefon
  und Mobilnummer; einer ist als Hauptkontakt markiert.
- **Eindeutige Kundennummer** (`K00001`, …) – wird automatisch vergeben und ist
  Name des Ablageordners.
- **Änderbar mit Protokoll** – jede Änderung wird mit Zeitpunkt sowie altem und
  neuem Wert im Änderungsprotokoll festgehalten.
- **Archivieren** – archivierte Kunden bleiben vollständig erhalten, sind aber
  schreibgeschützt; sie können jederzeit reaktiviert werden.
- **Löschen erst nach 10 Jahren** – endgültiges Löschen ist nur für archivierte
  Kunden nach Ablauf der Aufbewahrungsfrist möglich (§ 147 AO / § 257 HGB: Frist
  beginnt mit Ende des Kalenderjahres; archiviert 2026 → löschbar ab 01.01.2037).
  Die Sperre ist zusätzlich als Trigger in der Datenbank hinterlegt, gilt also auch
  bei direktem SQL-Zugriff.

## Stammdaten

- **Firmendaten als Absender** – Name, Inhaber/Geschäftsführung, Anschrift,
  Kommunikation, Steuer- und Registerangaben, Bankverbindung und Standard-Zahlungsziel.
  Die Felder entsprechen den Verkäuferangaben der XRechnung (BG-4 Seller).
- **Prüfung auf X-Rechnung-Tauglichkeit** – die Seite zeigt, welche Pflichtangaben
  noch fehlen (u. a. elektronische Adresse BT-34, Kontaktperson mit Telefon und E-Mail,
  USt-IdNr. oder Steuernummer, IBAN). Speichern ist auch unvollständig möglich.
- **Validierung** – IBAN mit Prüfziffer, BIC, deutsche USt-IdNr., PLZ, E-Mail-Adressen.
- **Kleinunternehmer (§ 19 UStG)** als Schalter für das spätere Rechnungsmodul.
- **Logo-Upload** – PNG, JPG oder WebP bis 5 MB. Das Bild wird geprüft, auf maximal
  1200 px verkleinert, um transparente Ränder beschnitten und als PNG ohne Metadaten
  gespeichert. Ein ersetztes oder entferntes Logo wird archiviert, nicht gelöscht.
- **Änderungsprotokoll** – jede Änderung mit Zeitpunkt sowie altem und neuem Wert.
- Beim ersten Start öffnet die Anwendung automatisch die Stammdaten.

## Leistungen

- **Drei Abrechnungsarten** – *Zeiteinheit* (Stunde, Minute, Tag, Woche, Monat),
  *Stück* oder *Pauschal*. Jede Einheit trägt den Einheitencode nach UN/ECE Rec. 20
  (z. B. `HUR`, `DAY`, `H87`, `LS`), den die X-Rechnung später benötigt.
- **Preis und Anzahl** – jede Leistung hat einen Netto-Einzelpreis (bis 4 Nachkommastellen)
  und eine Standard-Anzahl; der Gesamtpreis (netto/brutto) wird im Formular live berechnet.
  Beträge werden als Dezimalzahlen ohne Gleitkomma-Rundungsfehler gespeichert.
- **USt-Satz** 19, 7 oder 0 % je Leistung, optionale **Kategorie** zur Gruppierung.
- **Nummern** frei wählbar oder automatisch (`L0001`, …), eindeutig ohne Groß-/Kleinschreibung.
- **Import aus CSV oder Excel (.xlsx)** mit Vorschau vor dem Übernehmen:
  Spalten werden anhand der Überschriften erkannt (z. B. „Preis netto (€)“, „MwSt“, „Menge“),
  CSV-Trennzeichen und Zeichensatz automatisch bestimmt, deutsche Zahlen (`1.250,50`) gelesen.
  Fehlerhafte Zeilen werden mit Begründung angezeigt und nicht übernommen. Leistungen mit
  bereits vorhandener Nummer können aktualisiert oder übersprungen werden. Der Import läuft in
  einer Transaktion. Vorlagen gibt es als Excel und CSV.
- **Export** der (gefilterten) Liste als Excel oder CSV – im selben Format wieder importierbar.
- **Deaktivieren** statt Löschen für nicht mehr angebotene Leistungen; Löschen ist ebenfalls möglich.
- **Änderungsprotokoll** – jede Änderung (auch per Import) mit Zeitpunkt sowie altem und neuem Wert.

## Angebote

- **Leistungen aus der Liste wählen** – Suchfeld mit Tastatursteuerung; Einheit, Preis,
  Standardmenge und USt werden übernommen und können im Angebot angepasst werden.
- **Neue Leistungen direkt eingeben** – frei eingegebene Positionen werden beim Speichern
  automatisch ins Leistungsverzeichnis übernommen (abwählbar). Gibt es bereits eine Leistung
  mit gleicher Bezeichnung und Einheit, wird diese verknüpft statt doppelt angelegt.
- **Eindeutige Zuordnung zum Kunden** – jedes Angebot gehört zu genau einem Kunden
  (optional mit Ansprechpartner) und wird in den Kundendaten angezeigt.
- **Preisausweis nach Stammdaten** – Einstellung „Nettopreise zzgl. USt“ oder
  „Gesamtpreise inkl. USt“; Kleinunternehmer erhalten immer Gesamtpreise ohne USt
  mit Hinweis auf § 19 UStG. Mehrere Steuersätze (19 %/7 %) werden getrennt ausgewiesen.
- **Ablaufdatum** – Vorgabe aus den Stammdaten (30 Tage), Schnellwahl 14/30/60/90 Tage;
  abgelaufene Angebote werden automatisch gekennzeichnet.
- **Anmerkungen & Hinweise** – Freitext, der im Angebot unter den Leistungen erscheint.
- **Ablauf** – *Entwurf* (bearbeitbar, PDF-Vorschau mit Wasserzeichen) →
  *Abschließen* (PDF wird unter `kunden/<Kundennummer>/angebote/` abgelegt, das Angebot
  ist danach unveränderlich) → *angenommen* / *abgelehnt*. Kopien für neue Versionen.
  Abgeschlossene Angebote sind zusätzlich per Datenbank-Trigger gegen Änderung und Löschen
  geschützt.
- **Nummernkreis** `AN-<Jahr>-0001`, Änderungsprotokoll für alle Schritte.

## Rechnungen

- **Drei Wege zur Rechnung**
  1. *Aus einem angenommenen Angebot* – Knopf „Rechnung erstellen“ im Angebot übernimmt Kunde,
     Ansprechpartner, Betreff und Positionen (je Angebot eine Rechnung).
  2. *Leistungen für einen Kunden zusammenstellen* – Kunde wählen, Leistungen suchen und hinzufügen.
  3. *Rechnung an einen neuen Kunden* – Kundendaten direkt in der Rechnung erfassen; der Kunde wird
     beim Speichern in die Kundendatei übernommen und erhält eine Kundennummer.
  Frei eingegebene Positionen werden wie bei Angeboten ins Leistungsverzeichnis übernommen.
- **XRechnung 3.0 (UBL 2.1)** mit allen Pflichtangaben: Rechnungsnummer, -datum, Fälligkeit,
  Leistungsdatum oder -zeitraum, Käuferreferenz/Leitweg-ID (BT-10), Bestellnummer (BT-13),
  Verkäufer mit elektronischer Adresse, Kontakt, USt-IdNr./Steuernummer und Handelsregister,
  Käufer mit elektronischer Adresse (BT-49) und Anschrift, SEPA-Überweisung mit IBAN/BIC,
  Zahlungsbedingungen, Umsatzsteuer je Kategorie (S, E inkl. § 19 UStG, AE/Reverse Charge mit
  Befreiungsgrund) sowie alle Positionen mit Einheitencode (UN/ECE Rec. 20).
- **Prüfliste vor dem Abschließen** – zeigt fehlende Angaben (Stammdaten, Kundendaten, Rechnung)
  mit Link zur Stelle, an der sie ergänzt werden. Abschließen ist erst möglich, wenn alles passt.
- **Abschließen** vergibt die fortlaufende Rechnungsnummer `RE-<Jahr>-0001` (Entwürfe haben noch keine
  Nummer, gelöschte Entwürfe erzeugen also keine Lücken), erzeugt XML und PDF und legt beide
  **unter gleichem Namen** im Rechnungsordner des Kunden ab:
  `kunden/<Kundennummer>/rechnungen/<Datum>_<Rechnungsnummer>_<Kundennummer>.xml/.pdf`.
  Abgeschlossene Rechnungen sind unveränderlich (auch per Datenbank-Trigger geschützt).
- **Zahlungseingang** erfassen; überfällige Rechnungen werden gekennzeichnet, die Liste zeigt offene
  und überfällige Beträge.
- Rechnungen werden immer mit **Nettopreisen zzgl. USt** ausgewiesen (wie in der XRechnung).

### Stornorechnungen und Rechnungskorrekturen

Abgeschlossene Rechnungen bleiben unverändert. Fehler werden über einen eigenen, eindeutig
zugeordneten Beleg berichtigt (§ 31 Abs. 5 UStDV):

| | Stornorechnung | Rechnungskorrektur |
|---|---|---|
| Zweck | hebt die Rechnung vollständig auf | ersetzt die Rechnung mit geänderten Angaben |
| Anlegen | „Stornieren“ in der Rechnung, Grund angeben | „Korrigieren“ in der Rechnung, Positionen/Daten im Editor ändern, Grund angeben |
| Nummernkreis | `ST-<Jahr>-0001` | `KR-<Jahr>-0001` |
| XRechnung | Gutschrift, Typ 381 (UBL CreditNote) | Typ 384 (Corrected invoice) |
| Dateiname | `<Datum>_<Nr>_<Kundennr>_storno_zu_<Original-Nr>` | `<Datum>_<Nr>_<Kundennr>_korrektur_zu_<Original-Nr>` |

- **Eindeutige Zuordnung**: Jeder Folgebeleg verweist auf genau eine Originalrechnung desselben
  Kunden (BT-25/BT-26 in der XRechnung, Dateiname, Datenbank). Je Rechnung ist nur ein Folgebeleg
  möglich; die Datenbank verhindert Zuordnungen zu fremden Kunden, zu Entwürfen oder zu Stornos.
  Korrekturen lassen sich erneut korrigieren oder stornieren – die Seite „Zugehörige Belege“ zeigt die
  ganze Kette.
- **Status**: Das Original wird als *storniert* bzw. *korrigiert* gekennzeichnet und zählt nicht mehr zu
  den offenen Beträgen.
- **Bereits bezahlte Rechnungen**: Beim Storno wird der Betrag als offene Erstattung geführt. Bei der
  Korrektur wird die Zahlung angerechnet (BT-113) – es bleibt ein Restbetrag zu zahlen oder eine
  Erstattung an den Kunden.
- Storno und Korrektur übernehmen die steuerliche Behandlung des Originals (z. B. § 19 UStG).

### Offizielle Prüfung der XRechnung (optional, empfohlen)

Die erzeugten XML-Dateien lassen sich mit denselben Regeln prüfen wie beim KoSIT-Validator:
UBL-2.1-Schema, EN-16931-Geschäftsregeln (CEN) und nationale XRechnung-Regeln (KoSIT).

```bash
pip install -r requirements-pruefung.txt
python werkzeuge/pruefung_einrichten.py            # lädt die Regelwerke nach daten/xrechnung-pruefung
export XRECHNUNG_ARTEFAKTE=daten/xrechnung-pruefung    # Windows: set XRECHNUNG_ARTEFAKTE=daten\xrechnung-pruefung
```

Ist die Variable gesetzt, prüft die Anwendung jede XRechnung **vor dem Abschließen** und
verweigert ungültige Rechnungen. Einzelne Dateien prüfen:
`python werkzeuge/xrechnung_pruefen.py pfad/zur/rechnung.xml`

## Belegerfassung

Menü **Belege**. Alle Belege liegen in derselben Datenbank wie Kunden und Rechnungen – also auch
verschlüsselt, wenn die Datenbank verschlüsselt ist.

- **Erfassen** von Einnahmen und Ausgaben: Belegdatum, Partner, Rechnungsnummer, Beschreibung,
  EÜR-Kategorie, USt-Satz (19 %, 7 %, 0 %, mehrere). Netto *oder* Brutto eingeben – der Rest wird
  berechnet. Gutschriften mit Minus. Für Anlagevermögen zusätzlich die Nutzungsdauer (AfA-Tabelle).
- **Belegdatei** (PDF, JPG, PNG, XML, bis 20 MB) per Klick oder Drag & Drop. Sie wird unverändert unter
  ihrer SHA-256-Prüfsumme abgelegt (`<Datenordner>/belege/<Jahr>/…`); die Prüfsumme steht am Beleg.
- **GoBD:** Entwürfe sind frei änderbar und löschbar. **Festschreiben** vergibt eine fortlaufende Nummer
  (`E-2026-0001` Einnahmen, `A-2026-0001` Ausgaben); danach ist der Beleg unveränderbar und nicht
  löschbar – auch per Datenbank-Trigger. Korrekturen nur über **Storno** (Gegenbuchung mit Grund).
  Jede Aktion steht im Änderungsprotokoll.
- **Zahlungsdatum** (Zufluss/Abfluss) – kann auch nach dem Festschreiben einmalig nachgetragen werden.
- **XRechnung einlesen** (UBL und CII), z. B. Eingangsrechnungen von Lieferanten: legt einen Entwurf mit
  Partner, Nummer, Beträgen und Steuersätzen an; Doppelerfassungen werden erkannt.
- **Rechnungen aus dem Modul „Rechnungen“ werden automatisch übernommen** – als festgeschriebene
  Einnahme, ebenso später der Zahlungseingang. Das funktioniert auch in der Testversion (ohne XML):

  | Fall | Buchung |
  |---|---|
  | Rechnung | Einnahme über den Rechnungsbetrag, Zahlung = Zahlungseingang |
  | Storno einer **offenen** Rechnung | Gegenbuchung ohne Geldfluss – beide heben sich auf |
  | Storno einer **bezahlten** Rechnung | Erstattung (negative Einnahme), Zahlung = Erstattung |
  | Korrektur einer **offenen** Rechnung | Original wird storniert, Korrektur voll gebucht |
  | Korrektur einer **bezahlten** Rechnung | nur die Differenz wird gebucht |
  | Zahlung zurückgesetzt/geändert | Beleg storniert und mit aktuellem Stand neu gebucht |

- **Tags:** Jeder Beleg kann beliebig viele Tags tragen. Vorgegeben sind Fahrtkosten, Fremdleistung,
  Büromaterial, Internet/Kommunikation, Abo, Software, Waren, Reparaturen und sonstige Betriebskosten.
  Neue Tags lassen sich direkt beim Erfassen eintippen oder unter **Belege → Tags verwalten** anlegen,
  umbenennen und ausblenden (nie löschen – sie bleiben an alten Belegen). Ein Tag kann eine EÜR-Kategorie
  vorschlagen. Tags gehören nicht zum festgeschriebenen Inhalt und sind jederzeit änderbar (protokolliert).
- **Auswertung nach Tags** (**Belege → Auswertung nach Tags**): je Tag Anzahl, Netto, USt, Brutto und
  Anteil, dazu „(ohne Tag)“ und die Gesamtsumme (jeder Beleg einmal). Ausgaben oder Einnahmen, Jahr nach
  Beleg- oder Zahlungsdatum. **CSV-Export** mit allen Belegen gruppiert nach Tag, Zwischensumme je Tag
  und Übersicht der Summen. Ein Beleg mit mehreren Tags zählt bei jedem seiner Tags.
- **Auch ohne Festschreibung:** Auswertung nach Tags, Kennzahlen und EÜR berücksichtigen Entwürfe
  standardmäßig; „nur festgeschriebene Belege“ schaltet sie aus.
- **Jahre ab 2024:** Die Jahresauswahl beginnt mit 2024; vorgewählt ist das neueste Jahr mit Belegen.
  „danach nächsten Beleg erfassen“ übernimmt das Belegdatum – praktisch zum Nachtragen.
- **Liste** mit Filter (Jahr, Art, Status, Tag, „noch nicht bezahlt“), Suche, Kennzahlen und CSV-Export
  (mit Spalte „Tags“).

## Einnahmen-Überschuss-Rechnung (EÜR)

Menü **EÜR**, je Kalenderjahr, aus allen festgeschriebenen Belegen:

- **Zufluss-/Abflussprinzip** (§ 11 EStG): es zählt das Zahlungsdatum. Belege ohne Zahlung sind nicht
  enthalten – die Seite weist darauf hin. Entwürfe zählen standardmäßig mit (Auswertung ohne
  Festschreibung); „nur festgeschriebene Belege“ ergibt die endgültige EÜR.
- **Betriebseinnahmen** je Kategorie, vereinnahmte Umsatzsteuer, Erstattungen des Finanzamts.
- **Betriebsausgaben** je Kategorie, gezahlte Vorsteuer, an das Finanzamt gezahlte Umsatzsteuer.
- **Bewirtung** zu 70 % abziehbar (der nicht abziehbare Teil wird ausgewiesen; Vorsteuer voll).
- **GWG** (bis 800 € netto) im Jahr der Anschaffung, **Anlagevermögen** mit linearer AfA,
  monatsgenau, und **Anlagenverzeichnis** (AK, AfA bisher, AfA des Jahres, Restwert).
- **Kleinunternehmer** (§ 19 UStG, aus den Stammdaten): Ausgaben brutto, keine Vorsteuer.
- **Monatswerte** (Einnahmen, Ausgaben, USt, Vorsteuer, Zahllast) als Grundlage der
  Umsatzsteuer-Voranmeldung (Ist-Versteuerung).
- Export als **PDF** und **CSV**, Druckansicht.

Die Kategorien folgen den Gruppen der Anlage EÜR. Die Zuordnung zu den Formularzeilen des
jeweiligen Jahres bitte mit der Steuerberatung abstimmen. Die Software ersetzt keine Steuerberatung.

## Sicherheit

- Der Server ist nur lokal erreichbar (`127.0.0.1`).
- Formulare fremder Webseiten werden abgewiesen (Prüfung von `Origin`/`Referer` bei jedem POST).
- Belegdateien: nur PDF, JPG, PNG und XML; XML wird mit `defusedxml` gelesen (Schutz vor XML-Bomben).

## Tests

```bash
python -m pytest
```

Mit gesetztem `XRECHNUNG_ARTEFAKTE` werden zusätzlich alle in den Tests erzeugten XRechnungen
gegen die offiziellen Regeln geprüft.

## Rechtliches

© 2026 Ene Mene Media. Seit Version 1.4.0 steht „Buchhaltung für KMU“ unter **keiner Open-Source-Lizenz**
mehr (die MIT-Lizenz wurde entfernt); die Setups haben keine Lizenzseite. Bereits unter der MIT-Lizenz
veröffentlichte Stände bis 1.3.0 bleiben unter dieser Lizenz. Die Lizenzhinweise der mitgelieferten
Fremdsoftware stehen in `windows/DRITTANBIETER.txt` bzw. `macos/DRITTANBIETER.txt` und im Programmordner.
