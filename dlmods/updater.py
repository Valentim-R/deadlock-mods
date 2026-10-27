"""Atualização automática pelas Releases do GitHub (repositório público, sem login).

Cada release tem a tag vX.Y.Z e os arquivos que o build gera:
  DeadlockMods-x86_64.AppImage     Linux: baixa e troca o próprio AppImage no lugar
  DeadlockMods-Setup-X.Y.Z.exe     Windows instalado: roda o instalador em silêncio, que reabre o app
  DeadlockMods-windows-x64.zip     Windows portátil: só avisa e abre a página da release
Rodando do código-fonte (python main.py) não verifica nada: quem usa assim atualiza pelo git.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, net

REPO = "Valentim-R/deadlock-mods"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
APPIMAGE_ASSET = "DeadlockMods-x86_64.AppImage"
SETUP_ASSET = re.compile(r"^DeadlockMods-Setup-[\d.]+\.exe$")


def mode():
    """'appimage', 'windows' (instalado pelo Setup), 'zip' (Windows portátil) ou None (código-fonte)."""
    if os.environ.get("DLMODS_NO_UPDATE"):
        return None
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        exe = Path(sys.executable).parent
        return "windows" if any(exe.glob("unins*.exe")) else "zip"
    return None


def parse(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def check():
    """Release mais nova que esta versão -> {version, notes, url, asset: {name, url, sha256}}, senão None."""
    m = mode()
    if not m:
        return None
    rel = net.get_json(API, timeout=15)
    version = rel.get("tag_name", "").lstrip("v")
    if not version or parse(version) <= parse(__version__):
        return None
    asset = None
    for a in rel.get("assets", []):
        name = a.get("name", "")
        if (m == "appimage" and name == APPIMAGE_ASSET) or (m == "windows" and SETUP_ASSET.match(name)):
            digest = a.get("digest") or ""
            asset = {"name": name, "url": a["browser_download_url"],
                     "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None}
    return {"version": version, "notes": (rel.get("body") or "").strip(), "url": rel.get("html_url"),
            "mode": m, "asset": asset}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(info, progress=None):
    """Baixa o arquivo da atualização (verificando o SHA-256 que o GitHub publica). -> caminho."""
    a = info["asset"]
    if info["mode"] == "appimage":   # mesma pasta do AppImage: a troca vira um rename atômico
        dest = Path(os.environ["APPIMAGE"]).with_name(f".{APPIMAGE_ASSET}.novo")
    else:
        dest = Path(tempfile.gettempdir()) / a["name"]
    try:
        net.download(a["url"], dest, progress=progress)
        if a["sha256"] and _sha256(dest) != a["sha256"].lower():
            raise RuntimeError("a atualização baixada veio corrompida (SHA-256 não confere)")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


def install_and_restart(info, path):
    """Troca o app pela versão baixada e abre a nova; quem chama fecha o app logo depois."""
    if info["mode"] == "appimage":
        target = Path(os.environ["APPIMAGE"])
        os.chmod(path, 0o755)
        os.replace(path, target)
        subprocess.Popen([str(target)], start_new_session=True)
    else:
        # o [Run] do installer.iss reabre o app quando a instalação é silenciosa
        subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
