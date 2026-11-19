$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
if (!(Test-Path -LiteralPath $root -PathType Container)) {
    throw "Project directory not found: $root"
}
Set-Location -LiteralPath $root

$base = Join-Path $root 'release\final_installer'
$dist = Join-Path $base 'dist\ImprovementAuto'
$appExe = Join-Path $dist 'ImprovementAuto.exe'
$inside = Join-Path $dist '_internal'
$pgFolder = Join-Path $root 'release\installer_assets\postgresql17'
$pgFiles = @(Get-ChildItem -LiteralPath $pgFolder -Filter '*.exe' -File)
$dbDump = Join-Path $root 'release\installer_assets\database\improvement_auto_initial.dump'

if (!(Test-Path -LiteralPath $appExe -PathType Leaf)) { throw 'Existing ImprovementAuto.exe not found.' }
foreach ($relative in @('backend\app\main.py', 'deploy\frontend_releases\active.json', 'retention\cut_backup_retention_prod.py')) {
    if (!(Test-Path -LiteralPath (Join-Path $inside $relative) -PathType Leaf)) {
        throw "Required application component missing: $relative. Setup NOT built."
    }
}
if (@(Get-ChildItem -LiteralPath $inside -Recurse -File -Filter '.env' -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'A developer .env is present inside the packaged EXE. Setup NOT built.'
}
if ($pgFiles.Count -ne 1) { throw 'Exactly one PostgreSQL 17 installer .exe is required.' }
if (!(Test-Path -LiteralPath $dbDump -PathType Leaf)) { throw 'Clean initial database dump not found.' }

# Verify the owner-only production dump from the approved clean database.
$expectedDumpHash = 'b5dd1440ae06cc0320bb5d94be2dd68e5fd1bcd5a6878ca05e9e6fd1cd00299a'
$actualDumpHash = (Get-FileHash -LiteralPath $dbDump -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualDumpHash -ne $expectedDumpHash) {
    throw 'Initial database archive does not match the approved clean production dump. Setup NOT built.'
}

$compilerCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
)
$oldBuilder = Join-Path $root 'scripts\build_improvement_setup.py'
if (Test-Path -LiteralPath $oldBuilder) {
    $builderText = Get-Content -LiteralPath $oldBuilder -Raw -Encoding UTF8
    $compilerCandidates += @([regex]::Matches($builderText, 'Path\(r"([^\"]*ISCC\.exe)"\)') | ForEach-Object { $_.Groups[1].Value })
}
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (!$compiler) { throw 'Inno Setup ISCC.exe not found. Setup NOT built.' }

$buildDir = Join-Path $base 'FINAL_BUILD_PGFIX'
$outDir = Join-Path $base 'FINAL_OUTPUT_PGFIX'
New-Item -ItemType Directory -Path $buildDir, $outDir -Force | Out-Null

$helperCode = @'
param(
    [Parameter(Mandatory=$true)][string]$AppDir,
    [Parameter(Mandatory=$true)][string]$PgInstaller,
    [Parameter(Mandatory=$true)][string]$DumpFile
)
$ErrorActionPreference = 'Stop'

$homeDir = Join-Path $env:ProgramData 'ImprovementAuto'
$configFile = Join-Path $homeDir 'settings.env'
$tokenFile = Join-Path $homeDir '_max_install_token.tmp'
$secretDir = Join-Path $homeDir '_setup_private'
$pgRoot = Join-Path $env:ProgramFiles 'PostgreSQL\17'
$pgBin = Join-Path $pgRoot 'bin'
$psql = Join-Path $pgBin 'psql.exe'
$restore = Join-Path $pgBin 'pg_restore.exe'
$pgReady = Join-Path $pgBin 'pg_isready.exe'
$appExe = Join-Path $AppDir 'ImprovementAuto.exe'
$finished = $false

function Assert-Code([string]$description) {
    if ($LASTEXITCODE -ne 0) {
        throw "$description failed (exit code $LASTEXITCODE)"
    }
}

function Random-Strong-Password {
    $bytes = New-Object byte[] 24
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return ('Aa9' + [BitConverter]::ToString($bytes).Replace('-', ''))
}

function Write-PrivateLines([string]$path, [string[]]$lines) {
    [System.IO.File]::WriteAllLines($path, $lines, (New-Object System.Text.UTF8Encoding($false)))
}

# No existing PostgreSQL, settings, task or occupied port may be overwritten.
foreach ($file in @($PgInstaller, $DumpFile, $appExe, $tokenFile)) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required installation file missing: $file" }
}
if ((Test-Path -LiteralPath $pgRoot) -or (Test-Path -LiteralPath $configFile)) {
    throw 'An existing PostgreSQL or Improvement Auto installation was found. No overwrite allowed.'
}
if (@(Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'A PostgreSQL Windows service is already installed. No overwrite allowed.'
}
foreach ($port in @(5432, 8000)) {
    if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
        throw "TCP port $port is already in use; no overwrite allowed."
    }
}
foreach ($name in @('Improvement Auto Server','Improvement Auto Daily Backup')) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        throw "Existing scheduled task: $name. No overwrite allowed."
    }
}

# Inno Setup already restricted the parent directory to SYSTEM/Administrators.
New-Item -ItemType Directory -Path $secretDir -Force | Out-Null
& icacls.exe $secretDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
Assert-Code 'Secret directory ACL'

$optionsFile = Join-Path $secretDir 'pg_options.tmp'
$roleSql = Join-Path $secretDir 'create_role.sql'
$logDir = Join-Path $homeDir 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$setupLog = Join-Path $logDir 'setup_status.log'

try {
    $token = [System.IO.File]::ReadAllText($tokenFile).Trim()
    if (!$token -or $token.Contains("`n") -or $token.Contains("`r")) {
        throw 'MAX token is missing or invalid.'
    }

    # Windows cryptographic RNG: different credentials on every installation.
    $pgAdminPassword = Random-Strong-Password
    $appPassword = Random-Strong-Password

    # WARNING: no unparenthesized '+' inside PowerShell array literals.
    # The comma operator binds strongly and can merge entries unexpectedly.
    # Generate exactly seven key=value lines; secrets never go into process args.
    $expectedOptions = @(
        'mode=unattended'
        'unattendedmodeui=minimal'
        "superpassword=$pgAdminPassword"
        "servicepassword=$pgAdminPassword"
        'enable-components=server,commandlinetools'
        'serverport=5432'
        "prefix=$pgRoot"
    )
    Write-PrivateLines $optionsFile $expectedOptions

    # Fail safely BEFORE installing PostgreSQL if PowerShell built invalid data.
    # Do not display option values: they include the generated DB password.
    $actualOptions = [System.IO.File]::ReadAllLines($optionsFile, [System.Text.Encoding]::UTF8)
    if ($actualOptions.Length -ne 7 -or $expectedOptions.Length -ne 7) {
        throw 'PostgreSQL options file validation failed: wrong number of lines.'
    }
    for ($index = 0; $index -lt 7; $index++) {
        if ($actualOptions[$index] -cne $expectedOptions[$index]) {
            throw "PostgreSQL options file validation failed at line $($index + 1)."
        }
    }
    [System.IO.File]::AppendAllText($setupLog, "SETUP: PostgreSQL option file validated (7 settings).`r`n")

    # A single quoted argument string preserves spaces in the option file path.
    # Password remains ONLY in the protected options file.
    $process = Start-Process -FilePath $PgInstaller -ArgumentList ('--optionfile "' + $optionsFile + '"') -Wait -PassThru
    if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $psql)) {
        throw "PostgreSQL 17 installation failed (exit code $($process.ExitCode))."
    }

    $dbReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        & $pgReady -h 127.0.0.1 -p 5432 | Out-Null
        if ($LASTEXITCODE -eq 0) { $dbReady = $true; break }
        Start-Sleep -Seconds 2
    }
    if (!$dbReady) { throw 'PostgreSQL was installed but did not start.' }

    $env:PGPASSWORD = $pgAdminPassword
    Write-PrivateLines $roleSql @(
        "CREATE ROLE cut_app LOGIN PASSWORD '$appPassword';"
    )

    & $psql -w -X -h 127.0.0.1 -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -f $roleSql
    Assert-Code 'Create application PostgreSQL role'

    & $psql -w -X -h 127.0.0.1 -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE cut_improvement_auto_prod OWNER cut_app TEMPLATE template0;'
    Assert-Code 'Create new clean production database'

    & $restore --exit-on-error --role=cut_app --no-owner --no-acl -h 127.0.0.1 -p 5432 -U postgres -d cut_improvement_auto_prod $DumpFile
    Assert-Code 'Restore clean database and owner account'

    $check = & $psql -w -X -t -A -h 127.0.0.1 -p 5432 -U postgres -d cut_improvement_auto_prod -c "SELECT to_regclass('public.appointments');"
    Assert-Code 'Check restored database schema'
    if (!($check | Out-String).Trim()) { throw 'Appointments table was not restored.' }

    # PostgreSQL superuser password is NOT saved in the permanent configuration.
    # Each line is a separate scalar string (not a '+' / comma expression).
    $configLines = @(
        'DB_HOST=127.0.0.1'
        'DB_PORT=5432'
        'DB_USER=cut_app'
        "DB_PASSWORD=$appPassword"
        'DB_NAME=cut_improvement_auto_prod'
        "MAX_BOT_TOKEN=$token"
        'CUT_MAX_ALL_CLIENTS_ENABLED=1'
    )
    Write-PrivateLines $configFile $configLines
    $savedConfig = [System.IO.File]::ReadAllLines($configFile, [System.Text.Encoding]::UTF8)
    if ($savedConfig.Length -ne $configLines.Length) {
        throw 'Application settings file validation failed.'
    }
    for ($index = 0; $index -lt $configLines.Length; $index++) {
        if ($savedConfig[$index] -cne $configLines[$index]) {
            throw 'Application settings file validation failed.'
        }
    }
    & icacls.exe $configFile /inheritance:r /grant:r '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    Assert-Code 'Private application settings ACL'

    foreach ($folder in @('logs','backups','data')) {
        New-Item -ItemType Directory -Path (Join-Path $homeDir $folder) -Force | Out-Null
    }

    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $serverAction = New-ScheduledTaskAction -Execute $appExe -Argument '--background' -WorkingDirectory $AppDir
    $serverTrigger = New-ScheduledTaskTrigger -AtStartup
    $serverSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName 'Improvement Auto Server' -Action $serverAction -Trigger $serverTrigger -Settings $serverSettings -Principal $principal -ErrorAction Stop | Out-Null

    $backupAction = New-ScheduledTaskAction -Execute $appExe -Argument '--backup' -WorkingDirectory $AppDir
    $backupTrigger = New-ScheduledTaskTrigger -Daily -At '12:00'
    $backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName 'Improvement Auto Daily Backup' -Action $backupAction -Trigger $backupTrigger -Settings $backupSettings -Principal $principal -ErrorAction Stop | Out-Null

    Start-ScheduledTask -TaskName 'Improvement Auto Server' -ErrorAction Stop
    $webReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $webReady = $true; break }
        } catch { }
    }
    if (!$webReady) {
        throw 'Program did not open at http://127.0.0.1:8000/. Check ProgramData\ImprovementAuto\logs\fatal.log and server.log.'
    }

    # Acceptance check: make the first complete archive using the packaged EXE.
    $backupProcess = Start-Process -FilePath $appExe -ArgumentList '--backup' -Wait -PassThru
    if ($backupProcess.ExitCode -ne 0) {
        throw 'Initial backup test failed. Check ProgramData\ImprovementAuto\logs\backup.log.'
    }
    if (@(Get-ChildItem -Path (Join-Path $homeDir 'backups') -Directory -Filter 'backup_*' -ErrorAction SilentlyContinue).Count -lt 1) {
        throw 'Backup archive was not created.'
    }

    [System.IO.File]::AppendAllText($setupLog, "SETUP OK: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n")
    $finished = $true
} catch {
    [System.IO.File]::AppendAllText($setupLog, "SETUP ERROR: $($_.Exception.Message)`r`n")
    throw
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    foreach ($file in @($optionsFile, $roleSql, $tokenFile)) {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $secretDir) {
        Remove-Item -LiteralPath $secretDir -Force -Recurse -ErrorAction SilentlyContinue
    }
    if (!$finished -and (Test-Path -LiteralPath $configFile)) {
        # Failed installation must not leave reusable application credentials behind.
        Remove-Item -LiteralPath $configFile -Force -ErrorAction SilentlyContinue
    }
}
'@

$issCode = @'
#define AppName "Improvement Auto"
#define AppVersion "0.2.0"

[Setup]
AppId={{93D980D7-10AA-4C59-B503-FE82C0B846ED}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName=Improvement Auto 0.2.0
DefaultDirName={autopf}\Improvement Auto
DefaultGroupName=Improvement Auto
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=@@OUTPUT@@
OutputBaseFilename=Improvement Auto Setup PGFIX
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ImprovementAuto.exe

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "@@DIST@@\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "@@POSTGRES@@"; DestDir: "{tmp}"; DestName: "postgresql17.exe"; Flags: deleteafterinstall
Source: "@@DUMP@@"; DestDir: "{tmp}"; DestName: "initial.dump"; Flags: deleteafterinstall
Source: "@@HELPER@@"; DestDir: "{app}\_setup"; DestName: "install_machine.ps1"; Flags: ignoreversion
Source: "@@LINK@@"; DestDir: "{app}"; DestName: "ImprovementAuto.url"; Flags: ignoreversion

[Dirs]
Name: "{app}\_internal\data"

[Icons]
Name: "{autoprograms}\Improvement Auto"; Filename: "{app}\ImprovementAuto.url"; IconFilename: "{app}\ImprovementAuto.exe"
Name: "{autodesktop}\Improvement Auto"; Filename: "{app}\ImprovementAuto.url"; IconFilename: "{app}\ImprovementAuto.exe"

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Improvement Auto Server"" /F"; Flags: runhidden
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Improvement Auto Daily Backup"" /F"; Flags: runhidden

[Code]
var
  MaxPage: TInputQueryWizardPage;
  TokenFile: String;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if DirExists(ExpandConstant('{commonappdata}\ImprovementAuto')) or
     DirExists(ExpandConstant('{autopf}\PostgreSQL\17')) then
  begin
    MsgBox('Обнаружены компоненты предыдущей установки или PostgreSQL 17.' + #13#10 +
      'Для защиты данных автоматическая установка поверх них запрещена.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure InitializeWizard;
begin
  MaxPage := CreateInputQueryPage(wpSelectDir,
    'Подключение MAX', 'Готовим Improvement Auto',
    'Введите токен уже существующего бота MAX. Технические пароли PostgreSQL будут созданы автоматически.');
  MaxPage.Add('Токен MAX:', True);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = MaxPage.ID then
  begin
    if (Trim(MaxPage.Values[0]) = '') or
       (Pos(#13, MaxPage.Values[0]) > 0) or
       (Pos(#10, MaxPage.Values[0]) > 0) then
    begin
      MsgBox('Введите действующий токен MAX (без переносов строк).', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure ExecuteChecked(ExeFile, Params: String);
var
  ExitCode: Integer;
begin
  if not Exec(ExeFile, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    RaiseException('Не удалось запустить настройку компонентов.');
  if ExitCode <> 0 then
    RaiseException('Автоматическая настройка не завершена (' + IntToStr(ExitCode) + ').' + #13#10 +
      'Журнал: C:\ProgramData\ImprovementAuto\logs\setup_status.log' + #13#10 +
      'Не вводите реальные данные клиентов до устранения ошибки.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir, CmdArgs: String;
begin
  if CurStep <> ssPostInstall then Exit;

  Dir := ExpandConstant('{commonappdata}\ImprovementAuto');
  if not ForceDirectories(Dir) then
    RaiseException('Не удалось создать защищённый каталог приложения.');

  ExecuteChecked(ExpandConstant('{sys}\icacls.exe'),
    '"' + Dir + '" /inheritance:r /grant:r ' +
    '"*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"');

  TokenFile := Dir + '\_max_install_token.tmp';
  if not SaveStringToFile(TokenFile, Trim(MaxPage.Values[0]), False) then
    RaiseException('Не удалось сохранить временный токен MAX.');

  CmdArgs := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{app}\_setup\install_machine.ps1') + '"' +
    ' -AppDir "' + ExpandConstant('{app}') + '"' +
    ' -PgInstaller "' + ExpandConstant('{tmp}\postgresql17.exe') + '"' +
    ' -DumpFile "' + ExpandConstant('{tmp}\initial.dump') + '"';

  try
    ExecuteChecked(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), CmdArgs);
  finally
    DeleteFile(TokenFile);
  end;
end;

procedure DeinitializeSetup;
begin
  if TokenFile <> '' then DeleteFile(TokenFile);
end;
'@

$helperPath = Join-Path $buildDir 'install_machine.ps1'
$linkPath = Join-Path $buildDir 'ImprovementAuto.url'
$issPath = Join-Path $buildDir 'ImprovementAuto_FINAL.iss'

[IO.File]::WriteAllText($helperPath, $helperCode, [Text.UTF8Encoding]::new($true))
[IO.File]::WriteAllText($linkPath, "[InternetShortcut]`r`nURL=http://127.0.0.1:8000/`r`n", [Text.Encoding]::ASCII)
$issCode = $issCode.Replace('@@DIST@@', $dist).Replace('@@POSTGRES@@', $pgFiles[0].FullName).Replace('@@DUMP@@', $dbDump).Replace('@@HELPER@@', $helperPath).Replace('@@LINK@@', $linkPath).Replace('@@OUTPUT@@', $outDir)
if ($issCode.Contains('@@')) { throw 'Unresolved installer placeholders. No compilation performed.' }
[IO.File]::WriteAllText($issPath, $issCode, [Text.UTF8Encoding]::new($true))

# Optional: PowerShell parser checks bootstrap syntax without executing installation.
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($helperPath, [ref]$parseTokens, [ref]$parseErrors) | Out-Null
if (@($parseErrors).Count -gt 0) {
    $parseErrors | ForEach-Object { Write-Host $_.Message -ForegroundColor Red }
    throw 'Bootstrap PowerShell syntax check failed. Setup NOT compiled.'
}

Write-Host "`n=== FINAL IMPROVEMENT AUTO SETUP ===" -ForegroundColor Cyan
Write-Host 'Using existing PyInstaller build; PostgreSQL and clean database unchanged.'
& $compiler $issPath
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }

$setup = Join-Path $outDir 'Improvement Auto Setup PGFIX.exe'
if (!(Test-Path -LiteralPath $setup -PathType Leaf)) { throw 'Final setup file not found.' }
Write-Host "`nOK: FINAL SETUP COMPILED" -ForegroundColor Green
Write-Host "FILE: $setup"
Write-Host "SIZE: $([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB"
Write-Host "SHA256: $((Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash)"
Write-Host 'NO NEW PYINSTALLER BUILD; NO LOCAL DATABASE CHANGES.'
