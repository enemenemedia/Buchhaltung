# Buchhaltung für KMU – Netzwerkversion

Die Netzwerkversion läuft als Docker-Container auf einem Linux-Server oder einer NAS
(Synology, QNAP, Unraid …). Alle Arbeitsplätze im Büro öffnen sie im Browser – es wird
nichts auf den PCs installiert. Funktionen und Datenbank sind dieselben wie in der
Einzelplatzversion; dazu kommen:

- **Anmeldung mit eigenen Benutzern** und drei Rollen
  | Rolle | darf |
  |---|---|
  | Administrator | alles, dazu Benutzer verwalten, Stammdaten ändern, Datenbank verschlüsseln |
  | Buchhaltung | Kunden, Angebote, Rechnungen, Belege erfassen/ändern/festschreiben, EÜR |
  | Nur lesen | alles ansehen und herunterladen (PDF, CSV), nichts ändern |
- **Änderungsprotokoll mit Benutzer:** Jede Änderung zeigt, wer sie vorgenommen hat
  (Detailseiten und „Benutzer › Änderungsprotokoll“).
- **HTTPS** mit selbst erzeugtem Zertifikat; Passwörter und Daten gehen verschlüsselt durchs Netz.
- **Nur im lokalen Netz erreichbar:** Anfragen von Adressen außerhalb privater Netze
  (192.168.x.x, 10.x.x.x, 172.16–31.x.x) werden abgewiesen.

## 1. Installieren

Voraussetzung: Docker mit „docker compose“ (Synology: *Container Manager*, QNAP: *Container Station*).

1. Ordner auf dem Server anlegen, z. B. `/volume1/docker/buchhaltung`, und die Datei
   `docker-compose.yml` aus diesem Paket hineinlegen.
2. In `docker-compose.yml` anpassen:
   - `BUCHHALTUNG_HOSTNAMEN`: Name **und** IP-Adresse, unter denen die PCs den Server aufrufen,
     z. B. `"nas,nas.local,192.168.178.20"`. Daraus entsteht das HTTPS-Zertifikat.
   - `PUID`/`PGID`: Benutzer, dem der Datenordner gehören soll
     (Synology: `id` im Terminal zeigt z. B. 1026/100).
3. Starten:
   ```sh
   docker compose up -d
   docker compose logs          # zeigt den Einrichtungscode
   ```
   Synology Container Manager: *Projekt › Erstellen*, Ordner wählen, `docker-compose.yml` verwenden.

Das Image `ghcr.io/enemenemedia/buchhaltung` gibt es für Intel/AMD (amd64) und ARM (arm64).
Ohne Internetzugang zum Image lässt es sich aus den Quellen bauen: im Paket liegt alles dafür;
in `docker-compose.yml` die Zeile `image:` durch `build: { context: ., dockerfile: netzwerk/Dockerfile }` ersetzen.

## 2. Einrichten (einmalig)

1. Im Browser `https://192.168.178.20:8443/` öffnen (Ihre Adresse). Die Warnung zum Zertifikat
   beim ersten Mal bestätigen – oder gleich Schritt 3 erledigen.
2. Den **Einrichtungscode** eingeben (steht in `docker compose logs` und in der Datei
   `daten/einrichtungscode.txt`) und den ersten **Administrator** anlegen.
3. Unter **Benutzer** die Konten für alle Mitarbeitenden anlegen und die Passwörter persönlich
   weitergeben. Jeder kann sein Passwort danach über seinen Namen oben rechts ändern.

## 3. Zertifikat einmalig vertrauen (empfohlen)

Damit der Browser nicht bei jedem Aufruf warnt, das Zertifikat der lokalen Zertifizierungsstelle
auf jedem PC einmal importieren. Herunterladen über den Link auf der Anmeldeseite
(`https://<server>:8443/zertifikat`).

- **Windows:** Datei doppelklicken › *Zertifikat installieren* › *Lokaler Computer* ›
  *Alle Zertifikate in folgendem Speicher speichern* › **Vertrauenswürdige Stammzertifizierungsstellen**.
  Danach Browser neu starten (gilt für Edge und Chrome).
- **macOS:** Datei doppelklicken (Schlüsselbundverwaltung) › Zertifikat öffnen › *Vertrauen* ›
  **Immer vertrauen**.
- **Firefox:** *Einstellungen › Datenschutz & Sicherheit › Zertifikate anzeigen › Zertifizierungsstellen ›
  Importieren* › „Dieser CA vertrauen, um Websites zu identifizieren“.

Das Serverzertifikat wird automatisch erneuert (Laufzeit gut zwei Jahre, Erneuerung beim Neustart
30 Tage vor Ablauf oder wenn sich `BUCHHALTUNG_HOSTNAMEN` ändert). Die Zertifizierungsstelle bleibt
gleich – ein erneuter Import ist nicht nötig. Den privaten Schlüssel `daten/zertifikat/ca.key` nie
weitergeben. Eigenes Zertifikat statt des erzeugten: `BUCHHALTUNG_ZERTIFIKAT` und
`BUCHHALTUNG_SCHLUESSEL` auf PEM-Dateien im Container zeigen lassen.

## 4. Datensicherung

Alles liegt im Ordner `daten` (Datenbank `xrechnung.db`, Belegdateien, Rechnungs-PDF/-XML,
Zertifikate). Sichern Sie diesen Ordner regelmäßig, z. B. mit Hyper Backup – am sichersten bei
gestopptem Container (`docker compose stop`, sichern, `docker compose start`) oder nachts.
Die Aufbewahrungspflicht (10 Jahre für Buchungsbelege) gilt auch für die Sicherung.

**Verschlüsselte Datenbank:** Verschlüsselt ein Administrator die Datenbank (Einstellungen),
muss sie nach jedem Neustart des Containers einmal mit dem Datenbank-Passwort entsperrt werden,
bevor sich jemand anmelden kann. Ohne dieses Passwort sind die Daten verloren.

## 5. Aktualisieren

```sh
docker compose pull && docker compose up -d
```
Die Datenbank wird beim Start automatisch auf den neuen Stand gebracht. Vorher sichern.

## 6. Von der Einzelplatzversion umziehen

1. Einzelplatzversion beenden.
2. Den Inhalt ihres Datenordners (Standard: `Dokumente\Buchhaltung für KMU`) in den Ordner
   `daten` auf dem Server kopieren.
3. Container starten und wie unter 2. den Administrator anlegen. Ältere Einträge im
   Änderungsprotokoll erscheinen ohne Benutzernamen („–“).

## Einstellungen (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `BUCHHALTUNG_HOSTNAMEN` | – | Namen/IP-Adressen des Servers für das Zertifikat (Komma-getrennt) |
| `BUCHHALTUNG_NETZE` | private Netze | erlaubte Netze, z. B. `192.168.178.0/24` |
| `BUCHHALTUNG_PORT` | `8443` | Port im Container |
| `BUCHHALTUNG_SITZUNG_STUNDEN` | `10` | Abmeldung nach so vielen Stunden |
| `BUCHHALTUNG_THREADS` | `8` | gleichzeitige Anfragen |
| `PUID` / `PGID` | `1000` | Besitzer des Datenordners |
| `TZ` | `Europe/Berlin` | Zeitzone für Protokoll und Belege |

## Sicherheitshinweise

- Den Port **nicht** im Router freigeben (keine Portweiterleitung). Für Zugriff von unterwegs ein
  VPN zum Büronetz verwenden (z. B. das VPN der FRITZ!Box oder Synology VPN Server).
- Ein Reverse-Proxy vor dem Container (Synology „Anmeldeportal“) wird nicht benötigt; falls doch
  verwendet, `BUCHHALTUNG_NETZE` auf die Adresse des Proxys setzen und den Proxy selbst auf das lokale Netz beschränken.
- Nach fünf falschen Passwörtern ist ein Konto fünf Minuten gesperrt. Ein vergessenes Passwort setzt
  ein Administrator unter *Benutzer* neu. Benutzer werden nicht gelöscht, sondern deaktiviert.
