"""Descobre, a partir dos arquivos do jogo (+ mods), o modelo, esqueleto e pose de seleção de cada herói."""
import os
import re

from . import model as s2model

LOC = "resource/localization/citadel_gc_hero_names/citadel_gc_hero_names_english.txt"


class Game:
    def __init__(self, citadel_dir, vpk_cls):
        self.dir = citadel_dir
        self.G = vpk_cls(os.path.join(citadel_dir, "pak01_dir.vpk"))
        self._heroes = None
        self._clips = None
        self._names = None
        self._display = {}
        self._vdata = None
        self._by_name = None

    def names(self):
        """Nome exibido (minúsculo) -> codinome (hero_xxx)."""
        if self._names is None:
            self._names = {}
            try:
                txt = open(os.path.join(self.dir, LOC), encoding="utf-8-sig", errors="replace").read()
                models = self.hero_models()   # só códigos de herói de verdade (tem "hero_doorman_sort" etc.)
                for code, name in re.findall(r'"(hero_\w+):n"\s+"([^"]+)"', txt):
                    if code in models:
                        self._names.setdefault(name.lower(), code)
                        self._display.setdefault(code, name)
            except OSError:
                pass
        return self._names

    def hero_models(self):
        if self._heroes is None:
            if self._vdata is None:
                self._vdata = s2model.resource_kv(self.G.read("scripts/heroes.vdata_c"))
            h = self._vdata
            self._heroes = {k: v["m_strModelName"] for k, v in h.items()
                            if isinstance(v, dict) and v.get("m_strModelName")}
        return self._heroes

    def code_for(self, display_name):
        n = display_name.lower()
        names = self.names()
        if n in names:
            return names[n]
        # o GameBanana escreve sem o artigo ("Doorman" = "The Doorman")
        return next((c for k, c in names.items() if k.removeprefix("the ") == n.removeprefix("the ")), None)

    def model_for(self, display_name):
        code = self.code_for(display_name)
        return self.hero_models().get(code) if code else None

    NOT_HEROES = {"hero_base", "hero_targetdummy", "hero_genericperson", "hero_testhero"}

    def released(self):
        """Nomes (como o jogo escreve) dos heróis jogáveis: não desativados, com modelo e cena de retrato.
        "Em desenvolvimento" não conta: Violet e Baba têm essa marca e já estão no jogo."""
        self.names()
        models = self.hero_models()
        out = []
        for code, v in self._vdata.items():
            if (isinstance(v, dict) and code in models and code not in self.NOT_HEROES and not v.get("m_bDisabled")
                    and v.get("m_strUIPortraitMap") and code in self._display):
                out.append(self._display[code])
        return sorted(out, key=str.lower)

    def portrait_map(self, code):
        if self._vdata is None:
            self._vdata = s2model.resource_kv(self.G.read("scripts/heroes.vdata_c"))
        return (self._vdata.get(code) or {}).get("m_strUIPortraitMap")

    def select_clips(self):
        """esqueleto (.vnmskel) -> clip ui_hero_select."""
        if self._clips is None:
            self._clips = {}
            for n in self.G.files:
                if n.endswith("/ui_hero_select.vnmclip_c"):
                    try:
                        sk = s2model.resource_kv(self.G.read(n))["m_skeleton"]
                    except Exception:
                        continue
                    self._clips.setdefault(sk.lower(), n)
        return self._clips


    def clip_named(self, anim, skeleton):
        """Clip NM chamado <anim> (ou <prefixo>_<anim>) do mesmo esqueleto, ex.: vampirebat_ui_pose_alt."""
        if self._by_name is None:
            self._by_name = {}
            for n in self.G.files:
                if n.endswith(".vnmclip_c"):
                    self._by_name.setdefault(os.path.basename(n)[:-len(".vnmclip_c")], []).append(n)
        anim, skeleton = anim.lower(), skeleton.lower()
        cands = self._by_name.get(anim, []) + [n for b, ns in self._by_name.items() if b.endswith("_" + anim) for n in ns]
        for n in cands:
            try:
                if s2model.resource_kv(self.G.read(n))["m_skeleton"].lower() == skeleton:
                    return n
            except Exception:
                continue
        return None


class Resolver:
    """Lê arquivos com os mods (vpks) por cima do jogo."""

    def __init__(self, game, mod_vpks=()):
        self.game = game
        self.mods = list(mod_vpks)

    def has(self, path):
        path = path.lower()
        return any(path in m.files for m in self.mods) or path in self.game.G.files

    def read(self, path, start=0, length=None):
        path = path.lower()
        for m in self.mods:
            if path in m.files:
                return m.read(path, start, length)
        return self.game.G.read(path, start, length)

    def skeleton_for(self, model_data):
        """Esqueleto de animação (.vnmskel) do modelo: o declarado nele, ou o que tem os mesmos ossos."""
        for r in model_data.get("m_vecNmSkeletonRefs") or []:
            if self.has(r + "_c"):
                return r
        names = set(model_data["m_modelSkeleton"]["m_boneName"])
        best, score = None, 0
        for sk in self.game.select_clips():
            try:
                ids = s2model.resource_kv(self.read(sk + "_c"))["m_boneIDs"]
            except Exception:
                continue
            sc = len(names & set(ids)) / max(1, len(ids))
            if sc > score:
                best, score = sk, sc
        return best if score > 0.8 else None
