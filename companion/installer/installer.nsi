; Nojoin Companion Installer Script
; Requires NSIS 3.x with MUI2

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; Application Information
!define PRODUCT_NAME "Nojoin Companion"
!define PRODUCT_VERSION "0.1.1"
!define PRODUCT_PUBLISHER "Valtora"
!define PRODUCT_WEB_SITE "https://github.com/Valtora/Nojoin"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\nojoin-companion.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
!define PRODUCT_UNINST_ROOT_KEY "HKCU"

; Installer Settings
Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\Nojoin-Companion-Setup.exe"
InstallDir "$LOCALAPPDATA\Nojoin"
InstallDirRegKey HKCU "${PRODUCT_DIR_REGKEY}" ""
RequestExecutionLevel user
ShowInstDetails show
ShowUnInstDetails show

; Enable installer logging
!define LOGFILE "$INSTDIR\install.log"

; Installer Compression
SetCompressor /SOLID lzma

; MUI Settings
!define MUI_ABORTWARNING
!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
; Optional: Add custom bitmaps for the installer UI
; !define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
; !define MUI_HEADERIMAGE
; !define MUI_HEADERIMAGE_BITMAP "header.bmp"

; Welcome page
!insertmacro MUI_PAGE_WELCOME

; License page (optional - uncomment if you have a license)
; !insertmacro MUI_PAGE_LICENSE "LICENSE.txt"

; Directory page
!insertmacro MUI_PAGE_DIRECTORY

; Components page
!insertmacro MUI_PAGE_COMPONENTS

; Install files page
!insertmacro MUI_PAGE_INSTFILES

; Finish page
!define MUI_FINISHPAGE_RUN "$INSTDIR\nojoin-companion.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${PRODUCT_NAME}"
!insertmacro MUI_PAGE_FINISH

; Uninstaller pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Language
!insertmacro MUI_LANGUAGE "English"

; Variables
Var StartMenuGroup
Var ConfigBackup

; Logging macro - must be defined before use
!macro WriteToLog MSG
    FileOpen $9 "$INSTDIR\install.log" a
    FileSeek $9 0 END
    FileWrite $9 "${MSG}$\r$\n"
    FileClose $9
!macroend

; Installer Sections
Section "Nojoin Companion" SEC_MAIN
    SectionIn RO ; Required section
    
    ; Set output path first so we can write logs
    SetOutPath "$INSTDIR"
    
    ; Initialize log file
    FileOpen $0 "$INSTDIR\install.log" w
    FileWrite $0 "=== Nojoin Companion Installation Log ===$\r$\n"
    FileWrite $0 "Version: ${PRODUCT_VERSION}$\r$\n"
    FileWrite $0 "Install Directory: $INSTDIR$\r$\n"
    FileWrite $0 "=======================================$\r$\n$\r$\n"
    FileClose $0
    
    !insertmacro WriteToLog "Starting installation..."
    
    ; Check if application is running and terminate it
    !insertmacro WriteToLog "Checking for running instances..."
    Call CloseRunningApp
    !insertmacro WriteToLog "Running instances terminated."
    
    ; Backup existing config.json if it exists
    !insertmacro WriteToLog "Checking for existing config..."
    ${If} ${FileExists} "$INSTDIR\config.json"
        !insertmacro WriteToLog "Backing up existing config.json"
        CopyFiles /SILENT "$INSTDIR\config.json" "$TEMP\nojoin_config_backup.json"
        StrCpy $ConfigBackup "1"
    ${EndIf}
    
    SetOverwrite on
    
    ; Install files
    !insertmacro WriteToLog "Installing nojoin-companion.exe..."
    File "..\target\release\nojoin-companion.exe"
    !insertmacro WriteToLog "nojoin-companion.exe installed successfully."
    
    ; Verify the exe was installed
    ${If} ${FileExists} "$INSTDIR\nojoin-companion.exe"
        !insertmacro WriteToLog "nojoin-companion.exe verified."
    ${Else}
        !insertmacro WriteToLog "ERROR: nojoin-companion.exe not found after installation!"
        Abort "Failed to install nojoin-companion.exe"
    ${EndIf}
    
    ; Restore config.json if it was backed up
    ${If} $ConfigBackup == "1"
        !insertmacro WriteToLog "Restoring config.json from backup..."
        CopyFiles /SILENT "$TEMP\nojoin_config_backup.json" "$INSTDIR\config.json"
        Delete "$TEMP\nojoin_config_backup.json"
    ${EndIf}
    
    ; Create default config if none exists
    ${If} ${FileExists} "$INSTDIR\config.json"
        !insertmacro WriteToLog "Config file already exists."
    ${Else}
        !insertmacro WriteToLog "Creating default config.json..."
        FileOpen $0 "$INSTDIR\config.json" w
        FileWrite $0 '{$\r$\n  "api_port": 14443,$\r$\n  "api_token": "",$\r$\n  "local_port": 12345$\r$\n}$\r$\n'
        FileClose $0
    ${EndIf}
    
    ; Store installation folder
    !insertmacro WriteToLog "Writing registry keys..."
    WriteRegStr HKCU "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\nojoin-companion.exe"
    
    ; Create uninstaller
    !insertmacro WriteToLog "Creating uninstaller..."
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    
    ; Write uninstall registry keys
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\nojoin-companion.exe"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
    WriteRegDWORD ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "NoModify" 1
    WriteRegDWORD ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "NoRepair" 1
    
    ; Calculate installed size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "EstimatedSize" "$0"
    
    !insertmacro WriteToLog "Installation completed successfully."
SectionEnd

Section "Start Menu Shortcuts" SEC_STARTMENU
    StrCpy $StartMenuGroup "${PRODUCT_NAME}"
    
    CreateDirectory "$SMPROGRAMS\$StartMenuGroup"
    CreateShortCut "$SMPROGRAMS\$StartMenuGroup\${PRODUCT_NAME}.lnk" "$INSTDIR\nojoin-companion.exe" "" "$INSTDIR\nojoin-companion.exe" 0
    CreateShortCut "$SMPROGRAMS\$StartMenuGroup\Uninstall.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\Uninstall.exe" 0
SectionEnd

Section "Desktop Shortcut" SEC_DESKTOP
    CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\nojoin-companion.exe" "" "$INSTDIR\nojoin-companion.exe" 0
SectionEnd

Section "Run on Startup" SEC_STARTUP
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCT_NAME}" "$INSTDIR\nojoin-companion.exe"
SectionEnd

; Section Descriptions
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "Install the core ${PRODUCT_NAME} application. (Required)"
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTMENU} "Create shortcuts in the Start Menu."
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP} "Create a shortcut on the Desktop."
    !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTUP} "Automatically start ${PRODUCT_NAME} when Windows starts."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; Functions

Function CloseRunningApp
    ; Try to close any running instance gracefully
    FindWindow $0 "" "${PRODUCT_NAME}"
    ${If} $0 != 0
        ; Send WM_CLOSE message
        SendMessage $0 16 0 0 ; WM_CLOSE = 16
        Sleep 1000
    ${EndIf}
    
    ; Force kill if still running
    nsExec::ExecToLog 'taskkill /F /IM nojoin-companion.exe'
    Sleep 500
FunctionEnd

Function .onInit
    ; Check for existing installation
    ReadRegStr $0 HKCU "${PRODUCT_DIR_REGKEY}" ""
    ${If} $0 != ""
        ${If} ${FileExists} $0
            ; Get the installation directory from the existing install
            ${GetParent} $0 $INSTDIR
        ${EndIf}
    ${EndIf}
FunctionEnd

; Uninstaller Section
Section "Uninstall"
    ; Close running application
    nsExec::ExecToLog 'taskkill /F /IM nojoin-companion.exe'
    Sleep 500
    
    ; Remove files
    Delete "$INSTDIR\nojoin-companion.exe"
    Delete "$INSTDIR\Uninstall.exe"
    
    ; Optionally remove config (ask user)
    MessageBox MB_YESNO "Do you want to remove your settings (config.json)?" IDNO skip_config_delete
    Delete "$INSTDIR\config.json"
    skip_config_delete:
    
    ; Remove installation directory if empty
    RMDir "$INSTDIR"
    
    ; Remove Start Menu shortcuts
    Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
    Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk"
    RMDir "$SMPROGRAMS\${PRODUCT_NAME}"
    
    ; Remove Desktop shortcut
    Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
    
    ; Remove startup entry
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCT_NAME}"
    
    ; Remove registry keys
    DeleteRegKey ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}"
    DeleteRegKey HKCU "${PRODUCT_DIR_REGKEY}"
SectionEnd

Function un.onUninstSuccess
    HideWindow
    MessageBox MB_ICONINFORMATION|MB_OK "${PRODUCT_NAME} was successfully uninstalled."
FunctionEnd

