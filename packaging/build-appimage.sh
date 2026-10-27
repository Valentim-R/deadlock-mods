#!/usr/bin/env bash
# Gera dist/DeadlockMods-x86_64.AppImage (Python 3.12 + PySide6 + 7-Zip estático embutidos).
# Uso: packaging/build-appimage.sh
set -euo pipefail
shopt -s nullglob

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${XDG_CACHE_HOME:-$HOME/.cache}/deadlock-mods-build"
mkdir -p "$WORK" "$PROJ/dist"
cd "$WORK"

if [ ! -x venv/bin/python-appimage ]; then
    python3 -m venv venv
    venv/bin/pip -q install python-appimage
fi
if [ ! -x 7z/7zz ]; then
    url=$(curl -s https://www.7-zip.org/download.html | grep -oE 'a/7z[0-9]+-linux-x64\.tar\.xz' | head -1)
    curl -sfLO "https://www.7-zip.org/$url"
    mkdir -p 7z && tar -xf "$(basename "$url")" -C 7z
fi

rm -rf AppDir "Deadlock Mods-x86_64" recipe
# receita do python-appimage: o requirements aponta pro projeto onde ele estiver
cp -r "$PROJ/packaging/appimage" recipe
echo "$PROJ" > recipe/requirements.txt
venv/bin/python-appimage build app --no-packaging -n DeadlockMods -p 3.12 recipe
mv "Deadlock Mods-x86_64" AppDir
cp 7z/7zz AppDir/usr/bin/7zz

# Corta o que o app não usa (QML, Designer, ferramentas, pip…): ~314 MB -> ~156 MB antes de comprimir.
SP=AppDir/opt/python3.12/lib/python3.12/site-packages
P=$SP/PySide6
rm -rf "$P"/{designer,assistant,linguist,lupdate,lrelease,include,typesystems,scripts,glue,doc,examples} "$P"/qml* "$P"/*.pyi \
       "$P"/Qt/{qml,translations,metatypes} "$P"/Qt/plugins/{sqldrivers,designer,qmltooling,printsupport}
rm -f "$P"/Qt/lib/libQt6{Quick,Qml,Designer,Sql,Test,Pdf,ShaderTools,Help,UiTools,Labs,Lottie,Multimedia,Spatial,StateMachine,Charts,Graphs,DataVis,Location,Positioning,3D,Concurrent,OpenGLWidgets,PrintSupport}*
for so in "$P"/*.abi3.so; do
    case "$(basename "$so")" in
        QtCore.*|QtGui.*|QtWidgets.*|QtNetwork.*|QtSvg.*|QtOpenGL.*) ;;   # QtOpenGL: retratos 3D das skins
        *) rm -f "$so" ;;
    esac
done
rm -rf "$SP"/{pip,build,packaging,pyproject_hooks}*

tool=$(venv/bin/python-appimage which appimagetool)
ARCH=x86_64 "$tool" --comp zstd AppDir "$PROJ/dist/DeadlockMods-x86_64.AppImage"
ls -la "$PROJ/dist/DeadlockMods-x86_64.AppImage"
