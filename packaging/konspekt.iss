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

#ifndef SetupCompression
  #define SetupCompression "zip"
#endif

#define AppName "Konspekt"
#define AppExe "Konspekt.exe"

[Setup]
AppId={{8C4A6F42-6E0B-4F0E-9E0E-6D1B3B5B7A21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Valerij Frolov
AppPublisherURL=https://konspekt.aisearch.ru
AppSupportURL=https://github.com/lamver/konspekt-releases/issues
AppCopyright=© 2026 Valerij Frolov
; Сведения о файле установщика. Без версии Inno пишет 0.0.0.0, а
; безымянный установщик антивирусы с машинным обучением любят меньше.
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Valerij Frolov
VersionInfoDescription=Konspekt Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCopyright=© 2026 Valerij Frolov
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Права администратора не нужны: ставим только в свою папку.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=konspekt-{#AppVersion}-setup
SetupIconFile=konspekt.ico
UninstallDisplayIcon={app}\{#AppExe}
; Сжатие zip, а не lzma2/max, хотя установщик выходит 89 МБ вместо 61.
; 01.10: с lzma2/max Microsoft ставил Wacatac!ml каждой сборке подряд при
; любом коде, а с zip четыре сборки из четырёх чистые. Сильно сжатый
; файл машинное обучение антивирусов принимает за упакованный вредонос.
Compression={#SetupCompression}
#if Pos("lzma", SetupCompression) > 0
LZMADictionarySize=131072
#endif
SolidCompression=yes
WizardStyle=modern
; Windows 10 и новее: WebView2 на более старых недоступен.
MinVersion=10.0
; Пока идёт установка, этот замок держит установщик, а программа на
; старте ждёт, пока он освободится (packaging/launcher.py). Иначе
; Конспект, запущенный посреди тихого обновления (значок пропал, человек
; жмёт ярлык), занимает файлы, и обновление встаёт наполовину.
SetupMutex=KonspektSetup

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

// Перед подменой файлов закрываем все запущенные копии: иначе файлы
// заняты, и обновление встаёт наполовину. Задача №5 (05.10): новый exe
// с номером 0.15.3, а в _internal окно от 0.11, и «DeleteFile: код 5»
// на PIL\_imaging.pyd.
//
// Закрываем прямо перед копированием, а не при старте установщика: пока
// человек листает страницы, Конспект успевает запуститься снова. И ждём,
// пока копий не останется: taskkill только просит систему завершить
// процесс, а файлы освобождаются, когда он умер на самом деле.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode, i: Integer;
begin
  for i := 1 to 40 do
  begin
    // 128 — таких процессов нет.
    if not Exec('taskkill.exe', '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
       or (ResultCode = 128) then
      Break;
    Sleep(250);
  end;
  Result := '';
end;
