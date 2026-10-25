# Kundenverwaltung mit X-Rechnung

Lokale Software für KMU zur Verwaltung von Kunden, Leistungen und Angeboten sowie
zur Erstellung von X-Rechnungen (mit PDF-Ansicht). Basis ist eine SQLite-Datenbank;
die Bedienung erfolgt im Browser.

## Stand

| Modul | Status |
|---|---|
| Kundenverwaltung | ✅ fertig |
| Firmenstammdaten (mit Logo) | ✅ fertig |
| Leistungsverzeichnis (mit Import/Export und Änderungsprotokoll) | ✅ fertig |
| Angebote (mit PDF) | ✅ fertig |
| Rechnungen mit XRechnung (XML) + PDF | ✅ fertig |
| Stornorechnungen und Rechnungskorrekturen | ✅ fertig |

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
        ├── angebote/        z. B. AN-2026-0001.pdf
        ├── rechnungen/      z. B. 2026-10-25_RE-2026-0001_K00001.xml + .pdf
        │                         2026-10-26_ST-2026-0001_K00001_storno_zu_RE-2026-0001.xml + .pdf
        │                         2026-10-26_KR-2026-0001_K00001_korrektur_zu_RE-2026-0002.xml + .pdf
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

## Tests

```bash
python -m pytest
```

Mit gesetztem `XRECHNUNG_ARTEFAKTE` werden zusätzlich alle in den Tests erzeugten XRechnungen
gegen die offiziellen Regeln geprüft.
