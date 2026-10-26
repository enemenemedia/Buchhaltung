; Installer für „X-Rechnung für KMU“ (NSIS 3, Unicode)
; Gebaut von windows/bauen.py – Aufruf von Hand:
;   makensis -DVERSION=1.0.0 -DINHALT=<inhalt> -DAUSGABE=<dist> -DLIZENZ=<lizenz.txt> -DGROESSE_KB=<kb> installer.nsi
;
; - Installation ohne Administratorrechte für den angemeldeten Benutzer
; - Programmordner: %LOCALAPPDATA%\Programs\X-Rechnung für KMU (änderbar)
; - Datenordner:    Dokumente\X-Rechnung (änderbar; bei Updates wird der bisherige übernommen)
; - Deinstallation entfernt das Programm, NICHT die Daten (Aufbewahrungspflichten!)

Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ManifestDPIAware true

!define NAME "X-Rechnung für KMU"
!define FIRMA "Ene Mene Media"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\XRechnungKMU"
!define EINSTELLUNGEN "$INSTDIR\einstellungen.ini"

Name "${NAME}"
OutFile "${AUSGABE}\X-Rechnung-fuer-KMU-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${NAME}"
InstallDirRegKey HKCU "${REGKEY}" "InstallLocation"
RequestExecutionLevel user
BrandingText "${NAME} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1031 "ProductName" "${NAME}"
VIAddVersionKey /LANG=1031 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "FileDescription" "Installation von ${NAME}"
VIAddVersionKey /LANG=1031 "CompanyName" "${FIRMA}"
VIAddVersionKey /LANG=1031 "LegalCopyright" "MIT-Lizenz"

Var DATEN

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define MUI_ICON "${INHALT}\xrechnung.ico"
!define MUI_UNICON "${INHALT}\xrechnung.ico"
!define MUI_ABORTWARNING

; --- Seiten -----------------------------------------------------------------------
!define MUI_WELCOMEPAGE_TITLE "Willkommen bei ${NAME} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Dieser Assistent installiert ${NAME} – Kunden, Leistungen, Angebote und Rechnungen mit XRechnung und PDF.$\r$\n$\r$\nDie Installation benötigt keine Administratorrechte. Python und alle benötigten Komponenten sind enthalten.$\r$\n$\r$\nKlicken Sie auf „Weiter“, um fortzufahren."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LIZENZ}"

!define MUI_PAGE_HEADER_TEXT "Programmordner"
!define MUI_PAGE_HEADER_SUBTEXT "Wohin soll das Programm installiert werden?"
!define MUI_DIRECTORYPAGE_TEXT_TOP "Das Programm wird in den folgenden Ordner installiert. Ihre Daten liegen getrennt davon (nächster Schritt)."
!insertmacro MUI_PAGE_DIRECTORY

!define MUI_PAGE_HEADER_TEXT "Datenordner"
!define MUI_PAGE_HEADER_SUBTEXT "Wo sollen Datenbank, Angebote, Rechnungen und Dokumente gespeichert werden?"
!define MUI_DIRECTORYPAGE_TEXT_TOP "In diesem Ordner legt das Programm die Datenbank sowie je Kunde die Ordner für Angebote, Rechnungen (XML und PDF) und Dokumente an. Wählen Sie einen Ordner, der regelmäßig gesichert wird.$\r$\n$\r$\nBei einer Aktualisierung wird der bisherige Datenordner vorgeschlagen."
!define MUI_DIRECTORYPAGE_TEXT_DESTINATION "Datenordner"
!define MUI_DIRECTORYPAGE_VARIABLE $DATEN
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DatenordnerPruefen
!insertmacro MUI_PAGE_DIRECTORY

!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "${NAME} ist installiert"
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
  ; Bisherigen Datenordner übernehmen (Update), sonst Dokumente\X-Rechnung
  ReadINIStr $DATEN "${EINSTELLUNGEN}" "X-Rechnung" "datenordner"
  ${If} $DATEN == ""
    StrCpy $DATEN "$DOCUMENTS\X-Rechnung"
  ${EndIf}
FunctionEnd

Function ProgrammStarten
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\starter.py"'
FunctionEnd

Function DatenordnerPruefen
  ; Der Datenordner darf nicht im Programmordner liegen – sonst würde er bei der
  ; Deinstallation gelöscht.
  StrLen $0 "$INSTDIR"
  StrCpy $1 "$DATEN" $0
  ${If} $1 == "$INSTDIR"
    MessageBox MB_ICONEXCLAMATION|MB_OK "Der Datenordner darf nicht innerhalb des Programmordners liegen.$\r$\nBitte einen anderen Ordner wählen."
    Abort
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
  !insertmacro LaufendeBeenden

  ; Alte Programmdateien entfernen (Update), Einstellungen bleiben
  !insertmacro OrdnerEntfernen "$INSTDIR\python"
  !insertmacro OrdnerEntfernen "$INSTDIR\programm"
  !insertmacro OrdnerEntfernen "$INSTDIR\pruefung"

  SetOutPath "$INSTDIR"
  File /r "${INHALT}\*.*"

  CreateDirectory "$DATEN"
  WriteINIStr "${EINSTELLUNGEN}" "X-Rechnung" "datenordner" "$DATEN"
  WriteINIStr "${EINSTELLUNGEN}" "X-Rechnung" "version" "${VERSION}"

  ; Verknüpfungen
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${NAME}"
  CreateShortcut "$SMPROGRAMS\${NAME}\${NAME}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\starter.py"' "$INSTDIR\xrechnung.ico" 0 SW_SHOWNORMAL "" "Kunden, Angebote und XRechnungen"
  CreateShortcut "$SMPROGRAMS\${NAME}\Datenordner öffnen.lnk" "$DATEN"
  CreateShortcut "$SMPROGRAMS\${NAME}\${NAME} deinstallieren.lnk" "$INSTDIR\Deinstallieren.exe"
  CreateShortcut "$DESKTOP\${NAME}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\starter.py"' "$INSTDIR\xrechnung.ico" 0 SW_SHOWNORMAL "" "Kunden, Angebote und XRechnungen"

  ; Eintrag unter „Apps & Features“
  WriteUninstaller "$INSTDIR\Deinstallieren.exe"
  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${NAME}"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "Publisher" "${FIRMA}"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\xrechnung.ico"
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
  ReadINIStr $DATEN "${EINSTELLUNGEN}" "X-Rechnung" "datenordner"

  Delete "$DESKTOP\${NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${NAME}"
  !insertmacro OrdnerEntfernen "$INSTDIR\python"
  !insertmacro OrdnerEntfernen "$INSTDIR\programm"
  !insertmacro OrdnerEntfernen "$INSTDIR\pruefung"
  Delete "$INSTDIR\starter.py"
  Delete "$INSTDIR\xrechnung.ico"
  Delete "$INSTDIR\einstellungen.ini"
  Delete "$INSTDIR\Deinstallieren.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${REGKEY}"

  ${IfNot} ${Silent}
    MessageBox MB_ICONINFORMATION|MB_OK "${NAME} wurde entfernt.$\r$\n$\r$\nIhre Daten (Datenbank, Angebote, Rechnungen) wurden NICHT gelöscht und bleiben erhalten in:$\r$\n$DATEN$\r$\n$\r$\nBitte beachten Sie die gesetzlichen Aufbewahrungsfristen, bevor Sie diesen Ordner löschen."
  ${EndIf}
SectionEnd
