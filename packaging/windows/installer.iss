; Instalador do Deadlock Mods (Inno Setup). Gerado pelo packaging/build-windows.sh:
;   ISCC /DAppVersion=x.y.z /DSrcDir=<dist\DeadlockMods> /DOutDir=<dist> /DIconFile=<icon.ico> installer.iss
; Instala só pro usuário (sem pedir admin) em %LOCALAPPDATA%\Programs\DeadlockMods.

#define AppName "Deadlock Mods"

[Setup]
AppId={{6F3C2A51-9B7E-4D2C-A8E1-DEAD10C4D0D5}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Valentim
DefaultDirName={localappdata}\Programs\DeadlockMods
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=DeadlockMods-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\DeadlockMods.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; atualizar por cima com o app aberto: fecha ele antes
CloseApplications=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; versão anterior: tira o _internal velho pra não sobrar dll de outra versão
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\DeadlockMods.exe"
Name: "{group}\Coletar logs do Deadlock Mods"; Filename: "{app}\coletar-logs.bat"; IconFilename: "{sys}\shell32.dll"; IconIndex: 23
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DeadlockMods.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DeadlockMods.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; atualização automática (o app roda o Setup com /VERYSILENT): reabre sozinho no fim
Filename: "{app}\DeadlockMods.exe"; Flags: nowait; Check: WizardSilent

; Os mods baixados (%LOCALAPPDATA%\deadlock-mods) e os da pasta addons do jogo ficam: desinstalar o app não mexe no jogo.
