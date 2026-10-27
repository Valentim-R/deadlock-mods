#!/usr/bin/env bash
# Gera os builds e publica a release vX.Y.Z no GitHub (o app de quem já tem instalado se atualiza por ela).
# Uso: packaging/release.sh "notas da versão"   (a versão vem de dlmods/__init__.py)
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ"
VERSION=$(grep -oP '^__version__\s*=\s*"\K[^"]+' dlmods/__init__.py)
NOTES="${1:-}"
if gh release view "v$VERSION" >/dev/null 2>&1; then
    echo "a release v$VERSION já existe: suba __version__ em dlmods/__init__.py" >&2
    exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
    echo "tem mudança sem commit: faça o commit antes de publicar" >&2
    exit 1
fi
packaging/build-appimage.sh
packaging/build-windows.sh
git push
gh release create "v$VERSION" --title "Deadlock Mods $VERSION" --notes "$NOTES" \
    dist/DeadlockMods-x86_64.AppImage "dist/DeadlockMods-Setup-$VERSION.exe" dist/DeadlockMods-windows-x64.zip
