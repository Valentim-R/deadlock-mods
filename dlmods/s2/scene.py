"""Lê a cena de retrato do herói (maps/ui/hero_prefabs/<x>.vpk): câmera e luzes, como o jogo usa."""
import os
import re
import numpy as np
from . import model as s2model


def _vec(s):
    return np.array([float(x) for x in str(s).split()]) if isinstance(s, str) else np.array(s, float)


def forward(angles):
    p, y = np.radians(angles[0]), np.radians(angles[1])
    return np.array([np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), -np.sin(p)])


def matrix(angles):
    """QAngle (pitch, yaw, roll) -> matriz 3x3 da Source (colunas: frente, esquerda, cima)."""
    p, y, r = np.radians(angles[:3]) if len(angles) >= 3 else (*np.radians(angles[:2]), 0.0)
    sp, cp, sy, cy, sr, cr = np.sin(p), np.cos(p), np.sin(y), np.cos(y), np.sin(r), np.cos(r)
    return np.array([[cp * cy, sp * sr * cy - cr * sy, sp * cr * cy + sr * sy],
                     [cp * sy, sp * sr * sy + cr * cy, sp * cr * sy - sr * cy],
                     [-sp, sr * cp, cr * cp]])


def load(citadel_dir, portrait_map, vpk_cls):
    """portrait_map: 'maps/ui/hero_prefabs/necro.vmap' -> dict(camera=..., lights=[...]) ou None."""
    if not portrait_map:
        return None
    name = os.path.splitext(os.path.basename(portrait_map))[0]
    path = os.path.join(citadel_dir, os.path.dirname(portrait_map), name + ".vpk")
    if not os.path.exists(path):
        return None
    v = vpk_cls(path)
    ents_files = [n for n in v.files if n.endswith(".vents_c")]
    cam, lights, root, info = None, [], None, {}
    for ef in ents_files:
        e = s2model.resource_kv(v.read(ef))
        for x in e.get("m_entityKeyValues", []):
            kv = x.get("keyValues3Data") or {}
            vals = kv.get("values", kv) if isinstance(kv, dict) else {}
            cls = vals.get("classname")
            if cls == "prop_dynamic" and str(vals.get("targetname", "")).endswith("hero_root"):
                root = (_vec(vals.get("origin", "0 0 0")), matrix(_vec(vals.get("angles", "0 0 0"))))
                info = {"anim": vals.get("StartingAnim") or vals.get("IdleAnim") or "",
                        "bodygroups": dict((k, int(v)) for k, v in
                                           re.findall(r"(\w+)\s*=\s*(-?\d+)", str(vals.get("bodygroups") or "")))}
            elif cls == "point_camera" and cam is None:
                cam = {"origin": _vec(vals["origin"]), "angles": _vec(vals["angles"]), "fov": float(vals.get("FOV", 50))}
            elif cls in ("light_barn", "light_omni2", "light_spot", "light_omni"):
                b = float(vals.get("brightness", 1.0))
                col = np.array(vals.get("color", [255, 255, 255])[:3], float) / 255.0
                if abs(b) < 1e-3:
                    continue
                lights.append({"pos": _vec(vals["origin"]), "dir": forward(_vec(vals["angles"])), "color": col * b})
    if cam is None:
        return None
    # O herói fica no hero_root, que pode estar girado (ex.: Mina a 268°); o modelo a gente desenha na origem
    # sem rotação, então câmera e luzes vão pro espaço local dele — senão a câmera cai nas costas.
    o, R = root if root else (np.zeros(3), np.eye(3))
    cam["origin"] = R.T @ (cam["origin"] - o)
    cam["dir"] = R.T @ forward(cam["angles"])
    for l in lights:
        l["pos"] = R.T @ (l["pos"] - o)
        l["dir"] = R.T @ l["dir"]
    return {"camera": cam, "lights": lights, **info}
