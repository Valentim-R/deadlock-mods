"""Estado local dos mods e sincronização com a pasta addons do jogo.

Cada mod tem seus .vpk guardados em DATA/mods/<id>/. A ordem da lista é a
prioridade: o primeiro mod ativo vira pak01_dir.vpk, o próximo pak02 e assim
por diante. Os arquivos na pasta addons são hardlinks (não gastam espaço).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WINDOWS = sys.platform == "win32"
# No Windows tudo fica em %LOCALAPPDATA% (os .vpk são grandes demais pro perfil móvel).
_WIN_BASE = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "deadlock-mods"
DATA = _WIN_BASE if WINDOWS else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "deadlock-mods"
STATE_FILE = DATA / "state.json"
MODS_DIR = DATA / "mods"
DOWNLOADS = DATA / "downloads"
CACHE = _WIN_BASE / "cache" if WINDOWS else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "deadlock-mods"
# Sem isso cada subprocess abre uma janela de console no Windows.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}

STEAM_APPID = 1422450
PAK_RE = re.compile(r"^pak(\d\d)_dir\.vpk$")
SPLIT_VPK = re.compile(r"_\d{3}\.vpk$", re.I)
MARK_START = "// deadlock-mods - Start"
MARK_END = "// deadlock-mods - End"

# Deadlock Mod Manager antigo: flatpak ou instalado direto (AppImage/deb).
DMM_DATA_DIRS = [Path.home() / ".var/app/dev.stormix.deadlock-mod-manager/data/dev.stormix.deadlock-mod-manager",
                 Path.home() / ".local/share/dev.stormix.deadlock-mod-manager",
                 Path(os.environ.get("APPDATA", Path.home())) / "dev.stormix.deadlock-mod-manager"]


def sevenzip():
    """7-Zip: o embutido no AppImage (DLMODS_7Z) / no .exe (ao lado do executável) ou o do sistema."""
    cands = [os.environ.get("DLMODS_7Z"), "7zz", "7z"]
    if WINDOWS:
        here = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        cands = [os.environ.get("DLMODS_7Z"), str(here / "7z.exe"), "7z",
                 os.path.expandvars(r"%ProgramFiles%\7-Zip\7z.exe")]
    for cand in cands:
        if cand and shutil.which(cand):
            return cand
    raise RuntimeError("7-Zip não encontrado (instale o 7-Zip)" if WINDOWS else
                       "7-Zip não encontrado (instale o pacote 7zip ou p7zip)")


def _windows_steam_root():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        return Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"


def _steam_libraries():
    roots = [_windows_steam_root()] if WINDOWS else [
        Path.home() / ".local/share/Steam", Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    libs = []
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        libs.append(root)
        if vdf.exists():
            # No Windows o vdf escapa as barras: "D:\\SteamLibrary".
            libs += [Path(p.replace("\\\\", "\\"))
                     for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore"))]
    return libs


def find_game(installed_only=False):
    """Pasta do Deadlock. Primeiro a que a Steam registra como instalada (appmanifest): uma cópia velha
    esquecida em outra biblioteca não pode ganhar, senão os mods vão pra pasta errada."""
    libs = _steam_libraries()
    for lib in libs:
        acf = lib / f"steamapps/appmanifest_{STEAM_APPID}.acf"
        if acf.exists():
            m = re.search(r'"installdir"\s+"([^"]+)"', acf.read_text(encoding="utf-8", errors="ignore"))
            g = lib / "steamapps/common" / (m.group(1) if m else "Deadlock")
            if (g / "game/citadel/gameinfo.gi").exists():
                return g
    if installed_only:
        return None
    for lib in libs:
        g = lib / "steamapps/common/Deadlock"
        if (g / "game/citadel/gameinfo.gi").exists():
            return g
    return None


def game_running():
    if WINDOWS:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq deadlock.exe", "/NH"],
                           capture_output=True, text=True, errors="ignore", **NO_WINDOW)
        return "deadlock.exe" in r.stdout.lower()
    # Só o nome do processo (sem -f), senão qualquer comando com esse texto nos argumentos conta.
    return subprocess.run(["pgrep", "-ix", r"deadlock\.exe|project8.*"], capture_output=True).returncode == 0


def _safe(name):
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "arquivo"


class Store:
    def __init__(self):
        for d in (DATA, MODS_DIR, DOWNLOADS, CACHE):
            d.mkdir(parents=True, exist_ok=True)
        self.state = {"game_path": None, "mods": [], "managed": {}}
        if STATE_FILE.exists():
            self.state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        installed = find_game(installed_only=True)   # o que a Steam diz que está instalado sempre ganha
        if installed:
            self.state["game_path"] = str(installed)
        elif not self.state["game_path"] or not Path(self.state["game_path"]).exists():
            g = find_game()   # (ou a pasta escolhida à mão, se ainda existir)
            self.state["game_path"] = str(g) if g else None
        self.save()

    # ---- básico -------------------------------------------------------------
    def save(self):
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)

    @property
    def mods(self):
        return self.state["mods"]

    @property
    def game(self):
        return Path(self.state["game_path"]) if self.state["game_path"] else None

    @property
    def addons(self):
        return self.game / "game/citadel/addons" if self.game else None

    def get(self, mod_id):
        return next((m for m in self.mods if m["id"] == mod_id), None)

    def mod_dir(self, mod_id):
        return MODS_DIR / _safe(mod_id)

    # ---- gameinfo.gi --------------------------------------------------------
    def ensure_gameinfo(self):
        """Garante 'Game citadel/addons' nos SearchPaths (o Steam desfaz isso quando o jogo atualiza)."""
        gi = self.game / "game/citadel/gameinfo.gi"
        with open(gi, encoding="utf-8", errors="surrogateescape", newline="") as f:
            orig = f.read()
        nl = "\r\n" if "\r\n" in orig else "\n"
        # Tira um bloco nosso antigo: versões anteriores punham o addons como 1º "Game" sem Mod/Write,
        # e aí a Source 2 usa o addons como MOD (cfg/user_keys_default some → FATAL ERROR ao abrir).
        text = re.sub(rf"[ \t]*{re.escape(MARK_START)}.*?{re.escape(MARK_END)}[ \t]*\r?\n?", "", orig, flags=re.S)
        m = re.search(r"SearchPaths\s*\{(.*?)\}", text, re.S)
        if not m:
            raise RuntimeError("não achei SearchPaths no gameinfo.gi")
        block = m.group(1)
        # Se já tem o addons (ex.: o do DMM, com Mod/Write explícitos), não mexe.
        if not re.search(r"^\s*Game\s+citadel/addons\s*$", block, re.M):
            g = re.search(r"^([ \t]*)Game\s+citadel\s*$", block, re.M)
            if not g:
                raise RuntimeError("não achei 'Game citadel' no gameinfo.gi")
            ind = g.group(1)
            lines = [MARK_START, "Game\t\t\t\tcitadel/addons"]
            if not re.search(r"^\s*Mod\s", block, re.M):
                # Sem chave Mod/Write o 1º Game vira MOD e pasta de escrita — tem que continuar sendo citadel.
                lines += ["Mod\t\t\t\tcitadel", "Write\t\t\t\tcitadel"]
            lines.append(MARK_END)
            pos = m.start(1) + g.start()
            text = text[:pos] + "".join(ind + l + nl for l in lines) + text[pos:]
        if text == orig:
            return False
        bak = gi.with_suffix(".gi.bak")
        if not bak.exists():
            shutil.copy2(gi, bak)
        with open(gi, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
        return True

    # ---- instalação ---------------------------------------------------------
    def extract_vpks(self, archive, mod_id, prefix=""):
        """Extrai os .vpk de um zip/rar/7z (ou copia um .vpk solto) pra pasta do mod."""
        dest = self.mod_dir(mod_id)
        dest.mkdir(parents=True, exist_ok=True)
        archive = Path(archive)
        found = []
        if archive.suffix.lower() == ".vpk":
            target = dest / _safe(prefix + archive.name)
            shutil.copy2(archive, target)
            return [target.name]
        with tempfile.TemporaryDirectory(dir=DATA) as tmp:
            r = subprocess.run([sevenzip(), "x", "-y", f"-o{tmp}", str(archive)], capture_output=True, text=True,
                               errors="replace", **NO_WINDOW)
            if r.returncode != 0:
                raise RuntimeError(f"não consegui extrair {archive.name}: {r.stderr.strip() or r.stdout[-300:]}")
            for p in sorted(Path(tmp).rglob("*.vpk")):
                if SPLIT_VPK.search(p.name):
                    continue  # pedaços de VPK dividido não são suportados como addon
                rel = p.relative_to(tmp)
                name = _safe(prefix + "__".join(rel.parts))
                shutil.move(str(p), dest / name)
                found.append(name)
        if not found:
            raise RuntimeError(f"{archive.name} não tem nenhum arquivo .vpk")
        return found

    def install(self, info, files_with_paths):
        """info = details() do GameBanana; files_with_paths = [(file_dict, caminho_baixado)]."""
        old = self.get(info["id"])
        old_enabled = {v["name"]: v["enabled"] for v in (old or {}).get("vpks", [])}
        if old:
            shutil.rmtree(self.mod_dir(info["id"]), ignore_errors=True)
        vpks = []
        for f, path in files_with_paths:
            prefix = f"{Path(f['name']).stem}__" if len(files_with_paths) > 1 else ""
            for name in self.extract_vpks(path, info["id"], prefix):
                vpks.append({"name": name, "archive": f["name"], "enabled": old_enabled.get(name)})
        # Sem escolha anterior (ou se a anterior deixaria tudo desligado): liga tudo se for
        # um só, senão só o primeiro de cada arquivo baixado.
        if not any(v["enabled"] for v in vpks):
            for v in vpks:
                v["enabled"] = None
        seen = set()
        for v in vpks:
            if v["enabled"] is None:
                v["enabled"] = len(vpks) == 1 or v["archive"] not in seen
            seen.add(v["archive"])
        entry = {
            "id": info["id"], "name": info["name"], "author": info.get("author", ""),
            "category": info.get("category", ""), "hero": info.get("hero", ""),
            "thumb": info.get("thumb"), "url": info.get("url"),
            "remote": True, "modified": info.get("modified", 0),
            "files": [{k: f[k] for k in ("file_id", "name", "md5", "size", "date")} for f, _ in files_with_paths],
            "vpks": vpks, "enabled": old["enabled"] if old else True,
            "installed_at": int(time.time()), "update": False,
            "camfix_on": info.get("camfix", True),   # FOV FIX escolhido na instalação
        }
        if old:
            self.mods[self.mods.index(old)] = entry
        else:
            self.mods.insert(0, entry)
        self.save()
        return entry

    def install_local(self, path, camfix=True):
        path = Path(path)
        mod_id = f"local-{int(time.time())}"
        names = self.extract_vpks(path, mod_id)
        entry = {"id": mod_id, "name": path.stem, "author": "", "category": "Local", "hero": "",
                 "thumb": None, "url": None, "remote": False, "modified": 0, "files": [],
                 "vpks": [{"name": n, "archive": path.name, "enabled": i == 0 or len(names) == 1}
                          for i, n in enumerate(names)],
                 "enabled": True, "installed_at": int(time.time()), "update": False, "camfix_on": camfix}
        self.mods.insert(0, entry)
        self.save()
        return entry

    def remove(self, mod_id):
        m = self.get(mod_id)
        if m:
            self.mods.remove(m)
            shutil.rmtree(self.mod_dir(mod_id), ignore_errors=True)
            self.save()

    def move(self, ids_in_order):
        by_id = {m["id"]: m for m in self.mods}
        self.state["mods"] = [by_id[i] for i in ids_in_order if i in by_id]
        self.save()

    # ---- sincronização com a pasta addons -----------------------------------
    def desired(self):
        """{'pak01_dir.vpk': Path(origem), ...} na ordem de prioridade."""
        out, n = {}, 1
        if getattr(self, "icons_vpk", None):   # ícones gerados pelo app: na frente de tudo (ver icons)
            out["pak01_dir.vpk"] = self.icons_vpk
            n = 2
        for m in self.mods:
            if not m["enabled"]:
                continue
            for v in m["vpks"]:
                if v["enabled"]:
                    out[f"pak{n:02d}_dir.vpk"] = self.mod_dir(m["id"]) / v["name"]
                    n += 1
        if n > 100:
            raise RuntimeError("o Deadlock só carrega até pak99; desative alguns arquivos")
        return out

    def adopt_unknown(self):
        """pakNN_dir.vpk na addons que não fomos nós que pusemos vira um mod local (nada é apagado)."""
        managed = self.state["managed"]
        adopted = []
        for p in sorted(self.addons.glob("pak*_dir.vpk")):
            if not PAK_RE.match(p.name):
                continue
            st = p.stat()
            known = managed.get(p.name)
            if known and known.get("ino") == st.st_ino:
                continue
            if any(os.path.samefile(p, self.mod_dir(m["id"]) / v["name"])
                   for m in self.mods for v in m["vpks"] if (self.mod_dir(m["id"]) / v["name"]).exists()):
                continue
            mod_id = f"local-{p.stem}-{int(st.st_mtime)}"
            d = self.mod_dir(mod_id)
            d.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), d / p.name)
            self.mods.append({"id": mod_id, "name": f"Mod manual ({p.name})", "author": "", "category": "Local",
                              "hero": "", "thumb": None, "url": None, "remote": False, "modified": 0, "files": [],
                              "vpks": [{"name": p.name, "archive": p.name, "enabled": True}], "enabled": True,
                              "installed_at": int(time.time()), "update": False})
            adopted.append(p.name)
        if adopted:
            self.save()
        return adopted

    # ---- mod padrão do app: câmera das skins antigas ------------------------
    def fix_cameras(self):
        """Corrige a câmera das skins feitas antes da atualização de 16/10/2026 (ver camfix).
        Cada .vpk é verificado uma vez: guarda tamanho+data pra não reler a cada sincronização."""
        from . import camfix
        fixed = []
        for m in self.mods:
            if not m.get("camfix_on", True):   # desligado na instalação ("Aplicar FOV FIX")
                continue
            for v in m["vpks"]:
                p = self.mod_dir(m["id"]) / v["name"]
                try:
                    st = p.stat()
                except OSError:
                    continue
                sig = [st.st_size, st.st_mtime_ns]
                if v.get("camfix_sig") == sig:
                    continue
                try:
                    status, models = camfix.fix_vpk(str(p))
                except Exception as e:   # formato que não dá pra corrigir com segurança: fica como está
                    status, models = f"não corrigido: {e}", []
                st = p.stat()
                v["camfix"], v["camfix_sig"] = status, [st.st_size, st.st_mtime_ns]
                if status == "fixed":
                    fixed.append(m["name"])
                self.save()
        return fixed

    # ---- mod padrão do app: ícones do herói com a skin ------------------------
    def update_icons(self):
        """Gera os ícones (seleção/barra de cima) das skins ligadas, por cima dos que elas trazem (ver icons).
        icons_missing = [(herói, mod)] que ainda não têm retrato 3D: a interface renderiza e sincroniza de novo."""
        self.icons_vpk, self.icons_missing = None, []
        if not self.state.get("auto_icons", True):
            return
        try:
            from . import icons
            self.icons_vpk, self.icons_missing = icons.build(self, DATA / "generated")
        except Exception:
            import traceback
            traceback.print_exc()   # sem ícones gerados, o resto da sincronização segue

    def sync(self):
        if not self.game:
            raise RuntimeError("não achei a pasta do Deadlock")
        if game_running():
            raise RuntimeError("feche o Deadlock antes de mudar os mods")
        self.addons.mkdir(parents=True, exist_ok=True)
        self.ensure_gameinfo()
        self.last_camfix = self.fix_cameras()
        self.adopt_unknown()
        self.update_icons()
        want = self.desired()
        for p in self.addons.glob("pak*_dir.vpk"):
            if PAK_RE.match(p.name) and (p.name not in want or not os.path.samefile(p, want[p.name])):
                p.unlink()
        managed = {}
        for name, src in want.items():
            dst = self.addons / name
            if not dst.exists():
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
            managed[name] = {"ino": dst.stat().st_ino, "src": str(src)}
        self.state["managed"] = managed
        self.save()
        return len(want)

    # ---- migração do Deadlock Mod Manager (flatpak) ---------------------------
    def dmm_available(self):
        return self.addons is not None and (self.addons / ".dmm.json").exists()

    def migrate_from_dmm(self):
        dmm = json.loads((self.addons / ".dmm.json").read_text(encoding="utf-8"))["mods"]
        local = []
        for d in DMM_DATA_DIRS:
            if (d / "state.json").exists():
                state = json.loads(json.loads((d / "state.json").read_text(encoding="utf-8"))["local-config"])["state"]
                local = sorted(state["localMods"], key=lambda m: m.get("installOrder", 0))
                break
        # Sem o state do app antigo, importa só pelo .dmm.json (nome/capa vêm na checagem de atualizações).
        known = {m["id"] for m in local}
        local += [{"id": mid, "name": f"Mod {mid}", "remoteUrl": f"https://gamebanana.com/mods/{mid}"}
                  for mid in dmm if mid not in known]
        imported = []
        for lm in local:
            mid = lm["id"]
            if self.get(mid) or mid not in dmm:
                continue
            info = dmm[mid]
            d = self.mod_dir(mid)
            d.mkdir(parents=True, exist_ok=True)
            vpks = []
            for cur, orig in zip(info["currentVpks"], info["originalVpkNames"]):
                src = self.addons / cur
                if src.exists():
                    name = _safe(orig)
                    shutil.move(str(src), d / name)
                    vpks.append({"name": name, "archive": orig, "enabled": True})
            # Variantes guardadas pelo app antigo como "<id>_<nome>.vpk" ficam desativadas.
            for p in sorted(self.addons.glob(f"{mid}_*.vpk")):
                name = _safe(p.name[len(mid) + 1:])
                if any(v["name"] == name for v in vpks):
                    p.unlink()
                    continue
                shutil.move(str(p), d / name)
                vpks.append({"name": name, "archive": name, "enabled": False})
            if not vpks:
                continue
            files = []
            for sd in lm.get("selectedDownloads") or []:
                fid = sd["url"].rsplit("/", 1)[-1]
                files.append({"file_id": fid, "name": sd["name"], "md5": sd.get("md5Checksum"),
                              "size": sd.get("size", 0), "date": 0})
            self.mods.append({
                "id": mid, "name": lm["name"], "author": lm.get("author", ""),
                "category": lm.get("category", ""), "hero": lm.get("hero") or lm.get("detectedHero") or "",
                "thumb": lm.get("thumbnailUrl"), "url": lm.get("remoteUrl"), "remote": True, "modified": 0,
                "files": files, "vpks": vpks, "enabled": bool(info.get("enabled", True)),
                "installed_at": int(time.time()), "update": False,
            })
            imported.append(lm["name"])
        (self.addons / ".dmm.json").rename(DATA / "dmm-backup.json")
        self.save()
        return imported
