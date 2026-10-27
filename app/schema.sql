-- Schema der Kundenverwaltung (SQLite)
-- Wird beim Start idempotent ausgeführt.

PRAGMA foreign_keys = ON;

-- Kunden ---------------------------------------------------------------
-- Felder orientieren sich an den Käuferangaben der XRechnung (BG-7 Buyer):
--   firma            -> BT-44 Buyer name
--   strasse          -> BT-50 Buyer address line 1
--   adresszusatz     -> BT-51 Buyer address line 2
--   plz              -> BT-53 Buyer post code
--   ort              -> BT-52 Buyer city
--   land             -> BT-55 Buyer country code (ISO 3166-1 alpha-2)
--   ust_id           -> BT-48 Buyer VAT identifier
--   leitweg_id       -> BT-10 Buyer reference (Pflicht bei öffentlichen Auftraggebern)
CREATE TABLE IF NOT EXISTS kunden (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kundennummer    TEXT    NOT NULL UNIQUE,
    firma           TEXT    NOT NULL,
    strasse         TEXT    NOT NULL DEFAULT '',
    adresszusatz    TEXT    NOT NULL DEFAULT '',
    plz             TEXT    NOT NULL DEFAULT '',
    ort             TEXT    NOT NULL DEFAULT '',
    land            TEXT    NOT NULL DEFAULT 'DE',
    ust_id          TEXT    NOT NULL DEFAULT '',
    steuernummer    TEXT    NOT NULL DEFAULT '',
    leitweg_id      TEXT    NOT NULL DEFAULT '',
    email           TEXT    NOT NULL DEFAULT '',
    telefon         TEXT    NOT NULL DEFAULT '',
    notizen         TEXT    NOT NULL DEFAULT '',
    erstellt_am     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    geaendert_am    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- Archivierung: archivierte Kunden bleiben vollständig erhalten.
    archiviert_am   TEXT,
    -- Erster Tag, an dem der Kunde endgültig gelöscht werden darf.
    -- Wird beim Archivieren berechnet (Aufbewahrungsfrist 10 Jahre,
    -- Fristbeginn mit Ablauf des Kalenderjahres, § 147 AO / § 257 HGB).
    loeschbar_ab    TEXT
);

CREATE INDEX IF NOT EXISTS idx_kunden_firma ON kunden (firma);

-- Firmenstammdaten (genau eine Zeile, id = 1) ----------------------------
-- Felder orientieren sich an den Verkäuferangaben der XRechnung (BG-4 Seller):
--   firma            -> BT-27 Seller name
--   handelsname      -> BT-28 Seller trading name
--   strasse/...      -> BG-5 Seller postal address (BT-35 .. BT-40)
--   ust_id           -> BT-31 Seller VAT identifier
--   steuernummer     -> BT-32 Seller tax registration identifier
--   registernummer   -> BT-30 Seller legal registration identifier
--   elektronische_adresse -> BT-34 Seller electronic address (Pflicht in XRechnung)
--   kontakt_*        -> BG-6 Seller contact (BT-41 .. BT-43, Pflicht in XRechnung)
--   iban/bic         -> BT-84 / BT-86 Payment account
-- Das Logo liegt als Datei unter <daten>/stammdaten/logo.png.
CREATE TABLE IF NOT EXISTS stammdaten (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    firma                   TEXT    NOT NULL DEFAULT '',
    handelsname             TEXT    NOT NULL DEFAULT '',
    inhaber                 TEXT    NOT NULL DEFAULT '',  -- Inhaber bzw. Geschäftsführung
    strasse                 TEXT    NOT NULL DEFAULT '',
    adresszusatz            TEXT    NOT NULL DEFAULT '',
    plz                     TEXT    NOT NULL DEFAULT '',
    ort                     TEXT    NOT NULL DEFAULT '',
    land                    TEXT    NOT NULL DEFAULT 'DE',
    telefon                 TEXT    NOT NULL DEFAULT '',
    email                   TEXT    NOT NULL DEFAULT '',
    website                 TEXT    NOT NULL DEFAULT '',
    elektronische_adresse   TEXT    NOT NULL DEFAULT '',
    kontakt_name            TEXT    NOT NULL DEFAULT '',
    kontakt_telefon         TEXT    NOT NULL DEFAULT '',
    kontakt_email           TEXT    NOT NULL DEFAULT '',
    ust_id                  TEXT    NOT NULL DEFAULT '',
    steuernummer            TEXT    NOT NULL DEFAULT '',
    finanzamt               TEXT    NOT NULL DEFAULT '',
    registergericht         TEXT    NOT NULL DEFAULT '',
    registernummer          TEXT    NOT NULL DEFAULT '',
    kleinunternehmer        INTEGER NOT NULL DEFAULT 0,   -- § 19 UStG
    kontoinhaber            TEXT    NOT NULL DEFAULT '',
    iban                    TEXT    NOT NULL DEFAULT '',
    bic                     TEXT    NOT NULL DEFAULT '',
    bank                    TEXT    NOT NULL DEFAULT '',
    zahlungsziel_tage       INTEGER NOT NULL DEFAULT 14,
    -- Preisausweis in Angeboten: 'netto' (zzgl. USt, Geschäftskunden) oder
    -- 'brutto' (Gesamtpreise inkl. USt, Privatkunden). Bei Kleinunternehmern
    -- werden immer Gesamtpreise ohne USt ausgewiesen.
    preisausweis            TEXT    NOT NULL DEFAULT 'netto' CHECK (preisausweis IN ('netto', 'brutto')),
    angebot_gueltigkeit_tage INTEGER NOT NULL DEFAULT 30,
    logo_aktualisiert_am    TEXT,
    geaendert_am            TEXT
);

INSERT OR IGNORE INTO stammdaten (id) VALUES (1);

-- Leistungsverzeichnis --------------------------------------------------
-- art:          'zeit' | 'stueck' | 'pauschal'
-- einheit_code: Einheitencode nach UN/ECE Rec. 20 für die XRechnung (BT-130),
--               z. B. HUR = Stunde, DAY = Tag, H87 = Stück, LS = Pauschale
-- preis:        Netto-Einzelpreis je Einheit als Dezimaltext (z. B. '85.00')
-- anzahl:       Standardmenge, die beim Übernehmen in Angebot/Rechnung vorbelegt wird
-- Beträge werden als Text gespeichert und in Python mit Decimal gerechnet,
-- damit keine Rundungsfehler durch Gleitkommazahlen entstehen.
CREATE TABLE IF NOT EXISTS leistungen (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nummer          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    bezeichnung     TEXT    NOT NULL,
    beschreibung    TEXT    NOT NULL DEFAULT '',
    kategorie       TEXT    NOT NULL DEFAULT '',
    art             TEXT    NOT NULL CHECK (art IN ('zeit', 'stueck', 'pauschal')),
    einheit_code    TEXT    NOT NULL,
    preis           TEXT    NOT NULL DEFAULT '0',
    anzahl          TEXT    NOT NULL DEFAULT '1',
    ust_satz        TEXT    NOT NULL DEFAULT '19',
    aktiv           INTEGER NOT NULL DEFAULT 1,
    erstellt_am     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    geaendert_am    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_leistungen_bezeichnung ON leistungen (bezeichnung);

-- Ansprechpartner (beliebig viele je Kunde) ------------------------------
CREATE TABLE IF NOT EXISTS ansprechpartner (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kunde_id            INTEGER NOT NULL REFERENCES kunden (id) ON DELETE CASCADE,
    anrede              TEXT    NOT NULL DEFAULT '',
    titel               TEXT    NOT NULL DEFAULT '',
    vorname             TEXT    NOT NULL DEFAULT '',
    nachname            TEXT    NOT NULL DEFAULT '',
    funktion            TEXT    NOT NULL DEFAULT '',
    email               TEXT    NOT NULL DEFAULT '',
    telefon             TEXT    NOT NULL DEFAULT '',
    mobil               TEXT    NOT NULL DEFAULT '',
    ist_hauptkontakt    INTEGER NOT NULL DEFAULT 0,
    position            INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ansprechpartner_kunde ON ansprechpartner (kunde_id);

-- Angebote --------------------------------------------------------------
-- status: 'entwurf' (bearbeitbar) -> 'offen' (abgeschlossen, PDF erstellt)
--         -> 'angenommen' | 'abgelehnt'. „Abgelaufen“ ergibt sich aus gueltig_bis.
-- Empfänger, Preisausweis und Positionen werden als Kopie gespeichert, damit ein
-- abgeschlossenes Angebot unverändert bleibt, auch wenn sich Kunden-, Stamm- oder
-- Leistungsdaten später ändern.
CREATE TABLE IF NOT EXISTS angebote (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nummer              TEXT    NOT NULL UNIQUE,
    kunde_id            INTEGER NOT NULL REFERENCES kunden (id) ON DELETE CASCADE,
    ansprechpartner_id  INTEGER REFERENCES ansprechpartner (id) ON DELETE SET NULL,
    status              TEXT    NOT NULL DEFAULT 'entwurf'
                        CHECK (status IN ('entwurf', 'offen', 'angenommen', 'abgelehnt')),
    datum               TEXT    NOT NULL,
    gueltig_bis         TEXT    NOT NULL,
    betreff             TEXT    NOT NULL DEFAULT '',
    anmerkungen         TEXT    NOT NULL DEFAULT '',
    preisausweis        TEXT    NOT NULL DEFAULT 'netto' CHECK (preisausweis IN ('netto', 'brutto')),
    kleinunternehmer    INTEGER NOT NULL DEFAULT 0,
    empfaenger          TEXT    NOT NULL DEFAULT '',   -- Anschriftblock (mehrzeilig)
    summe_netto         TEXT    NOT NULL DEFAULT '0',
    summe_ust           TEXT    NOT NULL DEFAULT '0',
    summe_gesamt        TEXT    NOT NULL DEFAULT '0',
    pdf_datei           TEXT,                          -- relativ zum Datenverzeichnis
    abgeschlossen_am    TEXT,
    erstellt_am         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    geaendert_am        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (gueltig_bis >= datum)
);

CREATE INDEX IF NOT EXISTS idx_angebote_kunde ON angebote (kunde_id);

CREATE TABLE IF NOT EXISTS angebot_positionen (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    angebot_id      INTEGER NOT NULL REFERENCES angebote (id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    leistung_id     INTEGER REFERENCES leistungen (id) ON DELETE SET NULL,
    leistungsnummer TEXT    NOT NULL DEFAULT '',
    bezeichnung     TEXT    NOT NULL,
    beschreibung    TEXT    NOT NULL DEFAULT '',
    einheit_code    TEXT    NOT NULL,
    menge           TEXT    NOT NULL,
    einzelpreis     TEXT    NOT NULL,                  -- netto je Einheit
    ust_satz        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_angebot_positionen ON angebot_positionen (angebot_id, position);

-- Abgeschlossene Angebote sind unveränderlich: nur Status-Übergänge sind erlaubt.
CREATE TRIGGER IF NOT EXISTS angebote_schreibschutz
BEFORE UPDATE ON angebote
FOR EACH ROW
WHEN OLD.status <> 'entwurf' AND (
       NEW.nummer IS NOT OLD.nummer OR NEW.kunde_id IS NOT OLD.kunde_id
    OR NEW.datum IS NOT OLD.datum OR NEW.gueltig_bis IS NOT OLD.gueltig_bis
    OR NEW.betreff IS NOT OLD.betreff OR NEW.anmerkungen IS NOT OLD.anmerkungen
    OR NEW.preisausweis IS NOT OLD.preisausweis OR NEW.kleinunternehmer IS NOT OLD.kleinunternehmer
    OR NEW.empfaenger IS NOT OLD.empfaenger OR NEW.summe_gesamt IS NOT OLD.summe_gesamt
    OR NEW.summe_netto IS NOT OLD.summe_netto OR NEW.summe_ust IS NOT OLD.summe_ust
    OR NEW.pdf_datei IS NOT OLD.pdf_datei OR NEW.status = 'entwurf')
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Angebote können nicht geändert werden.');
END;

-- Abgeschlossene Angebote dürfen nur zusammen mit dem Kunden (nach Ablauf
-- der Aufbewahrungsfrist) gelöscht werden. Beim kaskadierenden Löschen ist
-- der Kunde bereits entfernt, daher greift die Sperre dann nicht.
CREATE TRIGGER IF NOT EXISTS angebote_loeschschutz
BEFORE DELETE ON angebote
FOR EACH ROW
WHEN OLD.status <> 'entwurf' AND EXISTS (SELECT 1 FROM kunden WHERE id = OLD.kunde_id)
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Angebote können nicht gelöscht werden.');
END;

CREATE TRIGGER IF NOT EXISTS angebot_positionen_schutz_insert
BEFORE INSERT ON angebot_positionen
FOR EACH ROW
WHEN (SELECT status FROM angebote WHERE id = NEW.angebot_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Angebote können nicht geändert werden.');
END;

CREATE TRIGGER IF NOT EXISTS angebot_positionen_schutz_update
BEFORE UPDATE ON angebot_positionen
FOR EACH ROW
WHEN (SELECT status FROM angebote WHERE id = OLD.angebot_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Angebote können nicht geändert werden.');
END;

CREATE TRIGGER IF NOT EXISTS angebot_positionen_schutz_delete
BEFORE DELETE ON angebot_positionen
FOR EACH ROW
WHEN (SELECT status FROM angebote WHERE id = OLD.angebot_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Angebote können nicht geändert werden.');
END;


-- Rechnungen ------------------------------------------------------------
-- status: 'entwurf' (bearbeitbar, noch ohne Rechnungsnummer)
--         -> 'offen' (abgeschlossen: Nummer vergeben, XRechnung-XML und PDF abgelegt)
--         -> 'bezahlt'. „Überfällig“ ergibt sich aus faellig_am.
-- Die Rechnungsnummer wird erst beim Abschließen vergeben, damit der Nummernkreis
-- lückenlos bleibt (gelöschte Entwürfe erzeugen keine Lücken).
-- verkaeufer/kaeufer: Kopie der Stamm- bzw. Kundendaten zum Zeitpunkt des
-- Abschlusses (JSON), aus denen XML und PDF erzeugt wurden.
CREATE TABLE IF NOT EXISTS rechnungen (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nummer              TEXT    UNIQUE,                     -- BT-1, ab Abschluss
    kunde_id            INTEGER NOT NULL REFERENCES kunden (id) ON DELETE CASCADE,
    ansprechpartner_id  INTEGER REFERENCES ansprechpartner (id) ON DELETE SET NULL,
    angebot_id          INTEGER REFERENCES angebote (id) ON DELETE SET NULL,
    status              TEXT    NOT NULL DEFAULT 'entwurf' CHECK (status IN ('entwurf', 'offen', 'bezahlt')),
    -- Belegart: 'rechnung' (380), 'korrektur' (384, ersetzt die Originalrechnung) oder
    -- 'storno' (381, Gutschrift über den vollen Betrag der Originalrechnung).
    art                 TEXT    NOT NULL DEFAULT 'rechnung' CHECK (art IN ('rechnung', 'korrektur', 'storno')),
    bezug_id            INTEGER REFERENCES rechnungen (id),   -- Originalrechnung (BT-25/26)
    korrektur_grund     TEXT    NOT NULL DEFAULT '',
    vorausbezahlt       TEXT    NOT NULL DEFAULT '0',         -- BT-113: auf das Original gezahlt
    rechnungsdatum      TEXT    NOT NULL,                   -- BT-2
    leistung_von        TEXT    NOT NULL,                   -- BT-72 bzw. BT-73
    leistung_bis        TEXT,                               -- BT-74 (Leistungszeitraum)
    zahlungsziel_tage   INTEGER NOT NULL DEFAULT 14,
    faellig_am          TEXT    NOT NULL,                   -- BT-9
    betreff             TEXT    NOT NULL DEFAULT '',
    anmerkungen         TEXT    NOT NULL DEFAULT '',        -- BT-22
    kaeufer_referenz    TEXT    NOT NULL DEFAULT '',        -- BT-10 (Leitweg-ID o. Ä.)
    bestellnummer       TEXT    NOT NULL DEFAULT '',        -- BT-13
    befreiung_art       TEXT    NOT NULL DEFAULT 'E' CHECK (befreiung_art IN ('E', 'AE')),
    befreiung_grund     TEXT    NOT NULL DEFAULT '',        -- BT-120 für 0-%-Positionen
    kleinunternehmer    INTEGER NOT NULL DEFAULT 0,
    empfaenger          TEXT    NOT NULL DEFAULT '',
    verkaeufer          TEXT,                               -- JSON, ab Abschluss
    kaeufer             TEXT,                               -- JSON, ab Abschluss
    summe_netto         TEXT    NOT NULL DEFAULT '0',
    summe_ust           TEXT    NOT NULL DEFAULT '0',
    summe_gesamt        TEXT    NOT NULL DEFAULT '0',
    dateiname           TEXT,                               -- ohne Endung, relativ zum Datenverzeichnis
    -- Abgeschlossen in der Testversion: XRechnung (XML) fehlt noch. JSON mit Angaben zum
    -- Zeitpunkt des Abschlusses, damit die XML nach dem Lizenzieren nachgeholt werden kann.
    xml_ausstehend      TEXT,
    abgeschlossen_am    TEXT,
    bezahlt_am          TEXT,
    erstellt_am         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    geaendert_am        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (leistung_bis IS NULL OR leistung_bis >= leistung_von),
    CHECK (faellig_am >= rechnungsdatum),
    CHECK (status = 'entwurf' OR nummer IS NOT NULL),
    CHECK ((art = 'rechnung') = (bezug_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_rechnungen_kunde ON rechnungen (kunde_id);
CREATE INDEX IF NOT EXISTS idx_rechnungen_angebot ON rechnungen (angebot_id);
-- Je Originalrechnung höchstens ein Storno bzw. eine Korrektur (auch als Entwurf).
CREATE UNIQUE INDEX IF NOT EXISTS idx_rechnungen_bezug ON rechnungen (bezug_id) WHERE bezug_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS rechnung_positionen (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rechnung_id     INTEGER NOT NULL REFERENCES rechnungen (id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    leistung_id     INTEGER REFERENCES leistungen (id) ON DELETE SET NULL,
    leistungsnummer TEXT    NOT NULL DEFAULT '',
    bezeichnung     TEXT    NOT NULL,
    beschreibung    TEXT    NOT NULL DEFAULT '',
    einheit_code    TEXT    NOT NULL,
    menge           TEXT    NOT NULL,
    einzelpreis     TEXT    NOT NULL,
    ust_satz        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rechnung_positionen ON rechnung_positionen (rechnung_id, position);

-- Abgeschlossene Rechnungen sind unveränderlich (GoBD). Erlaubt sind nur der
-- Wechsel zwischen „offen“ und „bezahlt“ samt Zahlungsdatum.
DROP TRIGGER IF EXISTS rechnungen_schreibschutz;
CREATE TRIGGER rechnungen_schreibschutz
BEFORE UPDATE ON rechnungen
FOR EACH ROW
WHEN OLD.status <> 'entwurf' AND (
       NEW.nummer IS NOT OLD.nummer OR NEW.kunde_id IS NOT OLD.kunde_id
    OR NEW.rechnungsdatum IS NOT OLD.rechnungsdatum OR NEW.leistung_von IS NOT OLD.leistung_von
    OR NEW.leistung_bis IS NOT OLD.leistung_bis OR NEW.faellig_am IS NOT OLD.faellig_am
    OR NEW.zahlungsziel_tage IS NOT OLD.zahlungsziel_tage OR NEW.betreff IS NOT OLD.betreff
    OR NEW.anmerkungen IS NOT OLD.anmerkungen OR NEW.kaeufer_referenz IS NOT OLD.kaeufer_referenz
    OR NEW.bestellnummer IS NOT OLD.bestellnummer OR NEW.befreiung_art IS NOT OLD.befreiung_art
    OR NEW.befreiung_grund IS NOT OLD.befreiung_grund OR NEW.kleinunternehmer IS NOT OLD.kleinunternehmer
    OR NEW.empfaenger IS NOT OLD.empfaenger OR NEW.verkaeufer IS NOT OLD.verkaeufer
    OR NEW.kaeufer IS NOT OLD.kaeufer OR NEW.summe_netto IS NOT OLD.summe_netto
    OR NEW.summe_ust IS NOT OLD.summe_ust OR NEW.summe_gesamt IS NOT OLD.summe_gesamt
    OR NEW.dateiname IS NOT OLD.dateiname OR NEW.abgeschlossen_am IS NOT OLD.abgeschlossen_am
    OR NEW.art IS NOT OLD.art OR NEW.bezug_id IS NOT OLD.bezug_id
    OR NEW.korrektur_grund IS NOT OLD.korrektur_grund OR NEW.vorausbezahlt IS NOT OLD.vorausbezahlt
    OR NEW.status = 'entwurf')
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Rechnungen können nicht geändert werden.');
END;

-- Storno/Korrektur nur zu abgeschlossenen Rechnungen desselben Kunden, nie zu einem Storno.
CREATE TRIGGER IF NOT EXISTS rechnungen_bezug_pruefen
BEFORE INSERT ON rechnungen
FOR EACH ROW
WHEN NEW.bezug_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM rechnungen o WHERE o.id = NEW.bezug_id AND o.kunde_id = NEW.kunde_id
       AND o.status <> 'entwurf' AND o.art <> 'storno')
BEGIN
    SELECT RAISE(ABORT, 'Storno/Korrektur nur zu einer abgeschlossenen Rechnung desselben Kunden.');
END;

CREATE TRIGGER IF NOT EXISTS rechnungen_loeschschutz
BEFORE DELETE ON rechnungen
FOR EACH ROW
WHEN OLD.status <> 'entwurf' AND EXISTS (SELECT 1 FROM kunden WHERE id = OLD.kunde_id)
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Rechnungen können nicht gelöscht werden.');
END;

CREATE TRIGGER IF NOT EXISTS rechnung_positionen_schutz_insert
BEFORE INSERT ON rechnung_positionen
FOR EACH ROW
WHEN (SELECT status FROM rechnungen WHERE id = NEW.rechnung_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Rechnungen können nicht geändert werden.');
END;

CREATE TRIGGER IF NOT EXISTS rechnung_positionen_schutz_update
BEFORE UPDATE ON rechnung_positionen
FOR EACH ROW
WHEN (SELECT status FROM rechnungen WHERE id = OLD.rechnung_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Rechnungen können nicht geändert werden.');
END;

CREATE TRIGGER IF NOT EXISTS rechnung_positionen_schutz_delete
BEFORE DELETE ON rechnung_positionen
FOR EACH ROW
WHEN (SELECT status FROM rechnungen WHERE id = OLD.rechnung_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'Abgeschlossene Rechnungen können nicht geändert werden.');
END;

-- Änderungsprotokoll ----------------------------------------------------
-- Allgemeine Tabelle, die auch von späteren Modulen (z. B. Leistungs-
-- verzeichnis) genutzt werden kann. Kein Fremdschlüssel, damit Einträge
-- auch nach endgültiger Löschung nachvollziehbar bleiben.
CREATE TABLE IF NOT EXISTS aenderungsprotokoll (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    zeitpunkt   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    bereich     TEXT    NOT NULL,          -- z. B. 'kunde'
    objekt_id   INTEGER NOT NULL,
    aktion      TEXT    NOT NULL,          -- angelegt, geaendert, archiviert, reaktiviert, geloescht
    details     TEXT    NOT NULL DEFAULT '' -- JSON mit geänderten Feldern
);

CREATE INDEX IF NOT EXISTS idx_protokoll_objekt ON aenderungsprotokoll (bereich, objekt_id);

-- Schutz auf Datenbankebene ---------------------------------------------
-- Löschen ist nur möglich, wenn der Kunde archiviert ist und die
-- Aufbewahrungsfrist abgelaufen ist – auch bei direktem SQL-Zugriff.
CREATE TRIGGER IF NOT EXISTS kunden_loeschschutz
BEFORE DELETE ON kunden
FOR EACH ROW
WHEN OLD.archiviert_am IS NULL
  OR OLD.loeschbar_ab IS NULL
  OR date('now', 'localtime') < OLD.loeschbar_ab
BEGIN
    SELECT RAISE(ABORT, 'Kunde darf erst nach Ablauf der 10-jährigen Aufbewahrungsfrist gelöscht werden.');
END;

-- Archivierte Kunden sind schreibgeschützt (nur Reaktivieren erlaubt).
CREATE TRIGGER IF NOT EXISTS kunden_archiv_schreibschutz
BEFORE UPDATE ON kunden
FOR EACH ROW
WHEN OLD.archiviert_am IS NOT NULL AND NEW.archiviert_am IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Archivierte Kunden können nicht geändert werden. Bitte zuerst reaktivieren.');
END;

CREATE TRIGGER IF NOT EXISTS ansprechpartner_archiv_insert
BEFORE INSERT ON ansprechpartner
FOR EACH ROW
WHEN (SELECT archiviert_am FROM kunden WHERE id = NEW.kunde_id) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Archivierte Kunden können nicht geändert werden. Bitte zuerst reaktivieren.');
END;

CREATE TRIGGER IF NOT EXISTS ansprechpartner_archiv_update
BEFORE UPDATE ON ansprechpartner
FOR EACH ROW
WHEN (SELECT archiviert_am FROM kunden WHERE id = OLD.kunde_id) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Archivierte Kunden können nicht geändert werden. Bitte zuerst reaktivieren.');
END;
