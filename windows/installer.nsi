; Installer für „Buchhaltung für KMU“ (NSIS 3, Unicode)
; Gebaut von windows/bauen.py – Aufruf von Hand:
;   makensis -DVERSION=1.0.0 -DINHALT=<inhalt> -DAUSGABE=<dist> -DGROESSE_KB=<kb> [-DTESTVERSION] installer.nsi
;
; Zwei Varianten aus demselben Skript:
; - Vollversion: installiert nur mit gültiger Lizenznummer. Die Nummer wird nach dem Entpacken
;   (in einen Zwischenordner) vom mitgelieferten Python geprüft; erst danach wird etwas installiert.
;   Ist sie ungültig, bleibt eine vorhandene Installation unverändert.
; - Testversion (-DTESTVERSION): ohne Lizenznummer; das Programm erstellt keine XRechnung (XML).
; - Ohne Lizenzpflicht (-DOHNE_LIZENZ, Standard seit 1.1): keine Lizenzseite, volle Funktion.
;   Eine Lizenznummer kann später im Programm unter „Einstellungen“ eingegeben werden.
;
; - Installation ohne Administratorrechte für den angemeldeten Benutzer
; - Programmordner: %LOCALAPPDATA%\Programs\Buchhaltung für KMU (änderbar)
; - Datenordner:    Dokumente\Buchhaltung für KMU (änderbar; bei Updates wird der bisherige übernommen;
;                   vorhandene Daten von „X-Rechnung für KMU“ können weiterverwendet werden;
;                   der Ordner muss für den angemeldeten Benutzer erreichbar und beschreibbar sein –
;                   geprüft beim Weiterklicken bzw. bei /S im Abschnitt: vollständiger Pfad, nicht im
;                   Programm-/Windows-Ordner oder im Profil eines anderen Benutzers, Schreibtest;
;                   Hinweis bei Wechseldatenträger und Netzlaufwerk)
; - INI-Dateien werden als UTF-16 angelegt, damit Umlaute (Pfad, Lizenznehmer) erhalten bleiben
; - Deinstallation entfernt das Programm, NICHT die Daten (Aufbewahrungspflichten!)

Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ManifestDPIAware true

!define NAME "Buchhaltung für KMU"
!define FIRMA "Ene Mene Media"
!ifdef TESTVERSION
  !define VARIANTE " (Testversion)"
  !define DATEINAME "Buchhaltung-fuer-KMU-Testversion-Setup-${VERSION}.exe"
!else
  !define VARIANTE ""
  !define DATEINAME "Buchhaltung-fuer-KMU-Setup-${VERSION}.exe"
!endif
; Lizenzseite und -prüfung nur in der Vollversion mit Lizenzpflicht
!ifndef TESTVERSION
  !ifndef OHNE_LIZENZ
    !define MIT_LIZENZ
  !endif
!endif
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BuchhaltungKMU"
!define EINSTELLUNGEN "$INSTDIR\einstellungen.ini"
!define INI_ABSCHNITT "Buchhaltung"
!define SYMBOL "buchhaltung.ico"

Name "${NAME}${VARIANTE}"
OutFile "${AUSGABE}\${DATEINAME}"
InstallDir "$LOCALAPPDATA\Programs\${NAME}"
InstallDirRegKey HKCU "${REGKEY}" "InstallLocation"
RequestExecutionLevel user
BrandingText "${NAME} ${VERSION}${VARIANTE}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1031 "ProductName" "${NAME}"
VIAddVersionKey /LANG=1031 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "FileDescription" "Installation von ${NAME}"
VIAddVersionKey /LANG=1031 "CompanyName" "${FIRMA}"
VIAddVersionKey /LANG=1031 "LegalCopyright" "© 2026 ${FIRMA}"

Var DATEN
Var DATENHINWEIS
!ifdef MIT_LIZENZ
Var LizenzName
Var LizenzNummer
Var FeldName
Var FeldNummer
!endif

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "nsDialogs.nsh"

!define MUI_ICON "${INHALT}\${SYMBOL}"
!define MUI_UNICON "${INHALT}\${SYMBOL}"
!define MUI_ABORTWARNING

; --- Seiten -----------------------------------------------------------------------
!define MUI_WELCOMEPAGE_TITLE "Willkommen bei ${NAME} ${VERSION}${VARIANTE}"
!ifdef TESTVERSION
!define MUI_WELCOMEPAGE_TEXT "Dieser Assistent installiert die Testversion von ${NAME} – Kunden, Angebote und Rechnungen mit PDF, Belegerfassung und Einnahmen-Überschuss-Rechnung.$\r$\n$\r$\nIn der Testversion sind alle Funktionen nutzbar, XRechnungen (XML) werden jedoch nicht erstellt. Mit einer Lizenznummer (im Programm unter „Einstellungen“) wird daraus die Vollversion – ohne Neuinstallation und ohne Datenverlust.$\r$\n$\r$\nDie Installation benötigt keine Administratorrechte."
!else ifdef OHNE_LIZENZ
!define MUI_WELCOMEPAGE_TEXT "Dieser Assistent installiert ${NAME} – Kunden, Angebote und Rechnungen mit XRechnung und PDF, Belegerfassung mit Tags und Einnahmen-Überschuss-Rechnung (EÜR).$\r$\n$\r$\nDie Installation benötigt keine Administratorrechte. Python und alle benötigten Komponenten sind enthalten.$\r$\n$\r$\nKlicken Sie auf „Weiter“, um fortzufahren."
!else
!define MUI_WELCOMEPAGE_TEXT "Dieser Assistent installiert ${NAME} – Kunden, Angebote und Rechnungen mit XRechnung und PDF, Belegerfassung und Einnahmen-Überschuss-Rechnung (EÜR).$\r$\n$\r$\nFür die Installation benötigen Sie Ihre Lizenznummer. Die Installation benötigt keine Administratorrechte. Python und alle benötigten Komponenten sind enthalten.$\r$\n$\r$\nKlicken Sie auf „Weiter“, um fortzufahren."
!endif
!insertmacro MUI_PAGE_WELCOME

!define MUI_PAGE_HEADER_TEXT "Programmordner"
!define MUI_PAGE_HEADER_SUBTEXT "Wohin soll das Programm installiert werden?"
!define MUI_DIRECTORYPAGE_TEXT_TOP "Das Programm wird in den folgenden Ordner installiert. Ihre Daten liegen getrennt davon (nächster Schritt)."
!insertmacro MUI_PAGE_DIRECTORY

!define MUI_PAGE_HEADER_TEXT "Datenordner"
!define MUI_PAGE_HEADER_SUBTEXT "Wo sollen Datenbank, Rechnungen und Belege gespeichert werden?"
!define MUI_DIRECTORYPAGE_TEXT_TOP "Hier legt das Programm die Datenbank, je Kunde Angebote und Rechnungen (XML und PDF) sowie die Belegablage an. Der Ordner muss für Ihr Windows-Benutzerkonto erreichbar und beschreibbar sein (z. B. unter Dokumente oder auf einem eigenen Datenlaufwerk) – das wird beim Weiterklicken geprüft. Bei einer Aktualisierung wird der bisherige Datenordner vorgeschlagen.$DATENHINWEIS"
!define MUI_DIRECTORYPAGE_TEXT_DESTINATION "Datenordner"
!define MUI_DIRECTORYPAGE_VARIABLE $DATEN
!define MUI_PAGE_CUSTOMFUNCTION_PRE DatenseiteVorbereiten
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DatenordnerPruefen
!insertmacro MUI_PAGE_DIRECTORY

!ifdef MIT_LIZENZ
Page custom LizenzSeite LizenzSeiteVerlassen
!endif

!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "${NAME}${VARIANTE} ist installiert"
!define MUI_FINISHPAGE_TEXT "Das Programm startet im Browser. Sie finden es im Startmenü und auf dem Desktop.$\r$\n$\r$\nDatenordner:$\r$\n$DATEN"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION ProgrammStarten
!define MUI_FINISHPAGE_RUN_TEXT "${NAME} jetzt starten"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "German"

; --- Funktionen ---------------------------------------------------------------------
Function .onInit
  InitPluginsDir
  ; Bisherigen Datenordner übernehmen (Update), sonst Dokumente\Buchhaltung für KMU
  ReadINIStr $DATEN "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "datenordner"
  ${If} $DATEN == ""
    StrCpy $DATEN "$DOCUMENTS\${NAME}"
  ${EndIf}
!ifdef MIT_LIZENZ
  ; Unbeaufsichtigte Installation:  Setup.exe /S /LIZENZNEHMER=Firma /LIZENZNUMMER=XRK1-…
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/LIZENZNEHMER=" $1
  ${IfNot} ${Errors}
    StrCpy $LizenzName $1
  ${EndIf}
  ClearErrors
  ${GetOptions} $0 "/LIZENZNUMMER=" $1
  ${IfNot} ${Errors}
    StrCpy $LizenzNummer $1
  ${EndIf}
  ${If} ${Silent}
  ${AndIf} $LizenzNummer == ""
    ReadINIStr $LizenzName "$DATEN\lizenz.ini" "Lizenz" "lizenznehmer"
    ReadINIStr $LizenzNummer "$DATEN\lizenz.ini" "Lizenz" "lizenznummer"
  ${EndIf}
!endif
FunctionEnd

!ifdef MIT_LIZENZ
Function LizenzSeite
  !insertmacro MUI_HEADER_TEXT "Lizenz" "Bitte geben Sie Ihre Lizenz ein."
  ; Bei einer Aktualisierung die vorhandene Lizenz aus dem Datenordner vorschlagen
  ${If} $LizenzNummer == ""
    ReadINIStr $LizenzName "$DATEN\lizenz.ini" "Lizenz" "lizenznehmer"
    ReadINIStr $LizenzNummer "$DATEN\lizenz.ini" "Lizenz" "lizenznummer"
  ${EndIf}
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 24u "Lizenznehmer und Lizenznummer finden Sie in Ihrer Lizenz-E-Mail. Am besten kopieren und einfügen."
  Pop $0
  ${NSD_CreateLabel} 0 30u 100% 10u "Lizenznehmer (genau wie in der E-Mail):"
  Pop $0
  ${NSD_CreateText} 0 42u 100% 13u "$LizenzName"
  Pop $FeldName
  ${NSD_CreateLabel} 0 64u 100% 10u "Lizenznummer (beginnt mit XRK1-):"
  Pop $0
  nsDialogs::CreateControl EDIT ${DEFAULT_STYLES}|${WS_TABSTOP}|${ES_MULTILINE}|${ES_AUTOVSCROLL}|${ES_WANTRETURN} ${WS_EX_CLIENTEDGE} 0 76u 100% 36u "$LizenzNummer"
  Pop $FeldNummer
  ${NSD_CreateLabel} 0 118u 100% 20u "Ohne Lizenznummer können Sie die Testversion installieren (eigene Setup-Datei)."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function LizenzSeiteVerlassen
  ${NSD_GetText} $FeldName $LizenzName
  ${NSD_GetText} $FeldNummer $LizenzNummer
  ${If} $LizenzName == ""
    MessageBox MB_ICONEXCLAMATION|MB_OK "Bitte den Lizenznehmer eingeben."
    Abort
  ${EndIf}
  ; Leerzeichen und Zeilenumbrüche am Anfang sind beim Kopieren häufig
  StrCpy $0 0
  ${Do}
    StrCpy $1 $LizenzNummer 1 $0
    ${If} $1 == " "
    ${OrIf} $1 == "$\r"
    ${OrIf} $1 == "$\n"
    ${OrIf} $1 == "$\t"
      IntOp $0 $0 + 1
    ${Else}
      ${Break}
    ${EndIf}
  ${Loop}
  StrCpy $LizenzNummer $LizenzNummer "" $0
  StrCpy $1 $LizenzNummer 4
  StrLen $2 $LizenzNummer
  ${If} $1 != "XRK1"          ; Vergleich ohne Groß-/Kleinschreibung
  ${OrIf} $2 < 100
    MessageBox MB_ICONEXCLAMATION|MB_OK "Die Lizenznummer beginnt mit „XRK1-“ und ist etwa 140 Zeichen lang.$\r$\nBitte vollständig aus der Lizenz-E-Mail kopieren."
    Abort
  ${EndIf}
FunctionEnd
!endif

Function DatenseiteVorbereiten
  ; Vorhandene Daten von „X-Rechnung für KMU“ (Kunden, Rechnungen, Lizenz) können weiterverwendet werden
  StrCpy $DATENHINWEIS ""
  ${If} ${FileExists} "$DOCUMENTS\X-Rechnung\xrechnung.db"
  ${AndIf} $DATEN != "$DOCUMENTS\X-Rechnung"
    StrCpy $DATENHINWEIS "$\r$\n$\r$\nIhre Daten aus „X-Rechnung für KMU“ liegen in $DOCUMENTS\X-Rechnung. Wählen Sie diesen Ordner, um mit Ihren Kunden und Rechnungen weiterzuarbeiten (beide Programme dann nicht gleichzeitig verwenden)."
  ${EndIf}
FunctionEnd

Function ProgrammStarten
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\starter.py"'
FunctionEnd

Function DatenordnerPruefen
  Call DatenordnerBewerten
  ${If} $0 != ""
    MessageBox MB_ICONEXCLAMATION|MB_OK "$0"
    Abort
  ${EndIf}
  ${If} $1 != ""
    MessageBox MB_ICONINFORMATION|MB_OKCANCEL "$1$\r$\n$\r$\nTrotzdem diesen Ordner verwenden?" IDOK +2
    Abort
  ${EndIf}
FunctionEnd

; Setzt $0, wenn $DATEN gleich ORDNER ist oder darin liegt (Vergleich mit abschließendem Backslash, ohne Groß/klein)
!macro NichtUnterOrdner ORDNER
  ${If} $0 == ""
    StrLen $R3 "${ORDNER}\"
    StrCpy $R1 "$DATEN\" $R3
    ${If} $R1 == "${ORDNER}\"
      StrCpy $0 "Der Ordner ${ORDNER} ist für Programme bzw. Windows reserviert – dort können Daten nicht sicher gespeichert werden.$\r$\nBitte einen Ordner unter „Dokumente“, auf einem eigenen Datenlaufwerk oder einem Netzlaufwerk wählen."
    ${EndIf}
  ${EndIf}
!macroend

; Der Datenordner muss für den angemeldeten Benutzer erreichbar und beschreibbar sein – das Programm
; läuft mit dessen Rechten. Ergebnis: $0 = Fehler (leer = in Ordnung), $1 = Hinweis zum Bestätigen.
Function DatenordnerBewerten
  StrCpy $0 ""
  StrCpy $1 ""
  ReadEnvStr $R0 "USERNAME"
  ; abschließenden Backslash entfernen
  StrCpy $R1 $DATEN 1 -1
  ${If} $R1 == "\"
    StrCpy $R1 $DATEN 3
    ${If} $R1 != $DATEN
      StrCpy $DATEN $DATEN -1
    ${EndIf}
  ${EndIf}
  ; vollständiger Pfad: Laufwerk (C:\…) oder Netzwerkpfad (\\Server\Freigabe\…)
  StrCpy $R1 $DATEN 2
  StrCpy $R2 $DATEN 1 1
  ${If} $R1 != "\\"
  ${AndIf} $R2 != ":"
    StrCpy $0 "Bitte einen vollständigen Pfad angeben, z. B. $DOCUMENTS\${NAME}."
    Return
  ${EndIf}
  StrLen $R3 $DATEN
  ${If} $R3 <= 3
    StrCpy $0 "Bitte nicht das Hauptverzeichnis eines Laufwerks wählen, sondern einen Ordner darin, z. B. $DATEN${NAME}."
    Return
  ${EndIf}

  ; nicht im Programmordner (würde bei der Deinstallation gelöscht)
  StrLen $R3 "$INSTDIR"
  StrCpy $R1 "$DATEN" $R3
  ${If} $R1 == "$INSTDIR"
    StrCpy $0 "Der Datenordner darf nicht innerhalb des Programmordners liegen.$\r$\nBitte einen anderen Ordner wählen."
    Return
  ${EndIf}
  ; nicht in Windows- oder Programmverzeichnissen (dort darf ein normaler Benutzer nicht schreiben)
  !insertmacro NichtUnterOrdner "$WINDIR"
  !insertmacro NichtUnterOrdner "$PROGRAMFILES32"
  !insertmacro NichtUnterOrdner "$PROGRAMFILES64"
  ${If} $0 != ""
    Return
  ${EndIf}
  ; nicht im Benutzerprofil eines anderen Benutzers (C:\Users\<anderer>\…)
  ${GetParent} "$PROFILE" $R4
  StrLen $R3 "$R4\"
  StrCpy $R1 "$DATEN" $R3
  ${If} $R1 == "$R4\"
    StrLen $R3 "$PROFILE\"
    StrCpy $R1 "$DATEN\" $R3
    StrLen $R3 "$R4\Public\"
    StrCpy $R5 "$DATEN\" $R3
    ${If} $R1 != "$PROFILE\"
    ${AndIf} $R5 != "$R4\Public\"
      StrCpy $0 "Der Ordner liegt im Benutzerprofil eines anderen Windows-Benutzers.$\r$\nDas Programm läuft unter Ihrem Benutzerkonto ($R0) und braucht einen Ordner, auf den Sie selbst zugreifen können – z. B. $DOCUMENTS\${NAME}."
      Return
    ${EndIf}
  ${EndIf}

  ; erreichbar und beschreibbar? (Ordner anlegen, Testdatei schreiben und wieder löschen)
  StrCpy $R6 "0"
  ${IfNot} ${FileExists} "$DATEN\*.*"
    StrCpy $R6 "1"
  ${EndIf}
  ClearErrors
  CreateDirectory "$DATEN"
  ${If} ${Errors}
  ${OrIfNot} ${FileExists} "$DATEN\*.*"
    StrCpy $0 "Der Ordner$\r$\n$DATEN$\r$\nkann für Ihr Benutzerkonto ($R0) nicht angelegt werden. Ist das Laufwerk verbunden und haben Sie dort Schreibrechte?$\r$\nBitte einen anderen Ordner wählen."
    Return
  ${EndIf}
  ClearErrors
  FileOpen $R7 "$DATEN\.schreibtest-setup" w
  ${If} ${Errors}
    ${If} $R6 == "1"
      RMDir "$DATEN"
    ${EndIf}
    StrCpy $0 "In den Ordner$\r$\n$DATEN$\r$\nkann Ihr Benutzerkonto ($R0) nicht schreiben. Das Programm braucht dort Lese- und Schreibrechte.$\r$\nBitte einen anderen Ordner wählen."
    Return
  ${EndIf}
  FileWrite $R7 "ok"
  FileClose $R7
  Delete "$DATEN\.schreibtest-setup"
  ${If} $R6 == "1"
    RMDir "$DATEN"                ; wird bei der Installation angelegt
  ${EndIf}

  ; Hinweise: Wechseldatenträger und Netzlaufwerke
  StrCpy $R1 $DATEN 2
  ${If} $R1 == "\\"
    StrCpy $1 "Der Datenordner liegt auf einem Netzlaufwerk. Die Einzelplatzversion darf ihn immer nur von EINEM PC aus verwenden – für mehrere PCs gibt es die Netzwerkversion. Ist das Netzlaufwerk nicht erreichbar, startet das Programm nicht."
  ${Else}
    StrCpy $R1 $DATEN 3
    System::Call 'kernel32::GetDriveTypeW(w "$R1") i .R2'
    ${If} $R2 == 2
      StrCpy $1 "Das Laufwerk $R1 ist ein Wechseldatenträger (z. B. USB-Stick). Das Programm startet nur, wenn er angeschlossen ist, und Daten gehen mit dem Datenträger leicht verloren. Für Sicherungen gibt es im Programm den Knopf „Sichern“."
    ${ElseIf} $R2 == 4
      StrCpy $1 "Das Laufwerk $R1 ist ein verbundenes Netzlaufwerk. Es muss bei jeder Anmeldung verbunden sein, und die Einzelplatzversion darf es immer nur von EINEM PC aus verwenden – für mehrere PCs gibt es die Netzwerkversion."
    ${EndIf}
  ${EndIf}
FunctionEnd

!macro LaufendeBeenden
  ; Läuft das Programm noch, wird es über den Starter beendet (Dateien sonst gesperrt).
  ${If} ${FileExists} "$INSTDIR\starter.py"
  ${AndIf} ${FileExists} "$INSTDIR\python\pythonw.exe"
    DetailPrint "Beende laufendes Programm …"
    ; Der Starter kehrt erst zurück, wenn der Programmprozess beendet ist.
    ExecWait '"$INSTDIR\python\pythonw.exe" "$INSTDIR\starter.py" --beenden'
  ${EndIf}
!macroend

!macro IniUnicode DATEI
  ; Leere INI-Datei mit UTF-16-BOM anlegen: WritePrivateProfileString schreibt dann Unicode
  ; (sonst ANSI – Umlaute in Pfad oder Lizenznehmer gingen verloren).
  Delete "${DATEI}"
  FileOpen $R8 "${DATEI}" w
  FileWriteWord $R8 0xFEFF
  FileClose $R8
!macroend

!macro OrdnerEntfernen ORDNER
  ; Löscht einen Ordner und wiederholt es, solange Dateien noch gesperrt sind
  ; (z. B. Virenscanner oder ein Prozess, der gerade endet).
  ${If} ${FileExists} "${ORDNER}\*.*"
    StrCpy $R9 0
    ${Do}
      RMDir /r "${ORDNER}"
      ${IfNot} ${FileExists} "${ORDNER}\*.*"
        ${Break}
      ${EndIf}
      IntOp $R9 $R9 + 1
      ${If} $R9 >= 30
        MessageBox MB_ICONEXCLAMATION|MB_RETRYCANCEL "Einige Dateien in$\r$\n${ORDNER}$\r$\nsind noch in Verwendung.$\r$\n$\r$\nBitte ${NAME} schließen und „Wiederholen“ wählen." /SD IDCANCEL IDRETRY +2
        Abort "Programmdateien sind noch in Verwendung."
        StrCpy $R9 0
      ${EndIf}
      Sleep 500
    ${Loop}
  ${EndIf}
!macroend

; --- Installation -------------------------------------------------------------------
Section "Programm" SecProgramm
  SectionIn RO
  ; unbeaufsichtigt (/S) gibt es keine Seiten: Datenordner hier prüfen, bevor etwas installiert wird
  ${If} ${Silent}
    Call DatenordnerBewerten
    ${If} $0 != ""
      Abort "$0"
    ${EndIf}
  ${EndIf}
  !insertmacro LaufendeBeenden

  ; Erst in einen Zwischenordner entpacken – eine vorhandene Installation bleibt unverändert,
  ; bis feststeht, dass die Lizenz gültig ist.
  !insertmacro OrdnerEntfernen "$INSTDIR\_neu"
  DetailPrint "Programmdateien entpacken …"
  SetOutPath "$INSTDIR\_neu"
  File /r "${INHALT}\*.*"

!ifdef MIT_LIZENZ
  DetailPrint "Lizenz prüfen …"
  !insertmacro IniUnicode "$PLUGINSDIR\lizenz.ini"
  WriteINIStr "$PLUGINSDIR\lizenz.ini" "Lizenz" "lizenznehmer" "$LizenzName"
  WriteINIStr "$PLUGINSDIR\lizenz.ini" "Lizenz" "lizenznummer" "$LizenzNummer"
  ClearErrors
  ExecWait '"$INSTDIR\_neu\python\pythonw.exe" "$INSTDIR\_neu\starter.py" --lizenz-pruefen "$PLUGINSDIR\lizenz.ini"' $0
  ${If} ${Errors}
  ${OrIf} $0 != 0
    ReadINIStr $1 "$PLUGINSDIR\lizenz.ini" "Lizenz" "fehler"
    ${If} $1 == ""
      StrCpy $1 "Die Lizenz konnte nicht geprüft werden (Fehlercode $0)."
    ${EndIf}
    SetOutPath "$TEMP"
    !insertmacro OrdnerEntfernen "$INSTDIR\_neu"
    RMDir "$INSTDIR"            ; nur wenn leer (Erstinstallation)
    MessageBox MB_ICONSTOP|MB_OK "Die Lizenznummer ist nicht gültig:$\r$\n$1$\r$\n$\r$\nEs wurde nichts installiert. Bitte starten Sie die Installation erneut und geben Sie die Lizenz genau so ein, wie sie in Ihrer Lizenz-E-Mail steht." /SD IDOK
    Abort "Installation abgebrochen: Lizenznummer ungültig."
  ${EndIf}
!endif

  ; Alte Programmdateien entfernen (Update), Einstellungen bleiben
  SetOutPath "$INSTDIR"
  !insertmacro OrdnerEntfernen "$INSTDIR\python"
  !insertmacro OrdnerEntfernen "$INSTDIR\programm"
  !insertmacro OrdnerEntfernen "$INSTDIR\pruefung"
  Delete "$INSTDIR\starter.py"
  Delete "$INSTDIR\${SYMBOL}"
  RMDir /r "$INSTDIR\__pycache__"
  DetailPrint "Programmdateien übernehmen …"
  Rename "$INSTDIR\_neu\python" "$INSTDIR\python"
  Rename "$INSTDIR\_neu\programm" "$INSTDIR\programm"
  ${If} ${FileExists} "$INSTDIR\_neu\pruefung\*.*"
    Rename "$INSTDIR\_neu\pruefung" "$INSTDIR\pruefung"
  ${EndIf}
  Rename "$INSTDIR\_neu\starter.py" "$INSTDIR\starter.py"
  Rename "$INSTDIR\_neu\${SYMBOL}" "$INSTDIR\${SYMBOL}"
  RMDir /r "$INSTDIR\_neu"
  ${IfNot} ${FileExists} "$INSTDIR\python\pythonw.exe"
    MessageBox MB_ICONSTOP|MB_OK "Die Programmdateien konnten nicht übernommen werden. Bitte die Installation erneut starten." /SD IDOK
    Abort "Programmdateien konnten nicht übernommen werden."
  ${EndIf}

  CreateDirectory "$DATEN"
!ifdef MIT_LIZENZ
  CopyFiles /SILENT "$PLUGINSDIR\lizenz.ini" "$DATEN\lizenz.ini"
!endif
  !insertmacro IniUnicode "${EINSTELLUNGEN}"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "datenordner" "$DATEN"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "version" "${VERSION}"

  ; Verknüpfungen
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${NAME}"
  CreateShortcut "$SMPROGRAMS\${NAME}\${NAME}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\starter.py"' "$INSTDIR\${SYMBOL}" 0 SW_SHOWNORMAL "" "Rechnungen, Belege und EÜR"
  CreateShortcut "$SMPROGRAMS\${NAME}\Datenordner öffnen.lnk" "$DATEN"
  CreateShortcut "$SMPROGRAMS\${NAME}\${NAME} deinstallieren.lnk" "$INSTDIR\Deinstallieren.exe"
  CreateShortcut "$DESKTOP\${NAME}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\starter.py"' "$INSTDIR\${SYMBOL}" 0 SW_SHOWNORMAL "" "Rechnungen, Belege und EÜR"

  ; Eintrag unter „Apps & Features“
  WriteUninstaller "$INSTDIR\Deinstallieren.exe"
  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${NAME}${VARIANTE}"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "Publisher" "${FIRMA}"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\${SYMBOL}"
  WriteRegStr HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Deinstallieren.exe"'
  WriteRegStr HKCU "${REGKEY}" "QuietUninstallString" '"$INSTDIR\Deinstallieren.exe" /S'
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${REGKEY}" "EstimatedSize" ${GROESSE_KB}
SectionEnd

; --- Deinstallation -----------------------------------------------------------------
Section "Uninstall"
  !insertmacro LaufendeBeenden
  ReadINIStr $DATEN "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "datenordner"

  Delete "$DESKTOP\${NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${NAME}"
  !insertmacro OrdnerEntfernen "$INSTDIR\python"
  !insertmacro OrdnerEntfernen "$INSTDIR\programm"
  !insertmacro OrdnerEntfernen "$INSTDIR\pruefung"
  RMDir /r "$INSTDIR\_neu"
  RMDir /r "$INSTDIR\__pycache__"
  Delete "$INSTDIR\starter.py"
  Delete "$INSTDIR\${SYMBOL}"
  Delete "$INSTDIR\einstellungen.ini"
  Delete "$INSTDIR\Deinstallieren.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${REGKEY}"

  ${IfNot} ${Silent}
    MessageBox MB_ICONINFORMATION|MB_OK "${NAME} wurde entfernt.$\r$\n$\r$\nIhre Daten (Datenbank, Rechnungen, Belege) wurden NICHT gelöscht und bleiben erhalten in:$\r$\n$DATEN$\r$\n$\r$\nBitte beachten Sie die gesetzlichen Aufbewahrungsfristen, bevor Sie diesen Ordner löschen."
  ${EndIf}
SectionEnd
