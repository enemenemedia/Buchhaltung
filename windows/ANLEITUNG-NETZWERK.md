# Buchhaltung für KMU – Netzwerkversion für Windows

Mehrere PCs im Büro arbeiten mit derselben Buchhaltung. Alle Daten liegen auf einem
**Netzlaufwerk** (Dateiserver, NAS oder freigegebener Ordner). Ein PC ist der **Server**, alle
anderen sind **Arbeitsplätze**, die die Buchhaltung im Browser öffnen.

```
  Arbeitsplatz-PC ─┐                      ┌──────────── Netzlaufwerk \\NAS\Buero ────────────┐
  Arbeitsplatz-PC ─┼── HTTPS (LAN) ──► Server-PC ──► Buchhaltung für KMU\                    │
  Arbeitsplatz-PC ─┘                      │        ├─ netzwerk-server.json  (Adresse)        │
                                          │        ├─ zertifikat-…crt       (für den Browser)│
                                          │        └─ Daten\  Datenbank, Belege, Rechnungen  │
                                          └──────────────────────────────────────────────────┘
```

Warum ein Server? Die Datenbank darf nie von mehreren PCs gleichzeitig direkt beschrieben werden –
auf Netzlaufwerken kann sie dabei beschädigt werden. Der Server ist das einzige Programm, das die
Daten öffnet; eine Sperrdatei verhindert, dass versehentlich ein zweiter Server startet.

Eine Setup-Datei für beide: **Buchhaltung-fuer-KMU-Netzwerk-Setup-1.3.0.exe**.
Administratorrechte sind nicht nötig (nur die Firewall-Freigabe fragt einmal nach).

## 1. Netzlaufwerk vorbereiten

- Eine Freigabe anlegen, z. B. `\\NAS\Buero` (Synology/QNAP: freigegebener Ordner; Windows-Server:
  Ordner freigeben).
- Der **Windows-Benutzer des Server-PCs** braucht Lese- und Schreibrechte.
- Die Arbeitsplätze brauchen nur **Lesezugriff auf den Ordner `Buchhaltung für KMU`** (Server-Adresse
  und Zertifikat). Den Unterordner `Daten` können (und sollten) Sie auf das Konto des Servers
  beschränken – die Mitarbeitenden greifen über den Browser zu, nicht über das Laufwerk. So kann
  niemand die Datenbank am Programm vorbei kopieren oder verändern.

## 2. Server installieren (ein PC)

1. Setup starten, Installationsart **Server** wählen.
2. **Netzlaufwerk** angeben:
   - Netzwerkpfad der Freigabe, z. B. `\\NAS\Buero`
   - optional einen **Laufwerksbuchstaben** (z. B. `N:`) – das Setup verbindet die Freigabe dauerhaft
     (`net use … /persistent:yes`), damit sie im Explorer erscheint
   - nur falls die Freigabe eigene Anmeldedaten hat (häufig bei NAS): Benutzername und Passwort –
     sie werden in der Windows-Anmeldeinformationsverwaltung gespeichert
3. Port (Standard 8443), **Autostart** und **Firewall-Freigabe** bestätigen.
4. Nach der Installation öffnet sich der Browser: den ersten **Administrator** anlegen (am Server-PC
   geht das ohne Einrichtungscode), dann unter **Benutzer** die Konten für alle Mitarbeitenden.

| Rolle | darf |
|---|---|
| Administrator | alles, dazu Benutzer verwalten, Stammdaten ändern, Datenbank verschlüsseln |
| Buchhaltung | Kunden, Angebote, Rechnungen, Belege erfassen/ändern/festschreiben, EÜR |
| Nur lesen | alles ansehen und herunterladen (PDF, CSV), nichts ändern |

Jede Änderung wird mit dem Anmeldenamen im Änderungsprotokoll festgehalten.

**Der Server-PC** muss während der Arbeitszeit eingeschaltet und mit einem Benutzer angemeldet sein
(Bildschirm sperren ist in Ordnung, Abmelden beendet den Server). Energiesparen/Ruhezustand für die
Arbeitszeit ausschalten. Mit Autostart startet der Server bei jeder Windows-Anmeldung von selbst.

Startmenü › *Buchhaltung für KMU – Netzwerk*: Buchhaltung öffnen, Server-Status, Server beenden,
Datenordner öffnen.

## 3. Arbeitsplätze installieren (alle anderen PCs)

1. Dasselbe Setup starten, Installationsart **Arbeitsplatz** wählen.
2. Dasselbe Netzlaufwerk angeben (Laufwerksbuchstabe und Zugangsdaten wie beim Server).
3. Beim ersten Start fragt Windows einmal, ob dem **Zertifikat** „Buchhaltung für KMU – lokale CA“
   vertraut werden soll → **Ja**. Danach öffnet sich die Anmeldung ohne Browser-Warnung.

Die Verknüpfung „Buchhaltung für KMU – Netzwerk“ findet den Server jedes Mal neu (auch wenn sich
seine IP-Adresse ändert). Läuft er nicht, erscheint ein Hinweis, welcher PC einzuschalten ist.
Firefox nutzt eigene Zertifikate: dort einmal die Warnung bestätigen oder das Zertifikat
`Buchhaltung für KMU\zertifikat-buchhaltung-kmu.crt` unter *Einstellungen › Zertifikate › Zertifizierungsstellen* importieren.

## 4. Datensicherung

Gesichert werden muss der Ordner `Buchhaltung für KMU` auf dem Netzlaufwerk (am besten mit der
Sicherung des NAS/Servers, nachts). Auf dem Server-PC liegen zusätzlich die Schlüssel für das
Zertifikat und die Anmeldungen (`%LOCALAPPDATA%\Buchhaltung-KMU-Netzwerk`); gehen sie verloren,
erzeugt der Server neue – die Arbeitsplätze bestätigen dann das neue Zertifikat einmal.
Aufbewahrungspflicht für Buchungsbelege: 10 Jahre.

**Verschlüsselte Datenbank:** Verschlüsselt ein Administrator die Datenbank (Einstellungen), muss
sie nach jedem Serverstart einmal mit dem Datenbank-Passwort entsperrt werden. Empfohlen, wenn die
Mitarbeitenden Lesezugriff auf das Netzlaufwerk haben.

## 5. Umzug

- **Von der Einzelplatzversion:** Einzelplatzversion beenden und den Inhalt ihres Datenordners
  (Standard `Dokumente\Buchhaltung für KMU`) nach `\\NAS\Buero\Buchhaltung für KMU\Daten` kopieren,
  dann den Server installieren. Ältere Protokolleinträge erscheinen ohne Benutzernamen.
- **Server auf einen anderen PC verlegen:** Auf dem alten Server-PC „Server beenden“ und die
  Netzwerkversion deinstallieren; auf dem neuen PC als Server installieren. Die Daten bleiben auf
  dem Netzlaufwerk; Benutzer und Passwörter bleiben erhalten (sie stehen in der Datenbank).
  Alle Anmeldungen enden einmal, die Arbeitsplätze bestätigen das neue Zertifikat.

## 6. Fehlerbehebung

| Meldung | Ursache / Lösung |
|---|---|
| „Netzlaufwerk nicht erreichbar“ | NAS/Server aus, Pfad falsch, Zugangsdaten fehlen → im Explorer `\\NAS\Buero` öffnen und prüfen |
| „Server läuft bereits auf dem PC …“ | Es gibt schon einen Server für diese Daten. Diesen PC als Arbeitsplatz verwenden |
| Arbeitsplatz: „Server-PC antwortet nicht“ | Server-PC einschalten/anmelden; Firewall auf dem Server-PC: Regel „Buchhaltung fuer KMU (Netzwerk-Server)“, Netzwerkprofil „Privat“ |
| Browser-Warnung zum Zertifikat | Arbeitsplatz-Verknüpfung einmal starten und das Zertifikat bestätigen |
| Passwort vergessen | Ein Administrator setzt unter *Benutzer* ein neues |

Firewall von Hand (als Administrator, auf dem Server-PC):
```
netsh advfirewall firewall add rule name="Buchhaltung fuer KMU (Netzwerk-Server)" dir=in action=allow protocol=TCP localport=8443 profile=private,domain
```

Unbeaufsichtigte Installation (Softwareverteilung):
```
Buchhaltung-fuer-KMU-Netzwerk-Setup-1.3.0.exe /S /MODUS=arbeitsplatz /FREIGABE=\\NAS\Buero /LAUFWERK=N:
```
Protokoll des Programms: `%LOCALAPPDATA%\Buchhaltung-KMU-Netzwerk\protokoll\netzwerk.log`.
