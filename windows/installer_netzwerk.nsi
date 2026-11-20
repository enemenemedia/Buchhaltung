; Installer der Windows-Netzwerkversion von „Buchhaltung für KMU“ (NSIS 3, Unicode)
; Gebaut von windows/bauen.py --variante netzwerk – Aufruf von Hand:
;   makensis -DVERSION=1.3.0 -DINHALT=<inhalt> -DAUSGABE=<dist> -DLIZENZ=<lizenz.txt> -DGROESSE_KB=<kb> installer_netzwerk.nsi
;
; Zwei Installationsarten aus einem Setup:
; - Server:       Dieser PC stellt die Buchhaltung im LAN bereit (HTTPS, Anmeldung mit Benutzern
;                 und Rollen). Alle Daten liegen auf dem Netzlaufwerk:
;                 <Freigabe>\Buchhaltung für KMU\        Server-Info und Zertifikat (lesen alle PCs)
;                 <Freigabe>\Buchhaltung für KMU\Daten\  Datenbank, Belege, Rechnungen (nur der Server)
; - Arbeitsplatz: Öffnet die Buchhaltung im Browser; Server-Adresse und Zertifikat kommen vom Netzlaufwerk.
;
; Bei der Installation wird das Netzlaufwerk zugewiesen: UNC-Pfad der Freigabe, optional ein
; Laufwerksbuchstabe (net use … /persistent:yes) und Zugangsdaten (cmdkey, nur wenn die Freigabe
; andere Anmeldedaten verlangt). Das Programm selbst arbeitet mit dem UNC-Pfad.
;
; Unbeaufsichtigt:  Setup.exe /S /MODUS=server|arbeitsplatz /FREIGABE=\\SERVER\Freigabe [/LAUFWERK=N:] [/PORT=8443]
;
; Installation ohne Administratorrechte; nur die Firewall-Freigabe (Server) fragt einmal nach.
; Die Deinstallation entfernt das Programm, NICHT die Daten und NICHT die Laufwerkszuordnung.

Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ManifestDPIAware true

!define NAME "Buchhaltung für KMU"
!define TITEL "Buchhaltung für KMU – Netzwerk"
!define FIRMA "Ene Mene Media"
!define DATEINAME "Buchhaltung-fuer-KMU-Netzwerk-Setup-${VERSION}.exe"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BuchhaltungKMUNetzwerk"
!define EINSTELLUNGEN "$INSTDIR\einstellungen.ini"
!define INI_ABSCHNITT "Netzwerk"
!define SYMBOL "buchhaltung.ico"
!define FIREWALL_REGEL "Buchhaltung fuer KMU (Netzwerk-Server)"
!define STARTER '"$INSTDIR\python\pythonw.exe" "$INSTDIR\netzwerk_starter.py"'

Name "${TITEL}"
OutFile "${AUSGABE}\${DATEINAME}"
InstallDir "$LOCALAPPDATA\Programs\${TITEL}"
InstallDirRegKey HKCU "${REGKEY}" "InstallLocation"
RequestExecutionLevel user
BrandingText "${TITEL} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1031 "ProductName" "${TITEL}"
VIAddVersionKey /LANG=1031 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "FileDescription" "Installation von ${TITEL}"
VIAddVersionKey /LANG=1031 "CompanyName" "${FIRMA}"
VIAddVersionKey /LANG=1031 "LegalCopyright" "MIT-Lizenz"

Var Modus          ; server | arbeitsplatz
Var Freigabe       ; \\SERVER\Freigabe
Var Laufwerk       ; N: oder leer
Var NetzBenutzer
Var NetzPasswort
Var Port
Var Autostart      ; 1/0
Var Firewall       ; 1/0
Var DATEN
Var ORDNER
Var Host
Var Abschluss
; Steuerelemente
Var RadioServer
Var RadioArbeit
Var FeldFreigabe
Var FeldLaufwerk
Var FeldBenutzer
Var FeldPasswort
Var FeldPort
Var KnopfAutostart
Var KnopfFirewall

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "nsDialogs.nsh"

!define MUI_ICON "${INHALT}\${SYMBOL}"
!define MUI_UNICON "${INHALT}\${SYMBOL}"
!define MUI_ABORTWARNING

; --- Seiten -----------------------------------------------------------------------
!define MUI_WELCOMEPAGE_TITLE "Willkommen bei ${TITEL} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Mit der Netzwerkversion arbeiten mehrere PCs im Büro mit derselben Buchhaltung. Alle Daten liegen auf einem Netzlaufwerk.$\r$\n$\r$\n• Ein PC ist der Server: Er stellt die Buchhaltung im lokalen Netz bereit (verschlüsselt per HTTPS, Anmeldung mit Benutzern und Rollen).$\r$\n• Alle anderen PCs sind Arbeitsplätze und öffnen die Buchhaltung im Browser.$\r$\n$\r$\nInstallieren Sie zuerst den Server, danach die Arbeitsplätze. Administratorrechte sind nicht nötig."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LIZENZ}"
Page custom ModusSeite ModusSeiteVerlassen
Page custom NetzSeite NetzSeiteVerlassen
Page custom ServerSeite ServerSeiteVerlassen

!define MUI_PAGE_HEADER_TEXT "Programmordner"
!define MUI_PAGE_HEADER_SUBTEXT "Wohin soll das Programm auf diesem PC installiert werden?"
!define MUI_DIRECTORYPAGE_TEXT_TOP "Das Programm wird lokal in den folgenden Ordner installiert. Die Daten liegen auf dem Netzlaufwerk."
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_TITLE "${TITEL} ist installiert"
!define MUI_FINISHPAGE_TEXT "$Abschluss"
!define MUI_PAGE_CUSTOMFUNCTION_PRE AbschlussVorbereiten
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION ProgrammStarten
!define MUI_FINISHPAGE_RUN_TEXT "Jetzt starten"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "German"

; --- Initialisierung ---------------------------------------------------------------
Function .onInit
  InitPluginsDir
  ; Bei einer Aktualisierung die bisherigen Einstellungen übernehmen
  ReadINIStr $Modus "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "modus"
  ReadINIStr $Freigabe "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "freigabe"
  ReadINIStr $Laufwerk "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "laufwerk"
  ReadINIStr $Port "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "port"
  ReadINIStr $Autostart "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "autostart"
  ${If} $Modus == ""
    StrCpy $Modus "server"
  ${EndIf}
  ${If} $Port == ""
    StrCpy $Port "8443"
  ${EndIf}
  ${If} $Autostart == ""
    StrCpy $Autostart "1"
  ${EndIf}
  StrCpy $Firewall "1"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/MODUS=" $R1
  ${IfNot} ${Errors}
    StrCpy $Modus $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/FREIGABE=" $R1
  ${IfNot} ${Errors}
    StrCpy $Freigabe $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/LAUFWERK=" $R1
  ${IfNot} ${Errors}
    StrCpy $Laufwerk $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/PORT=" $R1
  ${IfNot} ${Errors}
    StrCpy $Port $R1
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/OHNEFIREWALL" $R1
  ${IfNot} ${Errors}
    StrCpy $Firewall "0"
  ${EndIf}
  ${If} $Modus != "server"
  ${AndIf} $Modus != "arbeitsplatz"
    StrCpy $Modus "server"
  ${EndIf}
FunctionEnd

; --- Seite: Art der Installation -----------------------------------------------------
Function ModusSeite
  !insertmacro MUI_HEADER_TEXT "Art der Installation" "Ist dieser PC der Server oder ein Arbeitsplatz?"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateRadioButton} 0 0 100% 12u "&Server – dieser PC stellt die Buchhaltung im Netz bereit"
  Pop $RadioServer
  ${NSD_CreateLabel} 12u 14u -12u 34u "Nur EIN PC im Büro. Er muss eingeschaltet und angemeldet sein, während andere arbeiten. Er speichert alle Daten auf dem Netzlaufwerk. Hier legen Sie nach der Installation den ersten Administrator an."
  Pop $0
  ${NSD_CreateRadioButton} 0 56u 100% 12u "&Arbeitsplatz – Buchhaltung im Browser öffnen"
  Pop $RadioArbeit
  ${NSD_CreateLabel} 12u 70u -12u 34u "Für alle weiteren PCs. Server-Adresse und Zertifikat werden automatisch vom Netzlaufwerk übernommen; die Anmeldung erfolgt mit dem eigenen Benutzerkonto."
  Pop $0
  ${If} $Modus == "arbeitsplatz"
    ${NSD_Check} $RadioArbeit
  ${Else}
    ${NSD_Check} $RadioServer
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function ModusSeiteVerlassen
  ${NSD_GetState} $RadioArbeit $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Modus "arbeitsplatz"
  ${Else}
    StrCpy $Modus "server"
  ${EndIf}
FunctionEnd

; --- Seite: Netzlaufwerk ------------------------------------------------------------
Function NetzSeite
  !insertmacro MUI_HEADER_TEXT "Netzlaufwerk" "Wo liegen die gemeinsamen Daten?"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 20u "Netzwerkfreigabe (Dateiserver, NAS oder freigegebener Ordner), z. B. \\NAS\Buero oder \\SERVER\Daten. Darin wird der Ordner „${NAME}“ verwendet."
  Pop $0
  ${NSD_CreateText} 0 22u 100% 13u "$Freigabe"
  Pop $FeldFreigabe
  ${NSD_CreateLabel} 0 42u 55% 10u "Als Laufwerk verbinden (optional):"
  Pop $0
  ${NSD_CreateDropList} 56% 40u 44% 80u ""
  Pop $FeldLaufwerk
  ${NSD_CB_AddString} $FeldLaufwerk "(kein Laufwerksbuchstabe)"
  ${For} $1 4 25
    ; Buchstaben D: bis Z:
    IntOp $2 $1 + 65
    IntFmt $3 "%c:" $2
    ${NSD_CB_AddString} $FeldLaufwerk $3
  ${Next}
  ${If} $Laufwerk == ""
    ${NSD_CB_SelectString} $FeldLaufwerk "(kein Laufwerksbuchstabe)"
  ${Else}
    ${NSD_CB_SelectString} $FeldLaufwerk $Laufwerk
  ${EndIf}
  ${NSD_CreateLabel} 0 60u 100% 18u "Nur falls die Freigabe andere Anmeldedaten als Ihre Windows-Anmeldung verlangt (bei vielen NAS üblich):"
  Pop $0
  ${NSD_CreateLabel} 0 82u 30% 10u "Benutzername:"
  Pop $0
  ${NSD_CreateText} 31% 80u 69% 13u "$NetzBenutzer"
  Pop $FeldBenutzer
  ${NSD_CreateLabel} 0 99u 30% 10u "Passwort:"
  Pop $0
  ${NSD_CreatePassword} 31% 97u 69% 13u "$NetzPasswort"
  Pop $FeldPasswort
  ${NSD_CreateLabel} 0 116u 100% 20u "Die Zugangsdaten werden in der Windows-Anmeldeinformationsverwaltung dieses Benutzers gespeichert, nicht im Programm."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function NetzSeiteVerlassen
  ${NSD_GetText} $FeldFreigabe $Freigabe
  ${NSD_GetText} $FeldLaufwerk $Laufwerk
  ${NSD_GetText} $FeldBenutzer $NetzBenutzer
  ${NSD_GetText} $FeldPasswort $NetzPasswort
  ${If} $Laufwerk == "(kein Laufwerksbuchstabe)"
    StrCpy $Laufwerk ""
  ${EndIf}
  Call NetzlaufwerkEinrichten
  ${If} $0 != ""
    MessageBox MB_ICONEXCLAMATION|MB_OK "$0"
    Abort
  ${EndIf}
FunctionEnd

; Prüft und verbindet das Netzlaufwerk. Ergebnis in $0: leer = in Ordnung, sonst Fehlermeldung.
Function NetzlaufwerkEinrichten
  StrCpy $0 ""
  ; Leerzeichen und abschließenden Backslash entfernen
  ${Do}
    StrCpy $1 $Freigabe 1 -1
    ${If} $1 == "\"
    ${OrIf} $1 == " "
      StrCpy $Freigabe $Freigabe -1
    ${Else}
      ${Break}
    ${EndIf}
  ${Loop}
  ${Do}
    StrCpy $1 $Freigabe 1
    ${If} $1 == " "
      StrCpy $Freigabe $Freigabe "" 1
    ${Else}
      ${Break}
    ${EndIf}
  ${Loop}
  StrCpy $1 $Freigabe 2
  ${If} $1 != "\\"
    StrCpy $0 "Bitte die Freigabe als Netzwerkpfad angeben, z. B. \\NAS\Buero.$\r$\n$\r$\n(Ein Laufwerksbuchstabe wie N: gilt nur für einen Benutzer – deshalb arbeitet das Programm mit dem Netzwerkpfad.)"
    Return
  ${EndIf}
  ; Rechnername = Teil zwischen den beiden Backslashes am Anfang und dem nächsten Backslash
  StrCpy $Host $Freigabe "" 2
  StrCpy $2 0
  ${Do}
    StrCpy $1 $Host 1 $2
    ${If} $1 == "\"
    ${OrIf} $1 == ""
      ${Break}
    ${EndIf}
    IntOp $2 $2 + 1
  ${Loop}
  StrCpy $1 $Host "" $2
  StrCpy $Host $Host $2
  ${If} $Host == ""
  ${OrIf} $1 == ""
    StrCpy $0 "Der Netzwerkpfad muss Rechner und Freigabe enthalten, z. B. \\NAS\Buero."
    Return
  ${EndIf}

  ; Zugangsdaten für diesen Rechner speichern (wirkt auch nach einem Neustart)
  ${If} $NetzBenutzer != ""
    DetailPrint "Zugangsdaten für $Host speichern …"
    nsExec::ExecToStack 'cmdkey /add:$Host /user:"$NetzBenutzer" /pass:"$NetzPasswort"'
    Pop $1
    Pop $2
    ${If} $1 != 0
      StrCpy $0 "Die Zugangsdaten konnten nicht gespeichert werden:$\r$\n$2"
      Return
    ${EndIf}
  ${EndIf}

  ${IfNot} ${FileExists} "$Freigabe\*.*"
    StrCpy $0 "Die Freigabe $Freigabe ist nicht erreichbar.$\r$\n$\r$\nIst der Dateiserver/NAS eingeschaltet, der Pfad richtig geschrieben und haben Sie Zugriff? Falls die Freigabe ein eigenes Passwort hat, bitte Benutzername und Passwort eintragen."
    Return
  ${EndIf}

  ; Laufwerksbuchstaben verbinden
  ${If} $Laufwerk != ""
    ${If} ${FileExists} "$Laufwerk\*.*"
      ; Buchstabe schon belegt: zeigt er auf dieselbe Freigabe? (Testdatei über beide Wege)
      ${If} $Modus == "server"
        GetTempFileName $3 "$PLUGINSDIR"
        ${GetFileName} $3 $3
        ClearErrors
        FileOpen $4 "$Freigabe\$3" w
        ${If} ${Errors}
          StrCpy $0 "Auf der Freigabe $Freigabe kann nicht geschrieben werden.$\r$\nDer Server braucht Lese- und Schreibrechte."
          Return
        ${EndIf}
        FileClose $4
        ${IfNot} ${FileExists} "$Laufwerk\$3"
          Delete "$Freigabe\$3"
          StrCpy $0 "Das Laufwerk $Laufwerk ist bereits mit einer anderen Freigabe verbunden.$\r$\nBitte einen anderen Buchstaben wählen."
          Return
        ${EndIf}
        Delete "$Freigabe\$3"
      ${EndIf}
    ${Else}
      DetailPrint "Netzlaufwerk $Laufwerk verbinden …"
      nsExec::ExecToStack 'net use $Laufwerk "$Freigabe" /persistent:yes'
      Pop $1
      Pop $2
      ${If} $1 != 0
        StrCpy $0 "Das Laufwerk $Laufwerk konnte nicht mit $Freigabe verbunden werden:$\r$\n$\r$\n$2$\r$\nBitte Pfad, Zugangsdaten und Netzwerkverbindung prüfen."
        Return
      ${EndIf}
    ${EndIf}
  ${EndIf}

  StrCpy $ORDNER "$Freigabe\${NAME}"
  StrCpy $DATEN "$ORDNER\Daten"

  ${If} $Modus == "server"
    ; Datenordner anlegen und Schreibrecht prüfen
    CreateDirectory "$DATEN"
    ClearErrors
    FileOpen $4 "$DATEN\.schreibtest" w
    ${If} ${Errors}
      StrCpy $0 "Im Ordner $DATEN kann nicht geschrieben werden.$\r$\nDer Server braucht Lese- und Schreibrechte auf der Freigabe."
      Return
    ${EndIf}
    FileClose $4
    Delete "$DATEN\.schreibtest"
  ${Else}
    ${IfNot} ${FileExists} "$ORDNER\netzwerk-server.json"
      MessageBox MB_ICONQUESTION|MB_YESNO "Auf $Freigabe wurde noch kein Server von ${NAME} gefunden.$\r$\n$\r$\nBitte zuerst auf dem Server-PC die Installationsart „Server“ installieren und starten.$\r$\n$\r$\nTrotzdem fortfahren?" /SD IDYES IDYES +2
      StrCpy $0 "Bitte zuerst den Server installieren."
    ${EndIf}
  ${EndIf}
FunctionEnd

; --- Seite: Server-Optionen ------------------------------------------------------------
Function ServerSeite
  ${If} $Modus != "server"
    Abort
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "Server" "Wie soll der Server laufen?"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 2u 45% 10u "Port (Standard 8443):"
  Pop $0
  ${NSD_CreateNumber} 46% 0 20% 13u "$Port"
  Pop $FeldPort
  ${NSD_CreateCheckbox} 0 24u 100% 12u "Server beim &Anmelden an Windows automatisch starten (empfohlen)"
  Pop $KnopfAutostart
  ${If} $Autostart == "1"
    ${NSD_Check} $KnopfAutostart
  ${EndIf}
  ${NSD_CreateCheckbox} 0 42u 100% 12u "&Windows-Firewall für das lokale Netz freigeben (fragt einmal nach Administratorrechten)"
  Pop $KnopfFirewall
  ${If} $Firewall == "1"
    ${NSD_Check} $KnopfFirewall
  ${EndIf}
  ${NSD_CreateLabel} 0 62u 100% 50u "Die Arbeitsplätze öffnen später https://<Name dieses PCs>:Port. Der Server läuft, solange auf diesem PC ein Benutzer angemeldet ist (Sperren ist in Ordnung, Abmelden beendet ihn). Ruhezustand/Energiesparen für die Arbeitszeit ausschalten.$\r$\n$\r$\nDaten: $DATEN"
  Pop $0
  nsDialogs::Show
FunctionEnd

Function ServerSeiteVerlassen
  ${NSD_GetText} $FeldPort $Port
  ${If} $Port < 1024
  ${OrIf} $Port > 65535
    MessageBox MB_ICONEXCLAMATION|MB_OK "Bitte einen Port zwischen 1024 und 65535 angeben (Standard 8443)."
    Abort
  ${EndIf}
  ${NSD_GetState} $KnopfAutostart $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Autostart "1"
  ${Else}
    StrCpy $Autostart "0"
  ${EndIf}
  ${NSD_GetState} $KnopfFirewall $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Firewall "1"
  ${Else}
    StrCpy $Firewall "0"
  ${EndIf}
FunctionEnd

Function AbschlussVorbereiten
  ${If} $Modus == "server"
    StrCpy $Abschluss "Dieser PC ist der Server. Beim Start öffnet sich der Browser – legen Sie dort den ersten Administrator und unter „Benutzer“ die Konten für alle Mitarbeitenden an.$\r$\n$\r$\nDaten: $DATEN$\r$\n$\r$\nDanach auf den anderen PCs dasselbe Setup als „Arbeitsplatz“ installieren."
  ${Else}
    StrCpy $Abschluss "Dieser PC ist ein Arbeitsplatz. Beim ersten Start fragt Windows einmal, ob dem Zertifikat der Buchhaltung vertraut werden soll – bitte mit „Ja“ bestätigen. Danach öffnet sich die Anmeldung im Browser.$\r$\n$\r$\nNetzlaufwerk: $Freigabe"
  ${EndIf}
FunctionEnd

Function ProgrammStarten
  Exec '${STARTER}'
FunctionEnd

!macro LaufendeBeenden
  ${If} ${FileExists} "$INSTDIR\netzwerk_starter.py"
  ${AndIf} ${FileExists} "$INSTDIR\python\pythonw.exe"
    DetailPrint "Laufenden Server beenden …"
    ExecWait '${STARTER} --beenden'
  ${EndIf}
!macroend

!macro IniUnicode DATEI
  Delete "${DATEI}"
  FileOpen $R8 "${DATEI}" w
  FileWriteWord $R8 0xFEFF
  FileClose $R8
!macroend

!macro OrdnerEntfernen ORDNER
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
  ${If} ${Silent}
    ; ohne Seiten: Netzlaufwerk hier prüfen und verbinden
    Call NetzlaufwerkEinrichten
    ${If} $0 != ""
      Abort "$0"
    ${EndIf}
  ${EndIf}
  !insertmacro LaufendeBeenden

  !insertmacro OrdnerEntfernen "$INSTDIR\_neu"
  DetailPrint "Programmdateien entpacken …"
  SetOutPath "$INSTDIR\_neu"
  File /r "${INHALT}\*.*"

  SetOutPath "$INSTDIR"
  !insertmacro OrdnerEntfernen "$INSTDIR\python"
  !insertmacro OrdnerEntfernen "$INSTDIR\programm"
  !insertmacro OrdnerEntfernen "$INSTDIR\pruefung"
  Delete "$INSTDIR\starter.py"
  Delete "$INSTDIR\netzwerk_starter.py"
  Delete "$INSTDIR\${SYMBOL}"
  RMDir /r "$INSTDIR\__pycache__"
  DetailPrint "Programmdateien übernehmen …"
  Rename "$INSTDIR\_neu\python" "$INSTDIR\python"
  Rename "$INSTDIR\_neu\programm" "$INSTDIR\programm"
  ${If} ${FileExists} "$INSTDIR\_neu\pruefung\*.*"
    Rename "$INSTDIR\_neu\pruefung" "$INSTDIR\pruefung"
  ${EndIf}
  Rename "$INSTDIR\_neu\netzwerk_starter.py" "$INSTDIR\netzwerk_starter.py"
  Rename "$INSTDIR\_neu\${SYMBOL}" "$INSTDIR\${SYMBOL}"
  RMDir /r "$INSTDIR\_neu"
  ${IfNot} ${FileExists} "$INSTDIR\python\pythonw.exe"
    MessageBox MB_ICONSTOP|MB_OK "Die Programmdateien konnten nicht übernommen werden. Bitte die Installation erneut starten." /SD IDOK
    Abort "Programmdateien konnten nicht übernommen werden."
  ${EndIf}

  !insertmacro IniUnicode "${EINSTELLUNGEN}"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "modus" "$Modus"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "freigabe" "$Freigabe"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "laufwerk" "$Laufwerk"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "ordner" "$ORDNER"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "datenordner" "$DATEN"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "port" "$Port"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "autostart" "$Autostart"
  WriteINIStr "${EINSTELLUNGEN}" "${INI_ABSCHNITT}" "version" "${VERSION}"

  ; Verknüpfungen
  SetOutPath "$INSTDIR"
  Delete "$SMSTARTUP\${TITEL} (Server).lnk"
  RMDir /r "$SMPROGRAMS\${TITEL}"
  CreateDirectory "$SMPROGRAMS\${TITEL}"
  CreateShortcut "$SMPROGRAMS\${TITEL}\${NAME}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\netzwerk_starter.py"' "$INSTDIR\${SYMBOL}" 0 SW_SHOWNORMAL "" "Buchhaltung im Browser öffnen"
  CreateShortcut "$DESKTOP\${TITEL}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\netzwerk_starter.py"' "$INSTDIR\${SYMBOL}" 0 SW_SHOWNORMAL "" "Buchhaltung im Browser öffnen"
  CreateShortcut "$SMPROGRAMS\${TITEL}\Server-Status.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\netzwerk_starter.py" --status' "$INSTDIR\${SYMBOL}"
  ${If} $Laufwerk != ""
    CreateShortcut "$SMPROGRAMS\${TITEL}\Datenordner öffnen.lnk" "$Laufwerk\${NAME}"
  ${Else}
    CreateShortcut "$SMPROGRAMS\${TITEL}\Datenordner öffnen.lnk" "$ORDNER"
  ${EndIf}
  CreateShortcut "$SMPROGRAMS\${TITEL}\Deinstallieren.lnk" "$INSTDIR\Deinstallieren.exe"
  ${If} $Modus == "server"
    CreateShortcut "$SMPROGRAMS\${TITEL}\Server beenden.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\netzwerk_starter.py" --beenden' "$INSTDIR\${SYMBOL}"
    ${If} $Autostart == "1"
      CreateShortcut "$SMSTARTUP\${TITEL} (Server).lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\netzwerk_starter.py" --server --ohne-browser' "$INSTDIR\${SYMBOL}"
    ${EndIf}
    ${If} $Firewall == "1"
      DetailPrint "Windows-Firewall freigeben (Bestätigung mit Administratorrechten) …"
      ExecShellWait "runas" "$SYSDIR\cmd.exe" '/c netsh advfirewall firewall delete rule name="${FIREWALL_REGEL}" & netsh advfirewall firewall add rule name="${FIREWALL_REGEL}" dir=in action=allow protocol=TCP localport=$Port profile=private,domain program="$INSTDIR\python\pythonw.exe"' SW_HIDE
      nsExec::ExecToStack 'netsh advfirewall firewall show rule name="${FIREWALL_REGEL}"'
      Pop $1
      Pop $2
      ${If} $1 == 0
        WriteRegDWORD HKCU "${REGKEY}" "Firewall" 1
        DetailPrint "Firewall-Regel angelegt."
      ${Else}
        DetailPrint "Firewall-Regel nicht angelegt – Windows fragt beim ersten Serverstart nach."
      ${EndIf}
    ${EndIf}
  ${EndIf}

  WriteUninstaller "$INSTDIR\Deinstallieren.exe"
  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${TITEL} ($Modus)"
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
  ReadRegDWORD $1 HKCU "${REGKEY}" "Firewall"
  ${If} $1 == 1
  ${AndIfNot} ${Silent}
    ExecShellWait "runas" "$SYSDIR\cmd.exe" '/c netsh advfirewall firewall delete rule name="${FIREWALL_REGEL}"' SW_HIDE
  ${EndIf}
  Delete "$DESKTOP\${TITEL}.lnk"
  Delete "$SMSTARTUP\${TITEL} (Server).lnk"
  RMDir /r "$SMPROGRAMS\${TITEL}"
  !insertmacro OrdnerEntfernen "$INSTDIR\python"
  !insertmacro OrdnerEntfernen "$INSTDIR\programm"
  !insertmacro OrdnerEntfernen "$INSTDIR\pruefung"
  RMDir /r "$INSTDIR\_neu"
  RMDir /r "$INSTDIR\__pycache__"
  Delete "$INSTDIR\netzwerk_starter.py"
  Delete "$INSTDIR\${SYMBOL}"
  Delete "$INSTDIR\einstellungen.ini"
  Delete "$INSTDIR\Deinstallieren.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${REGKEY}"
  ${IfNot} ${Silent}
    MessageBox MB_ICONINFORMATION|MB_OK "${TITEL} wurde von diesem PC entfernt.$\r$\n$\r$\nDie Daten auf dem Netzlaufwerk wurden NICHT gelöscht:$\r$\n$DATEN$\r$\n$\r$\nAuch die Laufwerkszuordnung bleibt bestehen (entfernen im Explorer: Rechtsklick › Trennen)."
  ${EndIf}
SectionEnd
