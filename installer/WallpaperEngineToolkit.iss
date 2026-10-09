; Wallpaper Engine Toolkit — the installer.
;
; Made by "build.cmd installer" from the PyInstaller build in build\stage, with
; Inno Setup 6.3 or later (https://jrsoftware.org/isinfo.php):
;
;   ISCC /DAppVersion=3.19.0 /DSourceDir=<build>\WallpaperEngineToolkit installer\WallpaperEngineToolkit.iss
;
; What it promises (docs/installing.md):
;
; - It installs for the current user only and asks for no administrator rights:
;   %LOCALAPPDATA%\Programs\WallpaperEngineToolkit, the Start menu, and the
;   logon task are all the user's own.
; - It never deletes, moves or overwrites the data, which lives apart from the
;   program in %LOCALAPPDATA%\WallpaperEngineToolkit (app/data_location.py).
;   Uninstalling leaves it where it is, and installing again picks it up.
; - Before it replaces anything it asks the running window and tray tracker to
;   quit (--quit), and waits; it never ends them by force. What will not quit
;   is named, and you are asked to close it.
; - Before an update's new version does anything else, the data is copied
;   aside and checked (--backup-data, app/update_backup.py).
; - On a first install, the data of an earlier copy — a checkout run from
;   source, or a build from before 3.0.0 — can be copied in (--adopt-data).
;   The original is left where it was.
;
; The app does the work on the data itself, where it is tested; this script
; decides when, and stops when a step fails.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=x.y.z (build.cmd does)
#endif
#ifndef SourceDir
  #error Pass the build to package: ISCC /DSourceDir=<the folder WallpaperEngineToolkit.exe is in>
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#if Ver < EncodeVer(6, 3, 0)
  #error Inno Setup 6.3 or later is needed
#endif

#define AppName "Wallpaper Engine Toolkit"
#define ExeName "WallpaperEngineToolkit.exe"
; The names below are the app's own, and must stay what the app calls them:
; app/autostart.py, app/data_location.py, app/app_identity.py, app/branding.py,
; app/window_instance.py, app/tracker_feed.py.
#define TaskName "WallpaperEngineToolkitTracker"
#define LegacyTaskName "WallpaperSuiteTracker"
#define DataFolder "WallpaperEngineToolkit"
#define AppUserModelId "Nykolyn.WallpaperEngineToolkit"
#define StartMenuName "Toolkit"
#define FullName "Toolkit for Wallpaper Engine"
#define Mutexes "Local\WallpaperEngineToolkitWindow,Local\WallpaperEngineToolkitTracker,Local\WallpaperEngineToolkit-data-folder"
#define TrayMutex "Local\WallpaperEngineToolkitTracker"
#define RepoUrl "https://github.com/Nykolyn/wallpaper-engine-toolkit"

[Setup]
; Stable for good: Windows keeps the install under it, and an update finds the
; copy it replaces by it (app/install_info.py has the same).
AppId={{8E307879-3279-4E3D-9EA0-D1BB3A3D456B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Andrii Nykolyn
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
PrivilegesRequired=lowest
DefaultDirName={userpf}\WallpaperEngineToolkit
DisableDirPage=auto
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6 needs Windows 10 1809 or later.
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=WallpaperEngineToolkit-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Running copies are asked to quit by [Code] below, which knows how to ask
; them; the Restart Manager would only find the ones in this folder.
CloseApplications=no
RestartApplications=no
SetupMutex=WallpaperEngineToolkitSetup
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=auto
SetupLogging=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[CustomMessages]
en.AutostartTask=Start the tray tracker when I sign in to Windows
en.DesktopIconTask=Create a desktop shortcut
en.LaunchApp=Open Toolkit now
en.EarlierCaption=Data from an earlier copy
en.EarlierDescription=Have you used Wallpaper Engine Toolkit before, from a folder of your own?
en.EarlierExplanation=A copy run from source (a git clone), or built before version 3.0, keeps its data — settings, the authors database, the rotation history, the Steam key — in a "data" folder of its own. Name that copy's folder, and its data is copied in. The original stays where it is, untouched.%n%nLeave this empty if this is your first time.
en.EarlierFolder=Folder of the earlier copy (optional):
en.EarlierNotData=There is no Toolkit data in this folder, or in a "data" folder inside it.%n%nChoose the folder of the earlier copy, or leave the field empty.
en.DirIsData=This folder is, or holds, the Toolkit's data folder:%n%1%n%nChoose another folder for the program. The data stays where it is.
en.StillRunning=Toolkit is still running and could not be closed for you. It may be in the middle of something, or waiting for an answer.%n%nFinish what it is doing, close its window and quit it from its tray icon (right-click, Quit), then click Retry.
en.StillRunningAbort=Toolkit is still running. Close it and quit it from its tray icon, then run this again.
en.AdoptFailed=The data of the earlier copy could not be copied in:%n%n%1%n%nNothing was lost: all of it is still in%n%2%n%nToolkit was installed but not started, so a second try starts from the same place: run this installer again.
en.BackupFailed=Your data could not be copied aside before the update:%n%n%1%n%nThe new version is installed but was not started, and your data was not touched. Free some disk space, then run this installer again.
en.AutostartFailed=Starting the tracker with Windows could not be set up:%n%n%1%n%nYou can switch it on later, on the Tracker page.
en.DataKept=Your data was kept, in%n%1%n%nIt holds your settings, the authors database, the rotation history and the Steam key. Installing Toolkit again picks it up. If you no longer need it, delete that folder yourself.
en.StepFailedNoReport=it ended with code %1 and left no report

ru.AutostartTask=Запускать трекер в трее при входе в Windows
ru.DesktopIconTask=Создать ярлык на рабочем столе
ru.LaunchApp=Открыть Toolkit
ru.EarlierCaption=Данные из прежней копии
ru.EarlierDescription=Вы уже пользовались Wallpaper Engine Toolkit из своей папки?
ru.EarlierExplanation=Копия, запущенная из исходников (git clone) или собранная до версии 3.0, хранит данные — настройки, базу авторов, историю ротаций, ключ Steam — в своей папке «data». Укажите папку этой копии, и её данные будут скопированы. Оригинал останется на месте нетронутым.%n%nЕсли вы ставите Toolkit впервые, оставьте поле пустым.
ru.EarlierFolder=Папка прежней копии (необязательно):
ru.EarlierNotData=В этой папке и во вложенной папке «data» нет данных Toolkit.%n%nВыберите папку прежней копии или оставьте поле пустым.
ru.DirIsData=Эта папка — папка данных Toolkit или содержит её:%n%1%n%nВыберите для программы другую папку. Данные останутся на месте.
ru.StillRunning=Toolkit всё ещё работает, и закрыть его автоматически не удалось. Возможно, он чем-то занят или ждёт ответа.%n%nДождитесь окончания, закройте окно и выйдите через значок в трее (правый клик, Quit), затем нажмите «Повтор».
ru.StillRunningAbort=Toolkit всё ещё работает. Закройте его и выйдите через значок в трее, затем запустите установку снова.
ru.AdoptFailed=Не удалось скопировать данные прежней копии:%n%n%1%n%nНичего не потеряно: всё по-прежнему лежит в%n%2%n%nToolkit установлен, но не запущен, поэтому повторная попытка начнётся с того же места: запустите установку ещё раз.
ru.BackupFailed=Не удалось сделать резервную копию данных перед обновлением:%n%n%1%n%nНовая версия установлена, но не запущена, а ваши данные не тронуты. Освободите место на диске и запустите установку ещё раз.
ru.AutostartFailed=Не удалось настроить запуск трекера вместе с Windows:%n%n%1%n%nЕго можно включить позже на странице Tracker.
ru.DataKept=Ваши данные сохранены в%n%1%n%nТам настройки, база авторов, история ротаций и ключ Steam. При повторной установке Toolkit подхватит их. Если они больше не нужны, удалите эту папку сами.
ru.StepFailedNoReport=завершилось с кодом %1 без отчёта

uk.AutostartTask=Запускати трекер у треї під час входу в Windows
uk.DesktopIconTask=Створити ярлик на робочому столі
uk.LaunchApp=Відкрити Toolkit
uk.EarlierCaption=Дані з попередньої копії
uk.EarlierDescription=Ви вже користувалися Wallpaper Engine Toolkit зі своєї папки?
uk.EarlierExplanation=Копія, запущена з вихідного коду (git clone) або зібрана до версії 3.0, зберігає дані — налаштування, базу авторів, історію ротацій, ключ Steam — у власній папці «data». Вкажіть папку цієї копії, і її дані буде скопійовано. Оригінал залишиться на місці недоторканим.%n%nЯкщо ви встановлюєте Toolkit уперше, залиште поле порожнім.
uk.EarlierFolder=Папка попередньої копії (необов'язково):
uk.EarlierNotData=У цій папці та у вкладеній папці «data» немає даних Toolkit.%n%nОберіть папку попередньої копії або залиште поле порожнім.
uk.DirIsData=Ця папка — папка даних Toolkit або містить її:%n%1%n%nОберіть для програми іншу папку. Дані залишаться на місці.
uk.StillRunning=Toolkit досі працює, і закрити його автоматично не вдалося. Можливо, він чимось зайнятий або чекає на відповідь.%n%nДочекайтеся завершення, закрийте вікно й вийдіть через значок у треї (правий клік, Quit), потім натисніть «Повторити».
uk.StillRunningAbort=Toolkit досі працює. Закрийте його й вийдіть через значок у треї, потім запустіть встановлення знову.
uk.AdoptFailed=Не вдалося скопіювати дані попередньої копії:%n%n%1%n%nНічого не втрачено: усе й досі лежить у%n%2%n%nToolkit встановлено, але не запущено, тож повторна спроба почнеться з того самого місця: запустіть встановлення ще раз.
uk.BackupFailed=Не вдалося зробити резервну копію даних перед оновленням:%n%n%1%n%nНову версію встановлено, але не запущено, а ваші дані не змінено. Звільніть місце на диску й запустіть встановлення ще раз.
uk.AutostartFailed=Не вдалося налаштувати запуск трекера разом із Windows:%n%n%1%n%nЙого можна ввімкнути пізніше на сторінці Tracker.
uk.DataKept=Ваші дані збережено в%n%1%n%nТам налаштування, база авторів, історія ротацій і ключ Steam. Під час повторного встановлення Toolkit підхопить їх. Якщо вони більше не потрібні, видаліть цю папку самі.
uk.StepFailedNoReport=завершилося з кодом %1 без звіту

[Tasks]
Name: "autostart"; Description: "{cm:AutostartTask}"; Check: IsFirstInstall
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The Start-menu entry the app looks for (app/app_identity.py): its name, and
; the ID Windows names the tray's notifications by.
Name: "{userprograms}\{#StartMenuName}"; Filename: "{app}\{#ExeName}"; AppUserModelID: "{#AppUserModelId}"; Comment: "{#FullName}"
Name: "{userdesktop}\{#FullName}"; Filename: "{app}\{#ExeName}"; AppUserModelID: "{#AppUserModelId}"; Comment: "{#FullName}"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Run]
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent; Check: AllWentWell

[Code]
var
  EarlierPage: TInputDirWizardPage;
  FirstInstall: Boolean;
  HadData: Boolean;
  TrackerWasRunning: Boolean;
  PostInstallFailed: Boolean;

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8E307879-3279-4E3D-9EA0-D1BB3A3D456B}_is1';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

{ ---- where things are ---------------------------------------------------- }

function DataDir: String;
begin
  Result := ExpandConstant('{localappdata}\{#DataFolder}');
end;

{ The data folder exists once the app has marked it as its own. }
function HasDataFolder: Boolean;
begin
  Result := FileExists(DataDir + '\data-folder.json');
end;

{ Decided once, at the start: Windows has the install's entry before the
  last steps run, and the autostart task must not change its mind then. }
function IsFirstInstall: Boolean;
begin
  Result := FirstInstall;
end;

function InitializeSetup: Boolean;
begin
  FirstInstall := not RegKeyExists(HKCU, UninstallKey);
  Result := True;
end;

function InstalledExe: String;
var
  Location: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', Location) then
    if FileExists(AddBackslash(Location) + '{#ExeName}') then
      Result := AddBackslash(Location) + '{#ExeName}';
end;

function Within(const Dir, Parent: String): Boolean;
begin
  Result := Pos(Lowercase(AddBackslash(Parent)), Lowercase(AddBackslash(Dir))) = 1;
end;

{ ---- an earlier copy's data ---------------------------------------------- }

function HasKnownFile(const Dir: String): Boolean;
begin
  Result := FileExists(Dir + '\suite.json') or FileExists(Dir + '\config.json')
    or FileExists(Dir + '\history.json') or FileExists(Dir + '\authors.sqlite')
    or FileExists(Dir + '\tracker.json') or FileExists(Dir + '\secrets.json');
end;

{ The data folder of an earlier copy: Folder\data, or Folder itself; '' if neither
  holds any. The same test as data_location.data_in. }
function DataIn(const Folder: String): String;
var
  Dir: String;
begin
  Result := '';
  Dir := RemoveBackslashUnlessRoot(Trim(Folder));
  if Dir = '' then
    exit;
  if HasKnownFile(Dir + '\data') then
    Result := Dir + '\data'
  else if HasKnownFile(Dir) then
    Result := Dir;
end;

{ Split a command line into its program and the rest. }
procedure SplitCommand(const Line: String; var Prog, Rest: String);
var
  S: String;
  P: Integer;
begin
  S := Trim(Line);
  Prog := S;
  Rest := '';
  if Copy(S, 1, 1) = '"' then
  begin
    S := Copy(S, 2, Length(S));
    P := Pos('"', S);
    if P > 0 then
    begin
      Prog := Copy(S, 1, P - 1);
      Rest := Trim(Copy(S, P + 1, Length(S)));
    end else
      Prog := S;
  end else
  begin
    P := Pos(' ', S);
    if P > 0 then
    begin
      Prog := Copy(S, 1, P - 1);
      Rest := Trim(Copy(S, P + 1, Length(S)));
    end;
  end;
end;

{ What a logon task runs. False if there is no such task. }
function TaskAction(const Name: String; var Prog, Arguments: String): Boolean;
var
  Service, Folder, Task, Action: Variant;
begin
  Result := False;
  Prog := '';
  Arguments := '';
  try
    Service := CreateOleObject('Schedule.Service');
    Service.Connect();
    Folder := Service.GetFolder('\');
    Task := Folder.GetTask(Name);
    Action := Task.Definition.Actions.Item(1);
    Prog := Action.Path;
    Arguments := Action.Arguments;
    Result := Prog <> '';
  except
    Result := False;
  end;
end;

{ The folder of the copy an autostart entry starts: beside run_app.py for a
  checkout, beside the exe for a build. }
function CopyFolderOf(const Prog, Arguments: String): String;
var
  Name, Script, Rest: String;
begin
  Name := Lowercase(ExtractFileName(Prog));
  if (Name = 'python.exe') or (Name = 'pythonw.exe') then
  begin
    SplitCommand(Arguments, Script, Rest);
    Result := ExtractFileDir(Script);
  end else
    Result := ExtractFileDir(Prog);
end;

{ An earlier copy that keeps data of its own, found from its autostart entry
  (the logon task, or the Run key it falls back to; old names too). }
function FoundEarlierCopy: String;
var
  Names: TArrayOfString;
  I: Integer;
  Prog, Arguments, Line, Folder: String;
begin
  Result := '';
  SetArrayLength(Names, 2);
  Names[0] := '{#TaskName}';
  Names[1] := '{#LegacyTaskName}';
  for I := 0 to 1 do
  begin
    if TaskAction(Names[I], Prog, Arguments) then
    begin
      Folder := CopyFolderOf(Prog, Arguments);
      if DataIn(Folder) <> '' then
      begin
        Result := Folder;
        exit;
      end;
    end;
    if RegQueryStringValue(HKCU, RunKey, Names[I], Line) then
    begin
      SplitCommand(Line, Prog, Arguments);
      Folder := CopyFolderOf(Prog, Arguments);
      if DataIn(Folder) <> '' then
      begin
        Result := Folder;
        exit;
      end;
    end;
  end;
end;

function EarlierChoice: String;
begin
  Result := '';
  if (EarlierPage <> nil) and not HadData then
    Result := Trim(EarlierPage.Values[0]);
end;

{ ---- running copies -------------------------------------------------------- }

{ Whether a WallpaperEngineToolkit.exe process exists, from any folder. }
function ProgramRunning: Boolean;
var
  Locator, Service, Found: Variant;
begin
  Result := False;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Found := Service.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE Name = ''{#ExeName}''');
    Result := Found.Count > 0;
  except
    Result := False;
  end;
end;

{ A window or a tracker holds its mutex from soon after it starts until it
  ends, and listens for the request to quit while it does. A process of the
  toolkit runs for a few seconds before it takes its mutex, though: one just
  started, or the tracker an update has just started again. }
function AnythingRunning: Boolean;
begin
  Result := CheckForMutexes('{#Mutexes}') or ProgramRunning;
end;

{ Give a copy that is still starting the time to come up and take its mutex,
  so that it is asked to quit rather than missed. }
procedure WaitWhileStarting;
var
  I: Integer;
begin
  for I := 1 to 20 * 4 do
  begin
    if CheckForMutexes('{#Mutexes}') or not ProgramRunning then
      exit;
    Sleep(250);
  end;
end;

function WaitUntilGone(Seconds: Integer): Boolean;
var
  I: Integer;
begin
  for I := 1 to Seconds * 4 do
  begin
    if not AnythingRunning then
      break;
    Sleep(250);
  end;
  Result := not AnythingRunning;
end;

{ Ask everything of the toolkit that runs to quit, and wait for it. A copy
  installed by this installer (3.19.0 and later) asks the window and the
  tracker itself; a tracker started by the logon task — of any version — is
  ended through the task, which lets it shut down properly. What still runs
  after that is left to you: nothing is ended by force. }
function StopRunningCopies(Exe: String): String;
var
  Code: Integer;
begin
  Result := '';
  repeat
    WaitWhileStarting;
    if not AnythingRunning then
      exit;
    if (Exe <> '') and FileExists(Exe) then
      Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
    if AnythingRunning then
    begin
      Exec(ExpandConstant('{sys}\schtasks.exe'), '/end /tn "{#TaskName}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
      Exec(ExpandConstant('{sys}\schtasks.exe'), '/end /tn "{#LegacyTaskName}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
    end;
    if WaitUntilGone(10) then
      exit;
    Log('Toolkit is still running after it was asked to quit');
  until SuppressibleMsgBox(CustomMessage('StillRunning'), mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY;
  Result := CustomMessage('StillRunningAbort');
end;

{ ---- the app's own steps --------------------------------------------------- }

{ Run the installed exe with Params; True if it says it worked. Said is what it
  reported, read from the file it writes with --report. }
function RunStep(const Params: String; var Said: String): Boolean;
var
  Report: String;
  Lines: TArrayOfString;
  I, Code: Integer;
begin
  Report := ExpandConstant('{tmp}\step-report.txt');
  DeleteFile(Report);
  Said := '';
  Result := Exec(ExpandConstant('{app}\{#ExeName}'), Params + ' --report "' + Report + '"',
                 ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  if LoadStringsFromFile(Report, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      Said := Trim(Said + ' ' + Lines[I]);
  if Said = '' then
    Said := FmtMessage(CustomMessage('StepFailedNoReport'), [IntToStr(Code)]);
  Log(Params + ': ' + Said);
end;

function TaskStartsThisCopy: Boolean;
var
  Prog, Arguments: String;
begin
  Result := TaskAction('{#TaskName}', Prog, Arguments)
    and (CompareText(Prog, ExpandConstant('{app}\{#ExeName}')) = 0);
end;

procedure StartTracker;
var
  Code: Integer;
begin
  if TaskStartsThisCopy then
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/run /tn "{#TaskName}"', '', SW_HIDE, ewWaitUntilTerminated, Code)
  else
    Exec(ExpandConstant('{app}\{#ExeName}'), '--tracker', ExpandConstant('{app}'), SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure AfterFilesAreInstalled;
var
  Folder, Said: String;
begin
  { 1. A first install told where an earlier copy is copies its data in, before
       anything else can make a new, empty data folder. }
  Folder := EarlierChoice;
  if Folder <> '' then
    if not RunStep('--adopt-data "' + RemoveBackslashUnlessRoot(Folder) + '"', Said) then
    begin
      PostInstallFailed := True;
      SuppressibleMsgBox(FmtMessage(CustomMessage('AdoptFailed'), [Said, DataIn(Folder)]),
                         mbError, MB_OK, IDOK);
      exit;
    end;

  { 2. An update copies the data aside before the new version does anything else. }
  if HadData then
    if not RunStep('--backup-data', Said) then
    begin
      PostInstallFailed := True;
      SuppressibleMsgBox(FmtMessage(CustomMessage('BackupFailed'), [Said]), mbError, MB_OK, IDOK);
      exit;
    end;

  { 3. Autostart, if asked for: the logon task now starts this copy. }
  if WizardIsTaskSelected('autostart') then
    if not RunStep('--autostart on', Said) then
      SuppressibleMsgBox(FmtMessage(CustomMessage('AutostartFailed'), [Said]), mbError, MB_OK, IDOK);

  { 4. The tracker again, if it was running or has just been asked for. }
  if TrackerWasRunning or WizardIsTaskSelected('autostart') then
    StartTracker;
end;

{ ---- the wizard ------------------------------------------------------------- }

procedure InitializeWizard;
var
  Given: String;
begin
  EarlierPage := CreateInputDirPage(wpSelectDir, CustomMessage('EarlierCaption'),
    CustomMessage('EarlierDescription'), CustomMessage('EarlierExplanation'), False, '');
  EarlierPage.Add(CustomMessage('EarlierFolder'));
  { /EarlierCopy="<folder>" names it outright, as a silent install needs. }
  Given := ExpandConstant('{param:EarlierCopy|}');
  if Given <> '' then
    EarlierPage.Values[0] := Given
  else if not HasDataFolder then
    EarlierPage.Values[0] := FoundEarlierCopy;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Only a first start on this Windows account has no data folder to keep. }
  Result := (EarlierPage <> nil) and (PageID = EarlierPage.ID) and HasDataFolder;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Dir := WizardDirValue;
    if Within(Dir, DataDir) or Within(DataDir, Dir) then
    begin
      SuppressibleMsgBox(FmtMessage(CustomMessage('DirIsData'), [DataDir]), mbError, MB_OK, IDOK);
      Result := False;
    end;
  end else if (EarlierPage <> nil) and (CurPageID = EarlierPage.ID) then
  begin
    if (Trim(EarlierPage.Values[0]) <> '') and (DataIn(EarlierPage.Values[0]) = '') then
    begin
      SuppressibleMsgBox(CustomMessage('EarlierNotData'), mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  HadData := HasDataFolder;
  if (EarlierChoice <> '') and (DataIn(EarlierChoice) = '') then
  begin
    { Only a silent install can get here with a folder that holds no data. }
    Result := CustomMessage('EarlierNotData');
    exit;
  end;
  TrackerWasRunning := CheckForMutexes('{#TrayMutex}');
  Result := StopRunningCopies(InstalledExe);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    { An update replaces the bundle whole: files an older build had and this
      one does not would otherwise be left in it. Only in a folder that is this
      program's — the exe beside it says so. }
    if FileExists(ExpandConstant('{app}\{#ExeName}')) then
      DelTree(ExpandConstant('{app}\_internal'), True, True, True);
  end else if CurStep = ssPostInstall then
    AfterFilesAreInstalled;
end;

function AllWentWell: Boolean;
begin
  Result := not PostInstallFailed;
end;

{ ---- uninstalling ----------------------------------------------------------- }

function InitializeUninstall: Boolean;
var
  Stopped: String;
begin
  Stopped := StopRunningCopies(ExpandConstant('{app}\{#ExeName}'));
  Result := Stopped = '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    { Autostart goes only if it starts this copy, not one of your own builds. }
    Exec(ExpandConstant('{app}\{#ExeName}'), '--autostart release', ExpandConstant('{app}'),
         SW_HIDE, ewWaitUntilTerminated, Code)
  else if CurUninstallStep = usPostUninstall then
    if DirExists(DataDir) and not UninstallSilent then
      MsgBox(FmtMessage(CustomMessage('DataKept'), [DataDir]), mbInformation, MB_OK);
end;
