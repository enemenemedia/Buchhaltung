"""EÜR-Kategorien der Belegerfassung.

Die Kategorien folgen den Gruppen der Anlage EÜR. Die genaue Zeilennummer im Formular
ändert sich von Jahr zu Jahr und wird deshalb nicht fest hinterlegt.
Die Zuordnung im Einzelfall bitte mit der Steuerberatung abstimmen.
"""

EINNAHMEN = {
    "umsatz_steuerpflichtig": "Umsatzsteuerpflichtige Betriebseinnahmen",
    "umsatz_steuerfrei": "Umsatzsteuerfreie / nicht steuerbare Betriebseinnahmen",
    "umsatz_kleinunternehmer": "Betriebseinnahmen als Kleinunternehmer (§ 19 UStG)",
    "anlagenverkauf": "Veräußerung von Anlagevermögen",
    "ust_erstattung": "Vom Finanzamt erstattete Umsatzsteuer",
    "sonstige_einnahmen": "Sonstige Betriebseinnahmen",
}

AUSGABEN = {
    "waren": "Waren, Roh- und Hilfsstoffe",
    "fremdleistungen": "Bezogene Fremdleistungen",
    "personal": "Löhne, Gehälter, Sozialabgaben",
    "gwg": "Geringwertige Wirtschaftsgüter (bis 800 € netto)",
    "anlagevermoegen": "Anschaffung Anlagevermögen (Abschreibung)",
    "raumkosten": "Raumkosten, Miete, Nebenkosten",
    "telekommunikation": "Telefon, Internet",
    "software": "Software, Lizenzen, Hosting",
    "buero": "Büromaterial, Porto",
    "fortbildung": "Fortbildung, Fachliteratur",
    "beratung": "Rechts- und Steuerberatung, Buchführung",
    "werbung": "Werbekosten",
    "reisekosten": "Reisekosten",
    "bewirtung": "Bewirtungskosten (70 % abziehbar)",
    "kfz": "Kfz-Kosten",
    "versicherungen": "Versicherungen, Beiträge",
    "bankgebuehren": "Kontoführung, Zahlungsgebühren",
    "ust_zahlung": "An das Finanzamt gezahlte Umsatzsteuer",
    "sonstige_ausgaben": "Sonstige Betriebsausgaben",
}

ALLE = {**EINNAHMEN, **AUSGABEN}

STANDARD = {"einnahme": "umsatz_steuerpflichtig", "ausgabe": "sonstige_ausgaben"}

# Zahlungen an bzw. vom Finanzamt: der ganze Betrag ist Umsatzsteuer, keine Vorsteuer darin
OHNE_UST = {"ust_erstattung", "ust_zahlung"}

# Diese Ausgaben zählen in der EÜR im Jahr der Anschaffung (Belegdatum), nicht der Zahlung
NACH_ANSCHAFFUNG = {"gwg", "anlagevermoegen"}

GWG_GRENZE_CENT = 80000           # § 6 Abs. 2 EStG: bis 800 € netto
BEWIRTUNG_ABZIEHBAR = 70          # § 4 Abs. 5 Nr. 2 EStG: 70 % der angemessenen Kosten


def fuer_art(art: str) -> dict:
    return EINNAHMEN if art == "einnahme" else AUSGABEN


def bezeichnung(schluessel: str) -> str:
    return ALLE.get(schluessel, schluessel)
