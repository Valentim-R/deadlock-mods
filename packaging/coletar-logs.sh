#!/usr/bin/env bash
# Junta num .tar.gz o que precisamos pra ver por que os mods não funcionam.
# Uso:  bash coletar-logs.sh [caminho/do/DeadlockMods-x86_64.AppImage]
# Se passar o AppImage, ele abre o app: faça o que dá problema, feche o app, e o log fica salvo.
set -u
OUT="$HOME/deadlock-mods-logs"
rm -rf "$OUT"; mkdir -p "$OUT"
log() { echo "$*" | tee -a "$OUT/resumo.txt"; }

log "== sistema"
{ uname -a; cat /etc/os-release 2>/dev/null | head -3; ldd --version 2>&1 | head -1; echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"; } >> "$OUT/resumo.txt"

# acha a pasta do Deadlock em todas as bibliotecas da Steam (nativa e flatpak)
GAME=""
for root in "$HOME/.local/share/Steam" "$HOME/.steam/steam" "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"; do
    vdf="$root/steamapps/libraryfolders.vdf"
    libs="$root"
    [ -f "$vdf" ] && libs="$libs"$'\n'"$(grep -oP '"path"\s+"\K[^"]+' "$vdf")"
    while IFS= read -r lib; do
        if [ -f "$lib/steamapps/common/Deadlock/game/citadel/gameinfo.gi" ]; then GAME="$lib/steamapps/common/Deadlock"; break 2; fi
    done <<< "$libs"
done
log "== jogo: ${GAME:-NÃO ENCONTRADO}"

if [ -n "$GAME" ]; then
    C="$GAME/game/citadel"
    cp "$C/gameinfo.gi" "$OUT/gameinfo.gi" 2>/dev/null
    cp "$C/gameinfo.gi.bak" "$OUT/gameinfo.gi.bak" 2>/dev/null
    ls -la "$C/addons" > "$OUT/addons.txt" 2>&1
    ls -la "$C/cfg" > "$OUT/cfg.txt" 2>&1
    ls -la "$C" > "$OUT/citadel.txt" 2>&1
    cp "$C/console.log" "$OUT/console.log" 2>/dev/null
fi

# estado do app (lista de mods, pasta do jogo)
D="$HOME/.local/share/deadlock-mods"
cp "$D/state.json" "$OUT/state.json" 2>/dev/null
ls -laR "$D/mods" > "$OUT/mods.txt" 2>&1

# log do Proton, se ele tiver rodado o jogo com PROTON_LOG=1
cp "$HOME"/steam-1422450.log "$OUT/" 2>/dev/null

# opcional: roda o app e grava tudo que ele imprime
if [ $# -ge 1 ] && [ -f "$1" ]; then
    log "== abrindo o app: faça o que dá problema e depois feche a janela"
    chmod +x "$1"
    "$1" > "$OUT/app.log" 2>&1
fi

tar -czf "$HOME/deadlock-mods-logs.tar.gz" -C "$HOME" deadlock-mods-logs
echo
echo "Pronto: $HOME/deadlock-mods-logs.tar.gz — manda esse arquivo."
