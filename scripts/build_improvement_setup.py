from pathlib import Path
from textwrap import dedent
import ast
import subprocess
import sys

root = Path.cwd()
stage = root / "release" / "ImprovementAuto_0.2.0"
assets = root / "release" / "installer_assets"
database = assets / "database" / "improvement_auto_initial.dump"
postgres = list((assets / "postgresql17").glob("*.exe"))

compilers = [
    Path(r"C:\Users\Неукротимая\AppData\Local\Programs\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]

compiler = next((p for p in compilers if p.is_file()), None)

if not (stage / "backend/app/main.py").is_file():
    raise SystemExit("STOP: Release backend missing")

if not (stage / "deploy/frontend_releases/active.json").is_file():
    raise SystemExit("STOP: Frontend missing")

if len(postgres) != 1 or not database.is_file():
    raise SystemExit("STOP: PostgreSQL installer or database missing")

if not (root / "scripts/cut_backup_retention_prod.py").is_file():
    raise SystemExit("STOP: Backup retention script missing")

if compiler is None:
    raise SystemExit("STOP: Inno Setup compiler missing")

output = root / "release" / "final_installer"
output.mkdir(parents=True, exist_ok=True)

launcher = r'''
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import webbrowser
import zipfile

from datetime import datetime
from pathlib import Path
from threading import Thread
from urllib.request import urlopen


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    base = Path(sys._MEIPASS)

    home = Path(
        os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    ) / "ImprovementAuto"

    config = home / "settings.env"

    if not config.is_file():
        raise RuntimeError("Installation settings missing")

    from dotenv import dotenv_values

    settings = dotenv_values(config)

    for key, value in settings.items():
        if value is not None:
            os.environ[key] = value

    if os.environ.get("DB_NAME") != "cut_improvement_auto_prod":
        raise RuntimeError("Wrong production database")

    if not os.environ.get("DB_PASSWORD"):
        raise RuntimeError("Database password missing")

    if not os.environ.get("MAX_BOT_TOKEN"):
        os.environ["CUT_MAX_ALL_CLIENTS_ENABLED"] = "0"

    (home / "logs").mkdir(parents=True, exist_ok=True)

    os.chdir(base)
    sys.path.insert(0, str(base / "backend"))

    if "--backup" in sys.argv:
        from app.db.session import engine

        if engine.url.database != "cut_improvement_auto_prod":
            raise RuntimeError("Backup database mismatch")

        pg = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        ) / "PostgreSQL" / "17" / "bin"

        backup_root = home / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)

        temporary = Path(tempfile.mkdtemp(
            prefix="._unfinished_",
            dir=str(backup_root)
        ))

        try:
            db_file = temporary / "postgresql.dump"
            data_file = temporary / "data.zip"

            env = os.environ.copy()
            env["PGPASSWORD"] = engine.url.password or ""

            command = [
                str(pg / "pg_dump.exe"),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--host", engine.url.host or "127.0.0.1",
                "--port", str(engine.url.port or 5432),
                "--username", engine.url.username,
                "--dbname", engine.url.database,
                "--file", str(db_file),
            ]

            result = subprocess.run(
                command, env=env, capture_output=True
            )

            if result.returncode or not db_file.is_file():
                raise RuntimeError("PostgreSQL backup failed")

            check = subprocess.run(
                [str(pg / "pg_restore.exe"),
                 "--list", str(db_file)],
                capture_output=True,
            )

            if check.returncode:
                raise RuntimeError("Backup catalogue invalid")

            count = 0

            with zipfile.ZipFile(
                data_file, "w", zipfile.ZIP_DEFLATED
            ) as archive:
                for prefix, source in [
                    ("data", home / "data"),
                    ("runtime_data", base / "data"),
                ]:
                    if not source.is_dir():
                        continue

                    for item in sorted(source.rglob("*")):
                        if item.is_symlink():
                            raise RuntimeError(
                                "Unsafe data symlink"
                            )

                        if item.is_file():
                            name = (
                                prefix + "/" +
                                item.relative_to(source).as_posix()
                            )
                            archive.write(item, name)
                            count += 1

            with zipfile.ZipFile(data_file) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("Invalid data archive")

            manifest = {
                "database": engine.url.database,
                "created_local": datetime.now().astimezone().isoformat(),
                "database_archive": {
                    "file": "postgresql.dump",
                    "bytes": db_file.stat().st_size,
                    "sha256": checksum(db_file),
                },
                "data_archive": {
                    "file": "data.zip",
                    "files": count,
                    "bytes": data_file.stat().st_size,
                    "sha256": checksum(data_file),
                },
            }

            (temporary / "manifest.json").write_text(
                json.dumps(
                    manifest,
                    indent=2,
                    ensure_ascii=False
                ),
                encoding="utf-8",
            )

            final = backup_root / datetime.now().strftime(
                "backup_%Y%m%d_%H%M%S"
            )

            if final.exists():
                raise RuntimeError("Backup folder already exists")

            temporary.rename(final)

            sys.path.insert(
                0, str(base / "retention")
            )

            from cut_backup_retention_prod import prune_backup_root

            try:
                prune_backup_root(backup_root, final)
            except Exception as error:
                with (home / "logs" / "backup_errors.log").open(
                    "a", encoding="utf-8"
                ) as log:
                    log.write(
                        "Retention stopped safely: " +
                        str(error) + "\n"
                    )

            return

        finally:
            if temporary.is_dir():
                shutil.rmtree(temporary)

    url = "http://127.0.0.1:8000/"

    def is_running():
        try:
            with urlopen(url, timeout=1) as response:
                return response.status < 500
        except Exception:
            return False

    if "--background" not in sys.argv:
        if is_running():
            webbrowser.open(url)
            return

        def open_when_ready():
            for _ in range(90):
                if is_running():
                    webbrowser.open(url)
                    return
                time.sleep(1)

        Thread(
            target=open_when_ready,
            daemon=True
        ).start()

    from sqlalchemy import text
    from app.db.session import engine

    for _ in range(90):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError("PostgreSQL unavailable")

    from app.main import app
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        lifespan="on",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        home = Path(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        ) / "ImprovementAuto" / "logs"

        home.mkdir(parents=True, exist_ok=True)

        with (home / "fatal.log").open(
            "a", encoding="utf-8"
        ) as log:
            traceback.print_exc(file=log)

        if not any(
            arg in sys.argv
            for arg in ("--background", "--backup")
        ):
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                str(error),
                "Improvement Auto",
                0x10,
            )

        sys.exit(1)
'''

ast.parse(launcher)

launcher_path = output / "launcher.py"
launcher_path.write_text(
    launcher,
    encoding="utf-8",
)

task_folder = output / "_setup"
task_folder.mkdir(exist_ok=True)

task_script = task_folder / "register_backup.ps1"

task_script.write_text(r'''
$ErrorActionPreference = "Stop"

$exe = Join-Path (
    Split-Path -Parent $PSScriptRoot
) "ImprovementAuto.exe"

$action = New-ScheduledTaskAction `
    -Execute $exe `
    -Argument "--backup"

$trigger = New-ScheduledTaskTrigger -Daily -At "12:00"

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName "Improvement Auto Daily Backup" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null
''', encoding="utf-8-sig")

print("=== BUILD FINAL EXE ===", flush=True)

command = [
    sys.executable,
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--noconsole",
    "--name", "ImprovementAuto",
    "--distpath", str(output / "dist"),
    "--workpath", str(output / "work"),
    "--specpath", str(output),
    "--paths", str(stage / "backend"),
    "--add-data", str(stage / "backend") + ";backend",
    "--add-data", str(stage / "deploy") + ";deploy",
    "--add-data",
    str(root / "scripts/cut_backup_retention_prod.py")
        + ";retention",
    "--collect-all", "uvicorn",
    "--collect-submodules", "app",
    str(launcher_path),
]

if subprocess.run(command, cwd=root).returncode:
    raise SystemExit("STOP: EXE build failed")

exe = output / "dist/ImprovementAuto/ImprovementAuto.exe"

if not exe.is_file():
    raise SystemExit("STOP: Final executable missing")

print("OK: FINAL EXE CREATED", flush=True)

iss = r'''
#define AppName "Improvement Auto"

[Setup]
AppId={{93D980D7-10AA-4C59-B503-FE82C0B846ED}
AppName={#AppName}
AppVersion=0.2.0
DefaultDirName={autopf}\Improvement Auto
DefaultGroupName=Improvement Auto
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir=@@OUTPUT@@
OutputBaseFilename=Improvement Auto Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "@@DIST@@\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "@@PG@@"; DestDir: "{tmp}"; DestName: "postgresql17.exe"; Flags: deleteafterinstall
Source: "@@DB@@"; DestDir: "{tmp}"; DestName: "initial.dump"; Flags: deleteafterinstall
Source: "@@TASK@@"; DestDir: "{app}\_setup"; DestName: "register_backup.ps1"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\ImprovementAuto"
Name: "{commonappdata}\ImprovementAuto\logs"; Permissions: users-modify
Name: "{commonappdata}\ImprovementAuto\data"; Permissions: users-modify
Name: "{commonappdata}\ImprovementAuto\backups"; Permissions: users-modify
Name: "{app}\_internal\data"; Permissions: users-modify

[Icons]
Name: "{autoprograms}\Improvement Auto"; Filename: "{app}\ImprovementAuto.exe"
Name: "{autodesktop}\Improvement Auto"; Filename: "{app}\ImprovementAuto.exe"
Name: "{commonstartup}\Improvement Auto Server"; Filename: "{app}\ImprovementAuto.exe"; Parameters: "--background"

[Run]
Filename: "{app}\ImprovementAuto.exe"; Parameters: "--background"; Flags: nowait runhidden

[Code]
var
  PgPage, AppPage, MaxPage: TInputQueryWizardPage;
  PgBin: string;

function SetEnvironmentVariable(Name, Value: string): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure InitializeWizard;
begin
  PgPage := CreateInputQueryPage(
    wpSelectDir,
    'PostgreSQL',
    'Настройка базы данных СТО',
    'Создайте пароль администратора PostgreSQL.'
  );
  PgPage.Add('Пароль PostgreSQL:', True);

  AppPage := CreateInputQueryPage(
    PgPage.ID,
    'Пароль приложения',
    'Технический пользователь базы данных',
    'Этот пароль отличается от пароля Андрея.'
  );
  AppPage.Add('Технический пароль:', True);

  MaxPage := CreateInputQueryPage(
    AppPage.ID,
    'Подключение MAX',
    'Настройка уведомлений',
    'Введите действующий токен бота MAX.'
  );
  MaxPage.Add('Токен MAX:', True);
end;

function ValidPassword(P: string): Boolean;
var
  I: Integer;
  C: string;
  Upper, Lower, Digit: Boolean;
begin
  Result := False;

  if Length(P) < 14 then Exit;

  Upper := False;
  Lower := False;
  Digit := False;

  for I := 1 to Length(P) do
  begin
    C := Copy(P, I, 1);

    if Pos(C, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') > 0 then
      Upper := True
    else if Pos(C, 'abcdefghijklmnopqrstuvwxyz') > 0 then
      Lower := True
    else if Pos(C, '0123456789') > 0 then
      Digit := True
    else
      Exit;
  end;

  Result := Upper and Lower and Digit;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if (CurPageID = PgPage.ID) and
     (not ValidPassword(PgPage.Values[0])) then
  begin
    MsgBox(
      'Пароль PostgreSQL: 14+ символов, латинские буквы разного регистра и цифры.',
      mbError, MB_OK
    );
    Result := False;
  end;

  if (CurPageID = AppPage.ID) and
     (not ValidPassword(AppPage.Values[0])) then
  begin
    MsgBox(
      'Технический пароль: 14+ символов, латинские буквы разного регистра и цифры.',
      mbError, MB_OK
    );
    Result := False;
  end;

  if (CurPageID = MaxPage.ID) and
     (Trim(MaxPage.Values[0]) = '') then
  begin
    MsgBox('Укажите токен MAX.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure ExecChecked(FileName, Args: string);
var
  Code: Integer;
begin
  if not Exec(
    FileName, Args, '',
    SW_HIDE, ewWaitUntilTerminated, Code
  ) then
    RaiseException(
      'Не удалось запустить: ' + ExtractFileName(FileName)
    );

  if Code <> 0 then
    RaiseException(
      'Ошибка установки: ' +
      ExtractFileName(FileName) +
      ' (' + IntToStr(Code) + ')'
    );
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Sql, Params, SettingsFile, SettingsText: string;
  I, Code: Integer;
  Ready: Boolean;
begin
  if CurStep <> ssPostInstall then Exit;

  PgBin := ExpandConstant(
    '{autopf}\PostgreSQL\17\bin'
  );

  if FileExists(PgBin + '\psql.exe') then
    RaiseException(
      'PostgreSQL 17 уже установлен. Автоматическая установка поверх него запрещена.'
    );

  Params :=
    '--mode unattended --unattendedmodeui minimal ' +
    '--superpassword "' + PgPage.Values[0] + '" ' +
    '--servicepassword "' + PgPage.Values[0] + '" ' +
    '--disable-components pgAdmin,stackbuilder ' +
    '--serverport 5432 --prefix "' +
    ExpandConstant('{autopf}\PostgreSQL\17') + '"';

  ExecChecked(
    ExpandConstant('{tmp}\postgresql17.exe'),
    Params
  );

  if not FileExists(PgBin + '\psql.exe') then
    RaiseException('PostgreSQL не установлен.');

  Ready := False;

  for I := 1 to 45 do
  begin
    if Exec(
      PgBin + '\pg_isready.exe',
      '-h 127.0.0.1 -p 5432',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      Code
    ) then
      if Code = 0 then
      begin
        Ready := True;
        Break;
      end;

    Sleep(1000);
  end;

  if not Ready then
    RaiseException('PostgreSQL не запущен.');

  SetEnvironmentVariable(
    'PGPASSWORD', PgPage.Values[0]
  );

  try
    Sql :=
      'CREATE ROLE cut_app LOGIN PASSWORD ' + #39 +
      AppPage.Values[0] + #39 + ';';

    ExecChecked(
      PgBin + '\psql.exe',
      '-w -X -h 127.0.0.1 -p 5432 ' +
      '-U postgres -d postgres ' +
      '-v ON_ERROR_STOP=1 -c "' + Sql + '"'
    );

    ExecChecked(
      PgBin + '\psql.exe',
      '-w -X -h 127.0.0.1 -p 5432 ' +
      '-U postgres -d postgres -v ON_ERROR_STOP=1 ' +
      '-c "CREATE DATABASE cut_improvement_auto_prod OWNER cut_app TEMPLATE template0;"'
    );

    ExecChecked(
      PgBin + '\pg_restore.exe',
      '--exit-on-error --role=cut_app ' +
      '--no-owner --no-acl ' +
      '-h 127.0.0.1 -p 5432 ' +
      '-U postgres -d cut_improvement_auto_prod "' +
      ExpandConstant('{tmp}\initial.dump') + '"'
    );

  finally
    SetEnvironmentVariable('PGPASSWORD', '');
  end;

  SettingsFile := ExpandConstant(
    '{commonappdata}\ImprovementAuto\settings.env'
  );

  SettingsText :=
    'DB_HOST=127.0.0.1' + #13#10 +
    'DB_PORT=5432' + #13#10 +
    'DB_USER=cut_app' + #13#10 +
    'DB_PASSWORD=' + AppPage.Values[0] + #13#10 +
    'DB_NAME=cut_improvement_auto_prod' + #13#10 +
    'MAX_BOT_TOKEN=' + Trim(MaxPage.Values[0]) + #13#10 +
    'CUT_MAX_ALL_CLIENTS_ENABLED=1' + #13#10;

  if not SaveStringToFile(
    SettingsFile, SettingsText, False
  ) then
    RaiseException('Не удалось сохранить настройки.');

  ExecChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + SettingsFile +
    '" /inheritance:r /grant:r ' +
    '*S-1-5-18:F *S-1-5-32-544:F *S-1-5-32-545:R'
  );

  ExecChecked(
    ExpandConstant(
      '{sys}\WindowsPowerShell\v1.0\powershell.exe'
    ),
    '-NoProfile -ExecutionPolicy Bypass -File "' +
    ExpandConstant(
      '{app}\_setup\register_backup.ps1'
    ) + '"'
  );
end;
'''

setup_dir = output / "output"
setup_dir.mkdir(exist_ok=True)

iss = (
    iss.replace("@@OUTPUT@@", str(setup_dir))
       .replace("@@DIST@@", str(exe.parent))
       .replace("@@PG@@", str(postgres[0]))
       .replace("@@DB@@", str(database))
       .replace("@@TASK@@", str(task_script))
)

iss_file = output / "ImprovementAuto.iss"
iss_file.write_text(iss, encoding="utf-8-sig")

print("=== BUILD SETUP.EXE ===", flush=True)

result = subprocess.run(
    [str(compiler), str(iss_file)],
    cwd=root
)

if result.returncode:
    raise SystemExit("STOP: Inno Setup compilation failed")

setup = setup_dir / "Improvement Auto Setup.exe"

if not setup.is_file():
    raise SystemExit("STOP: Setup.exe not found")

print("\nOK: IMPROVEMENT AUTO SETUP CREATED")
print("FILE:", setup)
print("SIZE MB:", round(setup.stat().st_size / 1048576, 1))
print("ORIGINAL PROJECT UNCHANGED")
print("DATABASES UNCHANGED")
print("DO NOT INSTALL ON DEVELOPMENT PC")
