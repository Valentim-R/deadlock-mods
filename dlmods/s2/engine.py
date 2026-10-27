"""Monta o retrato de um herói (com ou sem skin) do jeito que a tela de seleção do jogo mostra."""
import os
import threading

import numpy as np

from . import heroes, renderer, scene
from . import model as s2model
from .vpk import VPK


class Engine:
    def __init__(self, citadel_dir):
        self.game = heroes.Game(str(citadel_dir), VPK)
        self.lock = threading.Lock()

    def prepare(self, hero_name, mod_vpks=()):
        """Parte pesada (CPU, sem OpenGL): malha posada, materiais e cena. Pode rodar em thread."""
        with self.lock:   # o índice do jogo é carregado uma vez só
            game = self.game
            vmdl = game.model_for(hero_name)
            if not vmdl:
                raise LookupError(f"herói desconhecido: {hero_name}")
            clips = game.select_clips()
            code = game.code_for(hero_name)
            portrait = game.portrait_map(code)
        res = heroes.Resolver(game, [VPK(p) for p in mod_vpks if os.path.exists(p)])
        raw = res.read(vmdl + "_c")
        data = s2model.resource_kv(raw)
        skel = res.skeleton_for(data)
        if not skel:
            raise LookupError(f"esqueleto não encontrado para {vmdl}")
        model = s2model.Model(raw, res.read(skel + "_c"))
        sc = scene.load(game.dir, portrait, VPK)
        anim = (sc or {}).get("anim") or ""
        pose = deltas = None
        if anim:   # a pose que a cena de retrato usa (a cena toca uma vez e para no último frame)
            with self.lock:
                clip = game.clip_named(anim, skel)
            if clip:   # clip NM (o sistema atual do jogo)
                pose = s2model.clip_pose(s2model.resource_kv(res.read(clip)), -1)
            else:      # sequência antiga embutida no vmdl (do mod, senão do jogo)
                for src in (raw, game.G.read(vmdl + "_c") if vmdl.lower() + "_c" in game.G.files else None):
                    deltas = s2model.legacy_pose(src, anim) if src else None
                    if deltas:
                        break
        if pose is None and not deltas:
            clip = clips.get(skel.lower())
            pose = s2model.clip_pose(s2model.resource_kv(res.read(clip))) if clip else None
        draws = model.build(pose, group_mask=model.group_mask((sc or {}).get("bodygroups")), deltas=deltas)
        hide = TWEAKS.get(code, {}).get("hide", ())
        draws = drop_strays([d for d in draws if not any(h in d["material"] for h in hide)])
        posed = getattr(model, "posed", {})
        heads = [posed[b] for b in TWEAKS.get(code, {}).get("heads", ()) if b in posed]
        if heads:   # enquadra entre várias cabeças (ex.: Mo e o Krill montado)
            head = np.mean(heads, axis=0)
        else:
            head = posed.get("head") if "head" in posed else next((v for k, v in posed.items() if "head" in k.lower()), None)
        mats = {d["material"]: renderer.material_info(res.read, d["material"]) for d in draws}
        return {"draws": draws, "materials": mats, "scene": sc, "head": head, "code": code}


def drop_strays(draws):
    """Tira malhas soltas longe do corpo (armas presas em ossos de IK que o jogo posiciona na hora)."""
    if len(draws) < 2:
        return draws
    allp = np.concatenate([d["pos"][d["idx"]] for d in draws])
    lo, hi = np.percentile(allp, 15, axis=0), np.percentile(allp, 85, axis=0)
    pad = (hi[2] - lo[2]) * 0.35
    lo, hi = lo - pad, hi + pad
    keep = []
    for d in draws:
        p = d["pos"][d["idx"]]
        if np.all(p.max(0) >= lo) and np.all(p.min(0) <= hi):
            keep.append(d)
    return keep or draws


def head_on_image(r, job, img):
    """[x, y, tamanho] da cabeça em pixels na imagem do último render (tamanho ≈ altura da cabeça), ou None."""
    if job.get("head") is None:
        return None
    allp = np.concatenate([d["pos"][d["idx"]] for d in job["draws"]])
    height = allp[:, 2].max() - allp[:, 2].min()
    head = np.asarray(job["head"], float)
    ax, ay = renderer.project(r.last_mvp, head)
    _bx, by = renderer.project(r.last_mvp, head + [0, 0, height * 0.12])
    return [ax * img.width(), ay * img.height(), abs(ay - by) * img.height()]


def hue(job):
    """Matiz/saturação dominante das luzes da cena de retrato (cor do fundo do card)."""
    import colorsys
    import numpy as np
    lights = (job.get("scene") or {}).get("lights") or []
    if not lights:
        return None
    c = sum(np.clip(l["color"], 0, None) for l in lights)
    c = np.clip(c / max(1e-6, c.max()), 0, 1)
    h, _l, s = colorsys.rgb_to_hls(*c)
    return [float(h), float(max(s, 0.45))]


# Ajustes por herói (codinome do heroes.vdata): frame = fração do corpo no card, front = quanto girar
# a câmera pra frente do herói (0 = ângulo da cena do jogo), hide = materiais que não entram no card,
# heads = ossos pra centralizar (média), head_weight = quanto o busto centraliza na cabeça,
# turn = giro extra da câmera em graus.
TWEAKS = {
    "hero_krill": {"frame": 0.9, "heads": ["head", "rider_head"]},   # a cabeça do Mo e a do Krill montado
    "hero_ratking": {"frame": 0.95, "hide": ["ratking_weapon"]},   # a arma fica solta longe dele
    "hero_hornet": {"front": 0.6},       # a pose de verdade vem do grafo de animação; a sequência fica de lado
    "hero_familiar": {"frame": 1.0},     # mostra o travesseiro-balão
    "hero_viscous": {"frame": 0.8, "front": 0.6},
    "hero_operative": {"turn": -150.0},  # a cena dele é provisória (a do Infernus) e mostra as costas
    "hero_forge": {"frame": 0.5, "head_weight": 1.0},
}


def render(r, job, size=(480, 640), frame=0.42, pad=(0, 0, 0, 0)):
    """Parte OpenGL (thread da interface): desenha o que prepare() montou (busto por padrão)."""
    tw = TWEAKS.get(job.get("code"), {})
    try:
        mats = {k: r.material(v) for k, v in job["materials"].items()}
        return r.render(job["draws"], mats, size, scene=job["scene"], fov=30.0, frame=tw.get("frame", frame),
                        head=job.get("head"), front=tw.get("front", 0.0), head_weight=tw.get("head_weight", 0.7),
                        turn=tw.get("turn", 0.0), pad=pad)
    finally:
        r.clear()
