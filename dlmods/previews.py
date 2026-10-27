"""Retratos 3D dos heróis com a skin aplicada, renderizados a partir dos arquivos do jogo (dlmods.s2).

A parte pesada (ler malha, pose e texturas) roda numa thread; o desenho OpenGL roda na thread da
interface. Cada retrato é renderizado uma vez só e fica guardado junto do mod (mods/<id>/.preview/),
então some quando o mod é apagado; o retrato padrão (sem skin) fica em renders/default/. Só é refeito
se a variante escolhida ou a versão do renderizador mudar (sobrescrevendo o mesmo arquivo).
"""
import json
import os
import re
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QImage

from .store import CACHE

RENDER_VERSION = 12
SIZE = (480, 640)
RENDERS = CACHE / "renders"
# A imagem é alargada em volta do busto o quanto o personagem passa dele (pad automático): o card usa
# só o busto, o efeito de "sair do card" usa a imagem toda, sem corte reto.


def vpk_paths(store, mod):
    if mod is None:
        return []
    d = store.mod_dir(mod["id"])
    chosen = [v for v in mod["vpks"] if v["enabled"]] or mod["vpks"]
    return [str(d / v["name"]) for v in chosen]


def place(store, hero, mod):
    """Arquivo do retrato: dentro da pasta do mod, ou renders/default pro padrão."""
    slug = re.sub(r"[^\w]+", "_", hero.lower()).strip("_")
    base = (store.mod_dir(mod["id"]) / ".preview") if mod else (RENDERS / "default")
    return base / f"{slug}.png"


def meta_for(paths):
    return {"v": RENDER_VERSION, "vpks": sorted(os.path.basename(p) for p in paths)}


def stored(store, hero, mod):
    """(caminho do PNG, meta) do retrato já renderizado e em dia com a variante escolhida, ou None."""
    path = place(store, hero, mod)
    try:
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    want = meta_for(vpk_paths(store, mod))
    if any(meta.get(k) != v for k, v in want.items()) or not path.exists():
        return None
    return path, meta


class Previews(QObject):
    def __init__(self, store, run):
        super().__init__()
        self.store, self.run = store, run
        self.engine = None
        self.renderer = None
        self.broken = None       # motivo, se não dá pra renderizar neste PC
        self.waiting = {}        # chave -> [callbacks]
        self.failed = set()
        self.queue = []
        self.busy = False
        self.dir = RENDERS
        self.mem = {}            # caminho -> (mtime, QImage, meta): sem reler/decodificar o PNG a cada atualização
        self._cleanup_old()

    def _cleanup_old(self):
        """Apaga o cache antigo (um arquivo por hash, que acumulava a cada mudança)."""
        try:
            for p in self.dir.glob("*.*"):
                if p.suffix in (".png", ".json"):
                    p.unlink()
        except OSError:
            pass

    def vpk_paths(self, mod):
        return vpk_paths(self.store, mod)

    def _place(self, hero, mod):
        return place(self.store, hero, mod)

    def _meta(self, paths):
        return meta_for(paths)

    def request(self, hero, mod, cb):
        """Chama cb(busto, hue, imagem inteira, retângulo do busto) quando o retrato existir (na hora, se já estiver guardado).
        hue = [matiz, saturação] da luz da cena do herói, ou None."""
        if self.broken or not self.store.game or not hero or hero == "Outros":
            return
        paths = self.vpk_paths(mod)
        path = self._place(hero, mod)
        key = str(path)
        if key in self.failed:
            return
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        hit = self.mem.get(key)
        if not (hit and hit[0] == mtime) and mtime is not None:
            meta = self._read_meta(path)
            img = QImage(str(path)) if meta else QImage()
            hit = (mtime, img, meta) if meta and not img.isNull() else None
            if hit:
                self.mem[key] = hit
        if hit and {k: hit[2].get(k) for k in ("v", "vpks")} == self._meta(paths):
            self._deliver(cb, hit[1], hit[2].get("hue"), hit[2].get("crop"))
            return
        first = key not in self.waiting
        self.waiting.setdefault(key, []).append(cb)
        if first:
            self.queue.append((key, hero, paths))
            self._next()

    def _next(self):
        if self.busy or not self.queue:
            return
        self.busy = True
        key, hero, paths = self.queue.pop(0)

        def prepare():
            if self.engine is None:
                from .s2.engine import Engine
                self.engine = Engine(self.store.game / "game/citadel")
            job = self.engine.prepare(hero, paths)
            job["paths"] = paths   # vai pro .json do retrato (variante escolhida)
            return job
        self.run(prepare, lambda job: self._draw(key, job), lambda err: self._fail(key, err))

    def _draw(self, key, job):
        try:
            from .s2 import engine, renderer
            if self.renderer is None:
                self.renderer = renderer.Renderer()
            img = engine.render(self.renderer, job, SIZE, pad="auto")
            hue = engine.hue(job)
            path = Path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(path))
            meta = self._meta(job.get("paths", []))
            meta["hue"] = hue
            meta["crop"] = renderer.pad_rect(SIZE, self.renderer.last_pad)
            meta["head"] = engine.head_on_image(self.renderer, job, img)   # pros ícones do herói (dlmods.icons)
            path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
        except Exception as e:
            traceback.print_exc()
            if self.renderer is None:
                self.broken = str(e) or repr(e)    # sem OpenGL: desiste de vez nesta sessão
                self.queue.clear()
            self._fail(key, str(e) or repr(e))
            return
        for cb in self.waiting.pop(key, []):
            try:
                self._deliver(cb, img, hue, meta["crop"])
            except RuntimeError:        # card já foi destruído
                pass
        self._finish()

    @staticmethod
    def _deliver(cb, full, hue, crop):
        """cb(busto, hue, imagem inteira, retângulo do busto nela)."""
        card = full.copy(*crop) if crop else full
        cb(card, hue, full, crop)

    @staticmethod
    def _read_meta(path):
        try:
            return json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _fail(self, key, err):
        print(f"prévia falhou: {err}")
        if "No module named" in str(err):
            self.broken = str(err)      # ex.: AppImage sem numpy
            self.queue.clear()
        self.failed.add(key)
        self.waiting.pop(key, None)
        self._finish()

    def _finish(self):
        self.busy = False
        QTimer.singleShot(0, self._next)
