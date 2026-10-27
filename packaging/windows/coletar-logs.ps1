# Junta num .zip na Área de Trabalho o que precisamos pra ver por que os mods não funcionam.
# Uso: dois cliques em "coletar-logs.bat" (fica ao lado do DeadlockMods.exe).
$ErrorActionPreference = "SilentlyContinue"
$out = Join-Path $env:TEMP "deadlock-mods-logs"
Remove-Item $out -Recurse -Force
New-Item -ItemType Directory $out | Out-Null
$resumo = Join-Path $out "resumo.txt"
function Log($t) { Write-Host $t; Add-Content -Encoding UTF8 $resumo $t }

Log "== sistema"
$os = Get-CimInstance Win32_OperatingSystem
Log "$($os.Caption) $($os.Version) ($($os.OSArchitecture))"
Get-CimInstance Win32_VideoController | ForEach-Object { Log "GPU: $($_.Name) driver $($_.DriverVersion)" }

# acha a pasta do Deadlock em todas as bibliotecas da Steam
$steam = (Get-ItemProperty "HKCU:\Software\Valve\Steam").SteamPath
if (-not $steam) { $steam = "${env:ProgramFiles(x86)}\Steam" }
Log "Steam: $steam"
$libs = @($steam)
$vdf = Join-Path $steam "steamapps\libraryfolders.vdf"
if (Test-Path $vdf) {
    $libs += Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"' -AllMatches |
        ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value -replace '\\\\', '\' }
}
$game = $null
foreach ($lib in $libs) {
    $g = Join-Path $lib "steamapps\common\Deadlock"
    if (Test-Path (Join-Path $g "game\citadel\gameinfo.gi")) { $game = $g; break }
}
Log "== jogo: $(if ($game) { $game } else { 'NÃO ENCONTRADO' })"

if ($game) {
    $c = Join-Path $game "game\citadel"
    Copy-Item "$c\gameinfo.gi", "$c\gameinfo.gi.bak", "$c\console.log" $out
    Get-ChildItem "$c\addons" -Force | Format-Table Name, Length, LastWriteTime -AutoSize | Out-File -Encoding UTF8 "$out\addons.txt"
    Get-ChildItem "$c\cfg" -Force | Format-Table Name, Length, LastWriteTime -AutoSize | Out-File -Encoding UTF8 "$out\cfg.txt"
    Get-ChildItem $c -Force | Format-Table Name, Length, LastWriteTime -AutoSize | Out-File -Encoding UTF8 "$out\citadel.txt"
}

# estado do app (lista de mods, pasta do jogo) e o log que o .exe grava
$d = Join-Path $env:LOCALAPPDATA "deadlock-mods"
Copy-Item "$d\state.json", "$d\app.log", "$d\app.prev.log" $out
Get-ChildItem "$d\mods" -Recurse -Force | Select-Object FullName, Length, LastWriteTime |
    Format-Table -AutoSize | Out-File -Encoding UTF8 -Width 300 "$out\mods.txt"

# app aberto agora? (o log só fica completo depois de fechar o app)
if (Get-Process DeadlockMods) { Log "AVISO: o Deadlock Mods ainda está aberto — feche e rode de novo pro app.log sair completo" }

$zip = Join-Path ([Environment]::GetFolderPath("Desktop")) "deadlock-mods-logs.zip"
Remove-Item $zip -Force
Compress-Archive -Path "$out\*" -DestinationPath $zip
Write-Host ""
Write-Host "Pronto: $zip — manda esse arquivo."
explorer.exe "/select,`"$zip`""
