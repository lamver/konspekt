; Установщик Konspekt.
;
; Ставим в папку пользователя (AppData\Local), а не в Program Files:
; тогда установка не просит прав администратора. Для программы, которую
; человек пробует по совету знакомого, запрос UAC на входе это лишний
; повод отказаться. Заодно снимается вопрос с правами на запись.
;
; Версию передаёт сборочный скрипт: /DAppVersion=0.1.0

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Konspekt"
#define AppExe "Konspekt.exe"

[Setup]
AppId={{8C4A6F42-6E0B-4F0E-9E0E-6D1B3B5B7A21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Konspekt
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Права администратора не нужны: ставим только в свою папку.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=konspekt-{#AppVersion}-setup
SetupIconFile=konspekt.ico
UninstallDisplayIcon={app}\{#AppExe}
; LZMA2 с большим словарём: дистрибутив в основном это уже сжатые .pyd и
; .dll, но словарь в 128 МБ забирает ещё десятки мегабайт.
Compression=lzma2/max
LZMADictionarySize=131072
SolidCompression=yes
WizardStyle=modern
; Windows 10 и новее: WebView2 на более старых недоступен.
MinVersion=10.0

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Запускать при входе в систему"; GroupDescription: "Дополнительно"; Flags: unchecked

[Files]
Source: "..\dist\Konspekt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Тихое обновление из самой программы: она закрылась ради подмены файлов
; и должна вернуться, иначе пропавший значок в трее выглядит поломкой.
; Обычная тихая установка (без ключа) ничего не запускает.
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent; Check: RestartRequested

[UninstallDelete]
; Кэш webview принадлежит нам и после удаления не нужен. Встречи, записи и
; настройки не трогаем: человек может ставить заново, а данные ему дороже
; программы. Удаление данных делается из самого приложения, осознанно.
Type: filesandordirs; Name: "{localappdata}\{#AppName}\EBWebView"

[Code]
// Ключ /RESTARTKONSPEKT передаёт сама программа при тихом обновлении.
function RestartRequested(): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), '/RESTARTKONSPEKT') = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

// Перед установкой закрываем запущенную копию: иначе файлы заняты и
// обновление поверх падает на середине.
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
