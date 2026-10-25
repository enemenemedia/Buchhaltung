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
