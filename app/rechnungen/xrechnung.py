"""Erzeugung der XRechnung 3.0 im Format UBL 2.1 (Invoice).

Die Elementreihenfolge folgt dem UBL-2.1-Schema. Kommentare nennen die
Business Terms (BT) bzw. Business Groups (BG) aus EN 16931 / XRechnung.

Belegarten (BT-3, UNTDID 1001):
  380 – Rechnung                         (UBL Invoice)
  384 – Rechnungskorrektur               (UBL Invoice, mit Bezug BG-3 auf die Originalrechnung)
  381 – Gutschrift = Stornorechnung      (UBL CreditNote, Beträge positiv, Bezug BG-3)

Steuerkategorien (UNTDID 5305):
  S  – Normalsatz/ermäßigter Satz (19 %, 7 %)
  E  – steuerbefreit; auch Kleinunternehmer nach § 19 UStG (mit Befreiungsgrund BT-120)
  AE – Steuerschuldnerschaft des Leistungsempfängers / Reverse Charge (BT-121 VATEX-EU-AE)
"""

from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

from .. import berechnung, zahlen

NS_INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CN = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

GRUND_KLEINUNTERNEHMER = "Umsatzsteuerbefreit als Kleinunternehmer gemäß § 19 UStG"
GRUND_REVERSE_CHARGE = "Steuerschuldnerschaft des Leistungsempfängers"
GRUND_STEUERFREI = "Steuerfreie Leistung"

ET.register_namespace("", NS_INV)
ET.register_namespace("cn", NS_CN)
ET.register_namespace("cac", NS_CAC)
ET.register_namespace("cbc", NS_CBC)


def _cac(eltern, name):
    return ET.SubElement(eltern, f"{{{NS_CAC}}}{name}")


def _cbc(eltern, name, text, **attrs):
    if text is None or text == "":
        return None
    e = ET.SubElement(eltern, f"{{{NS_CBC}}}{name}", {k: v for k, v in attrs.items() if v})
    e.text = str(text)
    return e


def betrag(wert) -> str:
    """Beträge mit genau zwei Nachkommastellen (BR-DEC-*)."""
    return f"{zahlen.runden(zahlen.parsen(wert)):.2f}"


def _zahl(wert) -> str:
    """Mengen und Preise ohne überflüssige Nullen, ohne Exponentenschreibweise."""
    return zahlen.speicherform(zahlen.parsen(wert))


def _eur(eltern, name, wert):
    return _cbc(eltern, name, betrag(wert), currencyID="EUR")


def steuerkategorie(satz: Decimal, kleinunternehmer: bool, befreiung_art: str) -> str:
    if kleinunternehmer:
        return "E"
    if satz > 0:
        return "S"
    return "AE" if befreiung_art == "AE" else "E"


def befreiungsgrund(kategorie: str, kleinunternehmer: bool, grund: str) -> str:
    if kategorie == "S":
        return ""
    if kleinunternehmer:
        return GRUND_KLEINUNTERNEHMER
    if kategorie == "AE":
        return grund or GRUND_REVERSE_CHARGE
    return grund or GRUND_STEUERFREI


def _adresse(party, d: dict) -> None:
    adr = _cac(party, "PostalAddress")
    _cbc(adr, "StreetName", d.get("strasse"))                 # BT-35 / BT-50
    _cbc(adr, "AdditionalStreetName", d.get("adresszusatz"))  # BT-36 / BT-51
    _cbc(adr, "CityName", d.get("ort"))                       # BT-37 / BT-52
    _cbc(adr, "PostalZone", d.get("plz"))                     # BT-38 / BT-53
    land = _cac(adr, "Country")
    _cbc(land, "IdentificationCode", d.get("land") or "DE")   # BT-40 / BT-55


def _steuer_id(party, nummer: str, schema: str) -> None:
    if not nummer:
        return
    pts = _cac(party, "PartyTaxScheme")
    _cbc(pts, "CompanyID", nummer)
    ts = _cac(pts, "TaxScheme")
    _cbc(ts, "ID", schema)


TYPCODE = {"rechnung": "380", "korrektur": "384", "storno": "381"}


def erzeugen(r: dict, positionen: list, verkaeufer: dict, kaeufer: dict, *,
             art: str = "rechnung", bezug: dict | None = None) -> bytes:
    """r: Rechnungskopf (nummer, rechnungsdatum, faellig_am, leistung_von/-bis, …).

    verkaeufer/kaeufer: eingefrorene Stamm- bzw. Kundendaten (siehe service.abschliessen).
    art: 'rechnung' | 'korrektur' | 'storno'.
    bezug: Originalrechnung bei Korrektur/Storno – {"nummer", "datum", "bezahlt"}.
    Positionen und Beträge werden immer positiv übergeben; bei der Gutschrift (Storno)
    ergibt sich die Gegenbuchung aus der Belegart 381.
    """
    if art not in TYPCODE:
        raise ValueError(f"Unbekannte Belegart {art}")
    if art != "rechnung" and not bezug:
        raise ValueError("Storno und Korrektur brauchen den Bezug auf die Originalrechnung.")
    gutschrift = art == "storno"
    klein = bool(r["kleinunternehmer"])
    summen = berechnung.berechnen(positionen, klein, "netto")
    vorausbezahlt = zahlen.parsen(r.get("vorausbezahlt") or "0") if art == "korrektur" else Decimal(0)

    inv = ET.Element(f"{{{NS_CN}}}CreditNote" if gutschrift else f"{{{NS_INV}}}Invoice")
    _cbc(inv, "CustomizationID", CUSTOMIZATION_ID)                        # BT-24
    _cbc(inv, "ProfileID", PROFILE_ID)                                    # BT-23
    _cbc(inv, "ID", r["nummer"])                                          # BT-1
    _cbc(inv, "IssueDate", r["rechnungsdatum"])                           # BT-2
    if not gutschrift:
        _cbc(inv, "DueDate", r["faellig_am"])                             # BT-9
    _cbc(inv, "CreditNoteTypeCode" if gutschrift else "InvoiceTypeCode", TYPCODE[art])   # BT-3
    for notiz in _notizen(r, klein, art, bezug):                          # BT-22
        _cbc(inv, "Note", notiz)
    _cbc(inv, "DocumentCurrencyCode", "EUR")                              # BT-5
    _cbc(inv, "BuyerReference", r["kaeufer_referenz"])                    # BT-10 (Leitweg-ID)
    if r.get("leistung_bis"):                                             # BG-14 Leistungszeitraum
        zeitraum = _cac(inv, "InvoicePeriod")
        _cbc(zeitraum, "StartDate", r["leistung_von"])                    # BT-73
        _cbc(zeitraum, "EndDate", r["leistung_bis"])                      # BT-74
    if r.get("bestellnummer"):
        bestellung = _cac(inv, "OrderReference")
        _cbc(bestellung, "ID", r["bestellnummer"])                        # BT-13
    if bezug:                                                             # BG-3 Vorausgegangene Rechnung
        ref = _cac(_cac(inv, "BillingReference"), "InvoiceDocumentReference")
        _cbc(ref, "ID", bezug["nummer"])                                  # BT-25
        _cbc(ref, "IssueDate", bezug["datum"])                            # BT-26

    # BG-4 Verkäufer ----------------------------------------------------------
    sup = _cac(_cac(inv, "AccountingSupplierParty"), "Party")
    _cbc(sup, "EndpointID", verkaeufer["elektronische_adresse"], schemeID="EM")   # BT-34
    if verkaeufer.get("handelsname"):
        _cbc(_cac(sup, "PartyName"), "Name", verkaeufer["handelsname"])          # BT-28
    _adresse(sup, verkaeufer)                                                     # BG-5
    _steuer_id(sup, verkaeufer.get("ust_id"), "VAT")                              # BT-31
    _steuer_id(sup, verkaeufer.get("steuernummer"), "FC")                         # BT-32
    rechtlich = _cac(sup, "PartyLegalEntity")
    _cbc(rechtlich, "RegistrationName", verkaeufer["firma"])                      # BT-27
    register = " ".join(x for x in (verkaeufer.get("registernummer"), verkaeufer.get("registergericht")) if x)
    _cbc(rechtlich, "CompanyID", register)                                        # BT-30
    kontakt = _cac(sup, "Contact")                                                # BG-6
    _cbc(kontakt, "Name", verkaeufer["kontakt_name"])                             # BT-41
    _cbc(kontakt, "Telephone", verkaeufer["kontakt_telefon"])                     # BT-42
    _cbc(kontakt, "ElectronicMail", verkaeufer["kontakt_email"])                  # BT-43

    # BG-7 Käufer ---------------------------------------------------------------
    cus = _cac(_cac(inv, "AccountingCustomerParty"), "Party")
    _cbc(cus, "EndpointID", kaeufer["elektronische_adresse"], schemeID="EM")     # BT-49
    _cbc(_cac(cus, "PartyIdentification"), "ID", kaeufer["kundennummer"])        # BT-46
    _adresse(cus, kaeufer)                                                        # BG-8
    _steuer_id(cus, kaeufer.get("ust_id"), "VAT")                                 # BT-48
    _cbc(_cac(cus, "PartyLegalEntity"), "RegistrationName", kaeufer["firma"])    # BT-44
    if kaeufer.get("ap_name") or kaeufer.get("ap_email") or kaeufer.get("ap_telefon"):
        k = _cac(cus, "Contact")                                                  # BG-9
        _cbc(k, "Name", kaeufer.get("ap_name"))                                   # BT-56
        _cbc(k, "Telephone", kaeufer.get("ap_telefon"))                           # BT-57
        _cbc(k, "ElectronicMail", kaeufer.get("ap_email"))                        # BT-58

    # BG-13 Lieferung: Leistungsdatum (ohne Zeitraum) ------------------------------
    if not r.get("leistung_bis"):
        _cbc(_cac(inv, "Delivery"), "ActualDeliveryDate", r["leistung_von"])      # BT-72

    # BG-16 Zahlung: SEPA-Überweisung ----------------------------------------------
    zm = _cac(inv, "PaymentMeans")
    if gutschrift:
        # Storno: Verrechnung mit der Originalrechnung bzw. Erstattung, falls bereits bezahlt
        _cbc(zm, "PaymentMeansCode", "ZZZ" if bezug.get("bezahlt") else "97")    # BT-81
        _cbc(zm, "PaymentID", r["nummer"])                                        # BT-83
    else:
        _cbc(zm, "PaymentMeansCode", "58")                                        # BT-81 SEPA-Überweisung
        _cbc(zm, "PaymentID", r["nummer"])                                        # BT-83 Verwendungszweck
        konto = _cac(zm, "PayeeFinancialAccount")                                 # BG-17
        _cbc(konto, "ID", verkaeufer["iban"])                                     # BT-84
        _cbc(konto, "Name", verkaeufer.get("kontoinhaber") or verkaeufer["firma"])  # BT-85
        if verkaeufer.get("bic"):
            _cbc(_cac(konto, "FinancialInstitutionBranch"), "ID", verkaeufer["bic"])  # BT-86
    _cbc(_cac(inv, "PaymentTerms"), "Note", zahlungsbedingung(r, art, bezug))     # BT-20

    # BG-23 Umsatzsteuer ----------------------------------------------------------
    gruppen: dict[str, dict] = {}
    for g in summen.steuergruppen:
        kat = steuerkategorie(g.satz, klein, r.get("befreiung_art") or "E")
        eintrag = gruppen.setdefault(f"{kat}{g.satz}", {"kat": kat, "satz": g.satz, "basis": Decimal(0),
                                                        "betrag": Decimal(0)})
        eintrag["basis"] += g.basis
        eintrag["betrag"] += g.betrag
    tax = _cac(inv, "TaxTotal")
    _eur(tax, "TaxAmount", summen.ust)                                            # BT-110
    for g in gruppen.values():
        sub = _cac(tax, "TaxSubtotal")
        _eur(sub, "TaxableAmount", g["basis"])                                    # BT-116
        _eur(sub, "TaxAmount", g["betrag"])                                       # BT-117
        kat = _cac(sub, "TaxCategory")
        _cbc(kat, "ID", g["kat"])                                                 # BT-118
        _cbc(kat, "Percent", _zahl(g["satz"]))                                    # BT-119
        if g["kat"] == "AE":
            _cbc(kat, "TaxExemptionReasonCode", "VATEX-EU-AE")                    # BT-121
        grund = befreiungsgrund(g["kat"], klein, r.get("befreiung_grund", ""))
        _cbc(kat, "TaxExemptionReason", grund)                                    # BT-120
        _cbc(_cac(kat, "TaxScheme"), "ID", "VAT")

    # BG-22 Summen ----------------------------------------------------------------
    lmt = _cac(inv, "LegalMonetaryTotal")
    _eur(lmt, "LineExtensionAmount", summen.netto)                                # BT-106
    _eur(lmt, "TaxExclusiveAmount", summen.netto)                                 # BT-109
    _eur(lmt, "TaxInclusiveAmount", summen.gesamt)                                # BT-112
    if vorausbezahlt:
        _eur(lmt, "PrepaidAmount", vorausbezahlt)                                 # BT-113
    _eur(lmt, "PayableAmount", summen.gesamt - vorausbezahlt)                     # BT-115

    # BG-25 Positionen ------------------------------------------------------------
    for nr, (p, z) in enumerate(zip(positionen, summen.zeilen), start=1):
        zeile = _cac(inv, "CreditNoteLine" if gutschrift else "InvoiceLine")
        _cbc(zeile, "ID", nr)                                                     # BT-126
        _cbc(zeile, "CreditedQuantity" if gutschrift else "InvoicedQuantity",     # BT-129/130
             _zahl(p["menge"]), unitCode=p["einheit_code"])
        _eur(zeile, "LineExtensionAmount", z.betrag_netto)                        # BT-131
        item = _cac(zeile, "Item")
        _cbc(item, "Description", p["beschreibung"])                              # BT-154
        _cbc(item, "Name", p["bezeichnung"])                                      # BT-153
        if p["leistungsnummer"]:
            _cbc(_cac(item, "SellersItemIdentification"), "ID", p["leistungsnummer"])  # BT-155
        kat = _cac(item, "ClassifiedTaxCategory")
        _cbc(kat, "ID", steuerkategorie(z.ust_satz, klein, r.get("befreiung_art") or "E"))  # BT-151
        _cbc(kat, "Percent", _zahl(z.ust_satz))                                   # BT-152
        _cbc(_cac(kat, "TaxScheme"), "ID", "VAT")
        _cbc(_cac(zeile, "Price"), "PriceAmount", _zahl(p["einzelpreis"]), currencyID="EUR")  # BT-146

    ET.indent(inv, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(inv, encoding="utf-8")


def _de(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def zahlungsbedingung(r: dict, art: str = "rechnung", bezug: dict | None = None) -> str:
    if art == "storno":
        if bezug and bezug.get("bezahlt"):
            return f"Der bereits gezahlte Betrag der Rechnung {bezug['nummer']} wird Ihnen erstattet."
        return f"Der Betrag wird mit der Rechnung {bezug['nummer']} verrechnet; es ist keine Zahlung erforderlich."
    vorausbezahlt = zahlen.parsen(r.get("vorausbezahlt") or "0") if art == "korrektur" else Decimal(0)
    if vorausbezahlt:
        return (f"Auf die Rechnung {bezug['nummer']} wurden bereits {zahlen.geld(vorausbezahlt)} gezahlt; "
                "es ist nur der Differenzbetrag fällig bzw. wird erstattet.")
    tage = int(r["zahlungsziel_tage"])
    faellig = _de(r["faellig_am"])
    if tage == 0:
        return f"Zahlbar sofort ohne Abzug (bis {faellig})."
    return f"Zahlbar innerhalb von {tage} Tagen ohne Abzug bis zum {faellig}."


def _notizen(r: dict, klein: bool, art: str = "rechnung", bezug: dict | None = None) -> list[str]:
    notizen = []
    if art == "storno":
        notizen.append(f"Stornorechnung zur Rechnung {bezug['nummer']} vom {_de(bezug['datum'])}.")
    elif art == "korrektur":
        notizen.append(f"Rechnungskorrektur: ersetzt die Rechnung {bezug['nummer']} vom {_de(bezug['datum'])}.")
    if art != "rechnung" and r.get("korrektur_grund"):
        notizen.append(f"Grund: {r['korrektur_grund']}")
    if r.get("betreff"):
        notizen.append(r["betreff"])
    if r.get("anmerkungen"):
        notizen.append(r["anmerkungen"])
    if klein:
        notizen.append("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.")
    return notizen
