// Ansprechpartner im Kundenformular dynamisch hinzufügen und entfernen.
(function () {
  const liste = document.getElementById("ap-liste");
  const vorlage = document.getElementById("ap-vorlage");
  const leer = document.getElementById("ap-leer");
  if (!liste || !vorlage) return;

  let naechster = Math.max(-1, ...[...liste.querySelectorAll(".ap-karte")].map(k => +k.dataset.index)) + 1;

  function aktualisieren() {
    const karten = liste.querySelectorAll(".ap-karte");
    leer.hidden = karten.length > 0;
    // Gibt es Kontakte, aber keinen Hauptkontakt, wird der erste zum Hauptkontakt.
    if (karten.length && !liste.querySelector('input[name="hauptkontakt"]:checked')) {
      karten[0].querySelector('input[name="hauptkontakt"]').checked = true;
    }
  }

  document.getElementById("ap-hinzufuegen").addEventListener("click", () => {
    const html = vorlage.innerHTML.replaceAll("__I__", String(naechster++));
    liste.insertAdjacentHTML("beforeend", html);
    const neu = liste.lastElementChild;
    aktualisieren();
    neu.querySelector('input[name$="-vorname"]').focus();
  });

  liste.addEventListener("click", (e) => {
    const knopf = e.target.closest(".entfernen");
    if (!knopf) return;
    knopf.closest(".ap-karte").remove();
    aktualisieren();
  });

  aktualisieren();
})();
