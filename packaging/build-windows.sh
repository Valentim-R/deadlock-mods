#!/usr/bin/env bash
# Gera dist/DeadlockMods-Setup-<versão>.exe (instalador Inno Setup) e dist/DeadlockMods-windows-x64.zip
# (PyInstaller --onedir + 7-Zip embutido) usando o Wine.
# Uso: packaging/build-windows.sh
# Precisa no host: wine, 7z, curl e python3 com PySide6 (só pra desenhar o ícone).
set -euo pipefail
shopt -s nullglob

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${XDG_CACHE_HOME:-$HOME/.cache}/deadlock-mods-build"
PYVER=3.12.10                      # último 3.12 com instalador oficial
INNOVER=6.7.3
# winemenubuilder desligado: senão os atalhos do Inno Setup/do teste do instalador vão parar no menu do Linux
export WINEPREFIX="$WORK/wine" WINEARCH=win64 WINEDEBUG=-all WINEDLLOVERRIDES="winemenubuilder.exe=d"
mkdir -p "$WORK/win" "$PROJ/dist"
cd "$WORK/win"

t0=$SECONDS
etapa() { echo "== [$((SECONDS - t0))s] $*"; }
wpath() { winepath -w "$1" 2>/dev/null; }

# --- prefixo + Python oficial do Windows
if [ ! -f "$WINEPREFIX/system.reg" ]; then
    etapa "criando prefixo do Wine"
    WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d" wineboot -i     # sem perguntar do Mono/Gecko
    wineserver -w
fi
PY="$WINEPREFIX/drive_c/Python312/python.exe"
if [ ! -f "$PY" ]; then
    etapa "instalando Python $PYVER"
    [ -f "python-$PYVER-amd64.exe" ] || curl -sfLO "https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe"
    wine "python-$PYVER-amd64.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 \
         Include_doc=0 Include_tcltk=0 Include_launcher=0 Shortcuts=0 'TargetDir=C:\Python312'
    wineserver -w
fi
wpy() { wine "$PY" "$@"; }

etapa "pip: pyinstaller + dependências"
wpy -m pip -q --disable-pip-version-check install --no-warn-script-location -U pyinstaller \
    "PySide6-Essentials>=6.5" requests numpy "backports.zstd; python_version < '3.14'"

# --- 7-Zip: o 7za do pacote "extra" não abre .rar, então vai o 7z.exe + 7z.dll do instalador completo
if [ ! -f 7z/7z.dll ]; then
    etapa "baixando 7-Zip"
    url=$(curl -s https://www.7-zip.org/download.html | grep -oE 'a/7z[0-9]+-x64\.exe' | head -1)
    curl -sfLO "https://www.7-zip.org/$url"
    rm -rf 7z && 7z x -y -o7z "$(basename "$url")" 7z.exe 7z.dll License.txt >/dev/null
fi

# --- ícone: renderiza o SVG em vários tamanhos e monta o .ico (entradas PNG) na mão
etapa "gerando ícone"
QT_QPA_PLATFORM=offscreen python3 - "$PROJ/dlmods/icon.svg" icon.ico <<'EOF'
import struct, sys
from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
app = QGuiApplication([])
svg = QSvgRenderer(sys.argv[1])
pngs = []
for s in (16, 24, 32, 48, 64, 128, 256):
    img = QImage(s, s, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img); svg.render(p); p.end()
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QBuffer.WriteOnly); img.save(buf, "PNG")
    pngs.append((s, bytes(ba)))
out = struct.pack("<HHH", 0, 1, len(pngs))
off = 6 + 16 * len(pngs)
for s, d in pngs:
    out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(d), off)
    off += len(d)
open(sys.argv[2], "wb").write(out + b"".join(d for _, d in pngs))
EOF

# --- PyInstaller
etapa "PyInstaller"
rm -rf build dist
excl=()
for m in QtQml QtQuick QtQuickWidgets QtQuickControls2 QtPdf QtPdfWidgets QtMultimedia QtMultimediaWidgets \
         QtSql QtTest QtDesigner QtHelp QtUiTools QtPrintSupport QtOpenGLWidgets QtConcurrent QtXml \
         QtDBus QtStateMachine QtCharts QtGraphs QtDataVisualization QtLocation QtPositioning \
         Qt3DCore QtWebEngineCore QtWebEngineWidgets QtBluetooth QtSerialPort QtSpatialAudio QtTextToSpeech; do
    excl+=(--exclude-module "PySide6.$m")
done
for m in tkinter unittest pydoc_data lib2to3 test; do excl+=(--exclude-module "$m"); done
wpy -m PyInstaller --noconfirm --clean --log-level WARN --windowed --onedir --name DeadlockMods \
    --icon "$(wpath icon.ico)" \
    --paths "$(wpath "$PROJ")" \
    --add-data "$(wpath "$PROJ/dlmods/icon.svg");dlmods" \
    --add-binary "$(wpath 7z/7z.exe);." --add-binary "$(wpath 7z/7z.dll);." \
    --add-data "$(wpath 7z/License.txt);7-Zip" \
    --hidden-import dlmods.s2.engine --hidden-import dlmods.camfix \
    "${excl[@]}" \
    --specpath "$(wpath .)" --workpath "$(wpath build)" --distpath "$(wpath dist)" \
    "$(wpath "$PROJ/main.py")"
wineserver -w

# --- corta o que o app não usa (traduções, plugins de impressão/SQL/QML…)
etapa "enxugando"
I=dist/DeadlockMods/_internal
P=$I/PySide6
rm -rf "$P"/translations "$P"/plugins/{sqldrivers,designer,qmltooling,printsupport,multimedia,position,sensors,texttospeech,webview,virtualkeyboard}
rm -f "$P"/Qt6{Quick,Qml,Designer,Sql,Test,Pdf,ShaderTools,Help,UiTools,Labs,Lottie,Multimedia,Spatial,StateMachine,Charts,Graphs,DataVis,Location,Positioning,3D,Concurrent,OpenGLWidgets,PrintSupport,VirtualKeyboard}*.dll
rm -f "$P"/plugins/imageformats/q{icns,tga,tiff,wbmp,pdf}*.dll "$P"/plugins/platforms/q{direct2d,minimal}.dll \
      "$P"/plugins/tls/q{certonly,openssl}backend.dll     # no Windows o TLS do Qt usa o schannel
rm -rf "$P"/plugins/generic

# coletor de logs pro caso de dar problema (dois cliques no .bat)
cp "$PROJ"/packaging/windows/coletar-logs.{bat,ps1} dist/DeadlockMods/

# --- instalador (Inno Setup): um .exe só pra mandar
ISCC="$WINEPREFIX/drive_c/InnoSetup/ISCC.exe"
if [ ! -f "$ISCC" ]; then
    etapa "instalando Inno Setup $INNOVER"
    f="innosetup-$INNOVER.exe"
    [ -f "$f" ] || curl -sfLO "https://github.com/jrsoftware/issrc/releases/download/is-${INNOVER//./_}/$f"
    wine "$f" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER '/DIR=C:\InnoSetup'
    wineserver -w
fi
VERSION=$(grep -oP '^__version__\s*=\s*"\K[^"]+' "$PROJ/dlmods/__init__.py")
etapa "instalador $VERSION"
rm -f "$PROJ"/dist/DeadlockMods-Setup-*.exe
wine "$ISCC" /Q "/DAppVersion=$VERSION" "/DSrcDir=$(wpath dist/DeadlockMods)" "/DOutDir=$(wpath "$PROJ/dist")" \
     "/DIconFile=$(wpath icon.ico)" "$(wpath "$PROJ/packaging/windows/installer.iss")"
wineserver -w

etapa "compactando"
rm -f "$PROJ/dist/DeadlockMods-windows-x64.zip"
(cd dist && 7z a -tzip -mx=9 "$PROJ/dist/DeadlockMods-windows-x64.zip" DeadlockMods >/dev/null)
etapa "pronto"
ls -la "$PROJ"/dist/DeadlockMods-windows-x64.zip "$PROJ"/dist/DeadlockMods-Setup-*.exe
