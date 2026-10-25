# Kundenverwaltung mit X-Rechnung

Lokale Software für KMU zur Verwaltung von Kunden, Leistungen und Angeboten sowie
zur Erstellung von X-Rechnungen (mit PDF-Ansicht). Basis ist eine SQLite-Datenbank;
die Bedienung erfolgt im Browser.

## Stand

| Modul | Status |
|---|---|
| Kundenverwaltung | ✅ fertig |
| Firmenstammdaten (mit Logo) | ✅ fertig |
| Leistungsverzeichnis (mit Änderungsprotokoll) | geplant |
| Angebote | geplant |
| X-Rechnung + PDF | geplant |

## Starten

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Danach im Browser `http://127.0.0.1:5000` öffnen.

Alle Daten liegen im Ordner `daten/` (änderbar über die Umgebungsvariable `XRECHNUNG_DATEN`):

```
daten/
├── xrechnung.db             SQLite-Datenbank
├── stammdaten/
│   ├── logo.png             aktuelles Firmenlogo
│   └── logo_archiv/         frühere Logos
└── kunden/
    └── K00001/              ein Ordner je Kundennummer
        ├── angebote/
        ├── rechnungen/
        └── dokumente/
```

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

## Tests

```bash
python -m pytest
```
