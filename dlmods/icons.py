"""Ícones do herói (tela de seleção e barra de cima da partida) gerados a partir do retrato 3D da skin.

Muitas skins só trocam o modelo e o jogo continua mostrando o rosto original nos ícones. O app renderiza a
skin ativa de cada herói, recorta os ícones em volta da cabeça e junta tudo num pacote próprio
(generated/icons_dir.vpk) que entra como pak01, na frente de todos os mods: os ícones que a skin traz
também são trocados, pra todos ficarem no mesmo estilo (fundo "pôster" na cor do herói).

Ícones (panorama/images/heroes/<nome>_<tipo>_psd.vtex_c, o <nome> vem do heroes.vdata):
  card, card_critical, card_gloat  seleção de herói / placar (busto)
  vertical                         barra de cima durante a partida
  sm, mm                           cabeça recortada com contorno (lista de heróis, minimapa)
As texturas são escritas no formato do jogo (BGRA8888 sem compressão), por cima do cabeçalho da textura
original de mesmo tamanho, então não precisa do compilador de recursos da Valve.
"""
import os
import struct
import zlib

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath

from .s2 import heroes
from .s2.vpk import VPK
from .s2.vtex import Texture

KINDS = ("card", "card_critical", "card_gloat", "vertical", "sm", "mm")
VDATA_KEY = {"card": "m_strIconHeroCard", "card_critical": "m_strIconHeroCardCritical",
             "card_gloat": "m_strIconHeroCardGloat", "vertical": "m_strTopBarVertical",
             "sm": "m_strIconImageSmall", "mm": "m_strMinimapImage"}
ICON_VERSION = 3


def vtex_path(vdata_value):
    """'file://{images}/heroes/haze_sm.psd' -> 'panorama/images/heroes/haze_sm_psd.vtex_c'."""
    p = vdata_value.replace("file://{images}/", "panorama/images/")
    stem, _, ext = p.rpartition(".")
    return f"{stem}_{ext}.vtex_c".lower()


def icon_paths(vdata_entry):
    """{tipo: caminho no vpk} dos ícones que o heroes.vdata declara pro herói."""
    return {k: vtex_path(vdata_entry[key]) for k, key in VDATA_KEY.items() if vdata_entry.get(key)}


# ---- recortes ---------------------------------------------------------------------------------------
def _crop(img, cx, top, w, h):
    """Recorte (w x h) com o centro horizontal em cx e o topo em top; o que sair da imagem fica transparente."""
    out = QImage(round(w), round(h), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QPointF(-(cx - w / 2), -top), img)
    p.end()
    return out


def _fit(img, w, h):
    return img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _fade_bottom(img, frac):
    """Some com a parte de baixo aos poucos (o corte reto no pescoço fica feio na cabeça recortada)."""
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    g = QLinearGradient(0, img.height() * (1 - frac), 0, img.height())
    g.setColorAt(0, QColor(0, 0, 0, 255))
    g.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(0, img.height() * (1 - frac), img.width(), img.height() * frac + 1), g)
    p.end()
    return img


def _outline(img, px, color=QColor(12, 12, 14)):
    """Contorno escuro em volta da silhueta, como nos ícones pequenos do jogo."""
    sil = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
    sil.fill(color)
    p = QPainter(sil)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, img)
    p.end()
    out = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    steps = 16
    for i in range(steps):
        a = 2 * np.pi * i / steps
        p.drawImage(QPointF(px * np.cos(a), px * np.sin(a)), sil)
    p.drawImage(0, 0, img)
    p.end()
    return out


def _lift(img):
    """Clareia um pouco: o render sai mais escuro que a arte pintada dos ícones do jogo."""
    im = img.convertToFormat(QImage.Format_ARGB32)
    a = np.frombuffer(im.constBits(), np.uint8).reshape(im.height(), im.bytesPerLine())[:, :im.width() * 4]
    a = a.reshape(im.height(), im.width(), 4).astype(np.float32) / 255
    rgb = 1 - (1 - a[..., :3]) ** 1.35
    a[..., :3] = rgb
    out = QImage(np.ascontiguousarray((a * 255 + 0.5).astype(np.uint8)).tobytes(), im.width(), im.height(),
                 im.width() * 4, QImage.Format_ARGB32)
    return out.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def _hsl(h, s, l, a=255):
    c = QColor.fromHslF(h % 1.0, min(1.0, s), min(1.0, l))
    c.setAlpha(a)
    return c


def _poster(fig, hue):
    """Figura sobre o fundo 'pôster' na cor do herói (faixa diagonal + listra clara, como os cards do app)."""
    h, s = hue or (0.08, 0.6)
    w, ht = fig.width(), fig.height()
    out = QImage(w, ht, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, ht)
    g.setColorAt(0, _hsl(h, s * .75, .12))
    g.setColorAt(1, _hsl(h, s * .6, .05))
    p.fillRect(QRectF(0, 0, w, ht), g)
    for a, b, color in ((.62, .18, _hsl(h, s, .33)), (.66, .22, _hsl(h, s, .7, 210))):
        band = QPainterPath()
        band.moveTo(0, ht * a)
        band.lineTo(w, ht * b)
        if color.alpha() < 255:   # listra fina
            band.lineTo(w, ht * (b + .04))
            band.lineTo(0, ht * (a + .04))
        else:
            band.lineTo(w, ht)
            band.lineTo(0, ht)
        p.fillPath(band, color)
    p.drawImage(0, 0, fig)
    p.end()
    return out


def make_images(full, crop, head, hue=None):
    """full: retrato inteiro (com pad); crop: (x, y, w, h) do busto nele; head: [x, y, altura da cabeça] em pixels;
    hue: [matiz, saturação] da cena do herói. -> {tipo: QImage} (o tamanho final sai em textures())."""
    hx, hy, head_px = head
    full = _lift(full)
    # o osso da cabeça fica perto da nuca; o topo de verdade (cabelo, chapéu, cabeção de remodel) vem da silhueta
    top = _silhouette_top(full, hx, head_px * 0.5, hy)
    if top is not None:
        head_px = min(max((hy - top) / 0.92, head_px * 0.8), head_px * 1.6)
    out = {"card": _poster(full.copy(*crop), hue)}
    # barra de cima: cabeça e ombros, a cabeça na metade de cima
    h = head_px * 2.5
    out["vertical"] = _poster(_crop(full, hx, hy - head_px * 1.0, h * 0.6, h), hue)
    # cabeça recortada (o contorno e o esmaecido entram depois, já no tamanho final)
    s = head_px * 1.65
    out["sm"] = out["mm"] = _crop(full, hx, hy - head_px * 1.0, s, s)
    return out


def _silhouette_top(img, cx, half, below):
    """Primeira linha (de cima pra baixo) com silhueta nas colunas cx±half, acima de `below`."""
    a = np.frombuffer(img.convertToFormat(QImage.Format_ARGB32).constBits(), np.uint8)
    a = a.reshape(img.height(), -1)[:, :img.width() * 4].reshape(img.height(), img.width(), 4)[..., 3]
    x0, x1 = max(0, int(cx - half)), min(img.width(), int(cx + half))
    # a linha precisa cobrir uns 30% da janela: chifre, antena e pontas finas não contam como topo
    rows = np.nonzero((a[:int(below), x0:x1] > 128).mean(1) >= 0.3)[0] if x1 > x0 and below > 0 else []
    return float(rows[0]) if len(rows) else None


# ---- textura .vtex_c ---------------------------------------------------------------------------------
def build_vtex(template, img):
    """Textura no formato da original `template` (BGRA8888, mesmas dimensões e mips) com a imagem `img`."""
    t = Texture(template)
    if t.format != 28 or t.faces != 1 or t.comp_sizes:
        raise ValueError("formato de textura inesperado")
    body = bytearray()
    for lvl in range(t.mips - 1, -1, -1):   # menor mip primeiro
        w, h = t.size(lvl)
        im = _fit(img, w, h).convertToFormat(QImage.Format_ARGB32)   # ARGB32 na memória = B G R A
        bpl = im.bytesPerLine()
        raw = bytes(im.constBits())
        body += b"".join(raw[y * bpl:y * bpl + w * 4] for y in range(h))
    # o cabeçalho fica igual ao da original: o 1º campo é o tamanho do cabeçalho (só os blocos), não do arquivo
    return bytes(template[:t.data_off]) + bytes(body)


def textures(game_read, paths, images):
    """{caminho no vpk: bytes} pros ícones; images = make_images(); paths = icon_paths()."""
    out = {}
    for kind, path in paths.items():
        src = images.get(kind) or images.get("card")
        tpl = game_read(path)
        t = Texture(tpl)
        img = src
        if kind in ("sm", "mm"):
            img = _fade_bottom(_outline(_fit(src, t.width, t.height), max(1.0, t.width / 42)), 0.2)
        out[path] = build_vtex(tpl, img)
    return out


# ---- pacote .vpk (v1, tudo embutido no _dir) ---------------------------------------------------------
def write_vpk(path, files):
    tree = {}
    for name, data in files.items():
        folder, _, rest = name.rpartition("/")
        stem, _, ext = rest.rpartition(".")
        tree.setdefault(ext, {}).setdefault(folder or " ", []).append((stem, data))
    t = bytearray()
    blob = bytearray()
    for ext, folders in tree.items():
        t += ext.encode() + b"\0"
        for folder, entries in folders.items():
            t += folder.encode() + b"\0"
            for stem, data in entries:
                t += stem.encode() + b"\0"
                t += struct.pack("<IHHIIH", zlib.crc32(data) & 0xFFFFFFFF, 0, 0x7FFF, len(blob), len(data), 0xFFFF)
                blob += data
            t += b"\0"
        t += b"\0"
    t += b"\0"
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(struct.pack("<III", 0x55AA1234, 1, len(t)) + t + blob)
    os.replace(tmp, path)


# ---- o "mod" de ícones -------------------------------------------------------------------------------
_games = {}


def _game(citadel):
    if str(citadel) not in _games:
        _games[str(citadel)] = heroes.Game(str(citadel), VPK)
    return _games[str(citadel)]


def plan(store):
    """[(herói, mod da skin ativa, {tipo: caminho})] de cada herói com skin ligada.
    Skin ativa = a primeira skin ligada daquele herói na ordem de prioridade (a que o jogo carrega)."""
    game = _game(store.game / "game/citadel")
    game.hero_models()
    vdata = game._vdata
    enabled = [m for m in store.mods if m["enabled"]]
    out, seen = [], set()
    for m in enabled:
        if m.get("category") != "Skins" or not m.get("hero"):
            continue
        code = game.code_for(m["hero"])
        if not code or code in seen or not isinstance(vdata.get(code), dict):
            continue
        seen.add(code)
        paths = {k: p for k, p in icon_paths(vdata[code]).items() if p in game.G.files}
        if paths:
            out.append((m["hero"], m, paths))
    return out


def build(store, dest_dir):
    """Monta (ou reaproveita) o pacote de ícones. -> (caminho do .vpk ou None, [(herói, mod) sem retrato ainda])."""
    from . import previews
    todo, missing, sig = [], [], [ICON_VERSION]
    for hero, mod, paths in plan(store):
        got = previews.stored(store, hero, mod)
        if not got or not got[1].get("head"):
            missing.append((hero, mod))
            continue
        todo.append((got, paths))
        sig.append([hero, mod["id"], got[0].stat().st_mtime_ns])
    if not todo:
        return None, missing
    dest = dest_dir / "icons_dir.vpk"
    if store.state.get("icons_sig") == sig and dest.exists():
        return dest, missing
    game = _game(store.game / "game/citadel")
    files = {}
    for (png, meta), paths in todo:
        full = QImage(str(png))
        if full.isNull():
            continue
        files.update(textures(game.G.read, paths, make_images(full, meta["crop"], meta["head"], meta.get("hue"))))
    dest_dir.mkdir(parents=True, exist_ok=True)
    write_vpk(str(dest), files)
    store.state["icons_sig"] = sig
    return dest, missing
