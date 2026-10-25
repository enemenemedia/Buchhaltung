// Beleg-Editor für Angebote und Rechnungen: Ansprechpartner je Kunde, Leistungsauswahl,
// freie Positionen, Reihenfolge und Live-Berechnung der Summen (Logik wie app/berechnung.py).
(function () {
  const form = document.querySelector("form[data-beleg-editor]");
  if (!form) return;

  const kunden = JSON.parse(document.getElementById("kunden-daten").textContent);
  const leistungen = JSON.parse(document.getElementById("leistungen-daten").textContent);
  const ausweis = form.dataset.ausweis;              // 'netto' | 'brutto'
  const klein = form.dataset.klein === "1";
  const liste = document.getElementById("pos-liste");
  const vorlage = document.getElementById("pos-vorlage");
  const leer = document.getElementById("pos-leer");
  const summen = document.getElementById("summen");

  // ---- Zahlen ------------------------------------------------------------
  function zahl(text) {
    let t = String(text ?? "").replace(/[€\s ]/g, "");
    if (!t) return NaN;
    if (t.includes(",") && t.includes(".")) {
      t = t.lastIndexOf(",") > t.lastIndexOf(".") ? t.replace(/\./g, "").replace(",", ".") : t.replace(/,/g, "");
    } else if (t.includes(",")) {
      t = t.replace(",", ".");
    } else if ((t.match(/\./g) || []).length > 1) {
      t = t.replace(/\./g, "");
    }
    return /^[+-]?\d+(\.\d+)?$/.test(t) ? Number(t) : NaN;
  }
  const r2 = x => Math.round((x + Number.EPSILON) * 100) / 100;
  const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const euro2 = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
  const dez = (x, max = 3) => new Intl.NumberFormat("de-DE", { maximumFractionDigits: max }).format(x);
  const preisText = x => dez(x, 4).includes(",") ? new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(x)
                                                 : new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2 }).format(x);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- Kunde & Ansprechpartner --------------------------------------------
  const kundeSel = document.getElementById("kunde");
  const apSel = document.getElementById("ansprechpartner");
  function ansprechpartnerFuellen(erstmalig) {
    if (!kundeSel || !apSel) return;
    const k = kunden.find(x => String(x.id) === kundeSel.value);
    const vorher = erstmalig ? apSel.dataset.wert : "";
    apSel.innerHTML = '<option value="">– ohne –</option>';
    if (!k) return;
    k.ansprechpartner.forEach(a => {
      const o = new Option(a.name + (a.haupt ? " (Hauptkontakt)" : ""), a.id);
      if (vorher ? String(a.id) === vorher : a.haupt) o.selected = true;
      apSel.add(o);
    });
  }
  kundeSel?.addEventListener("change", () => {
    ansprechpartnerFuellen(false);
    form.dispatchEvent(new CustomEvent("kundewechsel", { detail: kunden.find(x => String(x.id) === kundeSel.value) }));
  });
  ansprechpartnerFuellen(true);

  // ---- Laufzeit-Schnellwahl -------------------------------------------------
  const datum = document.getElementById("datum"), gueltig = document.getElementById("gueltig");
  if (datum && gueltig) form.querySelectorAll("[data-tage]").forEach(b => b.addEventListener("click", () => {
    const d = new Date((datum.value || new Date().toISOString().slice(0, 10)) + "T12:00:00");
    d.setDate(d.getDate() + Number(b.dataset.tage));
    gueltig.value = d.toISOString().slice(0, 10);
  }));

  // ---- Positionen ------------------------------------------------------------
  let naechster = Math.max(-1, ...[...liste.children].map(z => Number(z.dataset.index))) + 1;

  function neueZeile(werte) {
    const html = vorlage.innerHTML.replaceAll("__I__", String(naechster++));
    liste.insertAdjacentHTML("beforeend", html);
    const z = liste.lastElementChild;
    if (werte) {
      const setze = (feld, wert) => { z.querySelector(`[name$="-${feld}"]`).value = wert ?? ""; };
      setze("leistung_id", werte.id);
      setze("leistungsnummer", werte.nummer);
      setze("bezeichnung", werte.bezeichnung);
      setze("beschreibung", werte.beschreibung);
      setze("einheit_code", werte.einheit_code);
      setze("menge", dez(zahl(werte.anzahl)));
      setze("einzelpreis", preisText(zahl(werte.preis)));
      setze("ust_satz", werte.ust_satz);
      z.classList.remove("frei");
      z.querySelector(".pos-herkunft").innerHTML = `<span class="marke-status aktiv mono">${esc(werte.nummer)}</span>`;
    }
    hoehe(z.querySelector("textarea"));
    aktualisieren();
    return z;
  }

  function hoehe(ta) {
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.max(ta.scrollHeight, 34) + "px";
  }

  function aktualisieren() {
    const zeilen = [...liste.children];
    leer.hidden = zeilen.length > 0;
    zeilen.forEach((z, i) => {
      z.querySelector(".pos-zaehler").textContent = i + 1;
      z.querySelector(".hoch").disabled = i === 0;
      z.querySelector(".runter").disabled = i === zeilen.length - 1;
    });
    rechnen();
  }

  function rechnen() {
    const gruppen = new Map();     // Satz -> {netto, brutto}
    let gueltig = true;
    [...liste.children].forEach(z => {
      const menge = zahl(z.querySelector(".pos-menge").value || "1");
      const preis = zahl(z.querySelector(".pos-preis").value);
      const satz = klein ? 0 : Number(z.querySelector(".pos-ust").value);
      const einheit = z.querySelector(".pos-einheit").selectedOptions[0];
      const betragEl = z.querySelector(".pos-betrag"), zweit = z.querySelector(".pos-zweitbetrag");
      if (isNaN(menge) || isNaN(preis)) {
        betragEl.textContent = "–"; zweit.textContent = ""; gueltig = false;
        return;
      }
      const netto = r2(menge * preis), brutto = r2(netto * (1 + satz / 100));
      const g = gruppen.get(satz) || { netto: 0, brutto: 0 };
      g.netto += netto; g.brutto += brutto;
      gruppen.set(satz, g);
      if (ausweis === "brutto" && !klein) {
        betragEl.textContent = euro2.format(brutto);
        zweit.textContent = `netto ${euro2.format(netto)}`;
      } else {
        betragEl.textContent = euro2.format(netto);
        zweit.textContent = klein || ausweis === "netto" ? `${dez(menge)} × ${euro.format(preis)}` : "";
      }
      if (einheit) zweit.title = `${dez(menge)} ${menge === 1 ? einheit.textContent : einheit.dataset.mehrzahl}`;
    });

    if (!liste.children.length) { summen.innerHTML = ""; return; }
    const saetze = [...gruppen.keys()].sort((a, b) => b - a);
    let zeilen = [];
    if (klein) {
      const gesamt = r2(saetze.reduce((s, k) => s + gruppen.get(k).netto, 0));
      zeilen = [["Gesamtbetrag", gesamt, true], ["Gemäß § 19 UStG ohne Umsatzsteuer", null, false]];
    } else if (ausweis === "brutto") {
      let gesamt = 0, netto = 0;
      const ust = [];
      saetze.forEach(s => {
        const b = r2(gruppen.get(s).brutto), n = r2(b / (1 + s / 100));
        gesamt += b; netto += n;
        if (s) ust.push([`enthaltene USt ${dez(s)} %`, r2(b - n), false]);
      });
      zeilen = [["Gesamtbetrag", r2(gesamt), true], ["Nettobetrag", r2(netto), false], ...ust];
    } else {
      let netto = 0, ust = 0;
      const steuer = [];
      saetze.forEach(s => {
        const n = r2(gruppen.get(s).netto), u = r2(n * s / 100);
        netto += n; ust += u;
        steuer.push([`zzgl. ${dez(s)} % USt auf ${euro2.format(n)}`, u, false]);
      });
      zeilen = [["Summe netto", r2(netto), false], ...steuer, ["Gesamtbetrag", r2(netto + ust), true]];
    }
    summen.innerHTML = zeilen.map(([l, w, fett]) =>
      `<div class="${fett ? "summe-gesamt" : ""}"><span>${esc(l)}</span><span>${w === null ? "" : euro2.format(w)}</span></div>`
    ).join("") + (gueltig ? "" : '<p class="fehlertext klein">Einige Positionen enthalten ungültige Zahlen.</p>');
  }

  liste.addEventListener("click", e => {
    const z = e.target.closest(".pos-zeile");
    if (!z) return;
    if (e.target.closest(".entfernen-pos")) { z.remove(); aktualisieren(); }
    else if (e.target.closest(".hoch") && z.previousElementSibling) { liste.insertBefore(z, z.previousElementSibling); aktualisieren(); }
    else if (e.target.closest(".runter") && z.nextElementSibling) { liste.insertBefore(z.nextElementSibling, z); aktualisieren(); }
  });
  liste.addEventListener("input", e => { if (e.target.tagName === "TEXTAREA") hoehe(e.target); rechnen(); });
  liste.addEventListener("change", rechnen);

  document.getElementById("frei-hinzufuegen").addEventListener("click", () => {
    neueZeile(null).querySelector(".pos-bezeichnung").focus();
  });

  // ---- Leistungsauswahl --------------------------------------------------------
  const suche = document.getElementById("auswahl-suche");
  const treffer = document.getElementById("auswahl-liste");
  let aktiv = -1, sichtbar = [];

  function zeigen() {
    const q = suche.value.trim().toLowerCase();
    sichtbar = leistungen.filter(l => !q || [l.nummer, l.bezeichnung, l.kategorie, l.beschreibung]
      .some(t => String(t || "").toLowerCase().includes(q))).slice(0, 60);
    aktiv = sichtbar.length ? 0 : -1;
    treffer.innerHTML = sichtbar.length ? sichtbar.map((l, i) =>
      `<li role="option" data-i="${i}" class="${i === aktiv ? "aktiv" : ""}">
         <span class="mono gedimmt">${esc(l.nummer)}</span>
         <span class="auswahl-name">${esc(l.bezeichnung)}${l.kategorie ? ` <small class="gedimmt">· ${esc(l.kategorie)}</small>` : ""}</span>
         <span class="auswahl-preis">${euro.format(zahl(l.preis))} / ${esc(l.einheit)}</span>
       </li>`).join("")
      : `<li class="keine">Keine passende Leistung. <button type="button" class="knopf-link" id="als-neu">„${esc(suche.value)}“ als neue Leistung eingeben</button></li>`;
    treffer.hidden = false;
  }
  function markieren(i) {
    aktiv = i;
    [...treffer.querySelectorAll("li[data-i]")].forEach((li, j) => li.classList.toggle("aktiv", j === i));
    treffer.querySelector("li.aktiv")?.scrollIntoView({ block: "nearest" });
  }
  function waehlen(i) {
    if (!sichtbar[i]) return;
    neueZeile(sichtbar[i]);
    suche.value = "";
    treffer.hidden = true;
    suche.focus();
  }
  suche.addEventListener("focus", zeigen);
  suche.addEventListener("input", zeigen);
  suche.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); markieren(Math.min(aktiv + 1, sichtbar.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); markieren(Math.max(aktiv - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); waehlen(aktiv); }
    else if (e.key === "Escape") { treffer.hidden = true; }
  });
  treffer.addEventListener("mousedown", e => {
    e.preventDefault();  // Fokus im Suchfeld behalten
    const li = e.target.closest("li[data-i]");
    if (li) waehlen(Number(li.dataset.i));
    if (e.target.id === "als-neu") {
      const text = suche.value.trim();
      suche.value = ""; treffer.hidden = true;
      const z = neueZeile(null);
      z.querySelector(".pos-bezeichnung").value = text;
      z.querySelector(".pos-preis").focus();
    }
  });
  suche.addEventListener("blur", () => setTimeout(() => (treffer.hidden = true), 120));

  // Enter in Positionsfeldern soll das Formular nicht absenden
  liste.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.tagName === "INPUT") e.preventDefault(); });

  liste.querySelectorAll("textarea").forEach(hoehe);
  aktualisieren();
})();
