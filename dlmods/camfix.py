"""Correção de câmera dos mods de modelo de herói (mod padrão do app).

Na atualização do Deadlock de 16/10/2026 o jogo passou a ler os attachments de câmera do modelo
(standing_pivot, crouching_pivot, near_*, far_*, gunaim_*) num layout de coordenadas novo. Skins
compiladas antes disso ainda têm o layout antigo e a câmera para de seguir o jogador. A correção
gira esses attachments do jeito novo: rotação (0.5, 0.5, 0.5, 0.5) aplicada, posição (x, y, z) ->
(z, x, y). Mesma ideia do Fnxuxa_Mod_Fov (Fnxuxa), reimplementada aqui.

Só mexe nos .vpk guardados pelo app (mods/<id>/); o original vem de novo se o mod for reinstalado.
"""
import hashlib
import os
import struct
import zlib

from .s2 import kv3

CAMERA = {"standing_pivot", "crouching_pivot", "near_00", "near_01", "near_02",
          "far_00", "far_01", "far_02", "gunaim_00", "gunaim_01", "gunaim_02"}
STRIDE = 96                       # bytes entre attachments na tabela de floats do bloco
OLD = (-0.5, -0.5, -0.5, 0.5)     # rotação do standing_pivot no layout antigo
NEW = (0.0, 0.0, 0.0, 1.0)
RR = (0.5, 0.5, 0.5, 0.5)         # rotação antiga do root_motion: (x,y,z) -> (z,x,y)


class Skip(Exception):
    """Não dá pra corrigir com segurança (o mod fica como está)."""


# ---- matemática ----------------------------------------------------------------
def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _rotv(q, v):
    r = _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return r[:3]


def _clean(vals, eps):
    return [0.0 if abs(x) < eps else x for x in vals]


def _near(a, b, tol=1e-3):
    return len(a) == 4 and all(abs(x - y) <= tol for x, y in zip(a, b))


def _f32(rows):
    return b"".join(struct.pack(f"<{len(r)}f", *r) for r in rows)


# ---- KV3 v5: separa / junta os dois buffers ---------------------------------------
def _lz4_literals(data):
    """Bloco LZ4 válido só com literais (sem compressão de verdade, mas qualquer leitor aceita)."""
    n = len(data)
    if n < 15:
        return bytes([n << 4]) + data
    ext, rest = bytearray([0xF0]), n - 15
    while rest >= 255:
        ext.append(255)
        rest -= 255
    ext.append(rest)
    return bytes(ext) + data


def _split(blk):
    if len(blk) < 120 or blk[:4] != b"\x05\x33VK":
        raise ValueError("kv3 não suportado")
    method = struct.unpack_from("<I", blk, 20)[0]
    u1, c1, u2, c2 = struct.unpack_from("<4i", blk, 72)
    if 120 + c1 + c2 != len(blk):
        raise Skip("bloco KV3 com dados extras")
    r1, r2 = blk[120:120 + c1], blk[120 + c1:]
    if method == 0:
        return method, bytearray(r1), r2
    if method == 1:
        return method, kv3.lz4_block(r1, bytearray(), u1), bytes(kv3.lz4_block(r2, bytearray(), u2))
    if method == 2:
        return method, bytearray(kv3.zstd_decompress(r1)), kv3.zstd_decompress(r2)
    raise Skip("compressão desconhecida")


def _join(blk, method, a, b):
    a, b = bytes(a), bytes(b)
    if method == 0:
        ca, cb = a, b
    elif method == 1:
        ca, cb = _lz4_literals(a), _lz4_literals(b)
    else:
        if kv3._zstd is None:
            raise Skip("zstd indisponível")
        ca, cb = kv3._zstd.compress(a), kv3._zstd.compress(b)
    nb = bytearray(blk[:120]) + ca + cb
    struct.pack_into("<i", nb, 52, len(ca) + len(cb))
    struct.pack_into("<i", nb, 76, len(ca))
    struct.pack_into("<i", nb, 84, len(cb))
    return bytes(nb)


# ---- modelo (.vmdl_c) -------------------------------------------------------------
def _attachments(blk):
    root = kv3.parse(blk)
    out = []
    for it in root.get("m_attachments") or []:
        v = it.get("value") or {}
        names = v.get("m_influenceNames") or [""]
        out.append({"key": it.get("key", ""), "parent": names[0] if names else "",
                    "rots": [list(map(float, r)) for r in v.get("m_vInfluenceRotations") or []],
                    "offs": [list(map(float, r)) for r in v.get("m_vInfluenceOffsets") or []]})
    return out


def fix_model(model):
    """-> (bytes novos ou None, estado): fixed | already | nohero | unknown."""
    if len(model) < 16:
        return None, "nohero"
    m = bytearray(model)
    fsize, boff, cnt = struct.unpack_from("<I", m, 0)[0], *struct.unpack_from("<II", m, 8)
    if fsize != len(m) or cnt > 200 or 8 + boff + 12 * cnt > len(m):
        return None, "nohero"
    tbl = []
    for i in range(cnt):
        tp = 8 + boff + 12 * i
        o, s = struct.unpack_from("<II", m, tp + 4)
        tbl.append((m[tp:tp + 4].decode("ascii", "replace"), tp, tp + 4 + o, s))
        if tp + 4 + o + s > len(m):
            return None, "nohero"
    new, seen = {}, set()
    for idx, (kind, _tp, bs, s) in enumerate(tbl):
        if kind != "MDAT":
            continue
        blk = bytes(m[bs:bs + s])
        try:
            method, a, b = _split(blk)
        except ValueError:
            continue
        if b"standing_pivot" not in a:
            continue
        try:
            atts = _attachments(blk)
        except Exception:
            continue
        sp = next((x for x in atts if x["key"] == "standing_pivot"), None)
        if not sp or not sp["rots"]:
            continue
        if _near(sp["rots"][0], NEW):
            seen.add("already")
            continue
        if not _near(sp["rots"][0], OLD):
            seen.add("unknown")
            continue
        a0 = atts[0]
        r0 = a.find(_f32(a0["rots"]) + _f32(a0["offs"]))
        if r0 < 0:
            raise Skip("não achei a tabela de attachments com segurança")
        changed = 0
        for k, at in enumerate(atts):
            if at["parent"] != "root_motion" or at["key"] not in CAMERA:
                continue
            p = r0 + STRIDE * k
            chk = _f32(at["rots"]) + _f32(at["offs"])
            if a[p:p + len(chk)] != chk:
                raise Skip(f"layout inesperado em {at['key']}")
            q = _qmul(RR, at["rots"][0])
            n = sum(x * x for x in q) ** 0.5
            struct.pack_into("<4f", a, p, *_clean([x / n for x in q], 1e-5))
            struct.pack_into("<3f", a, p + 48, *_clean(_rotv(RR, at["offs"][0]), 1e-3))
            changed += 1
        if changed:
            new[idx] = _join(blk, method, a, b)
    if not new:
        return None, ("already" if "already" in seen else "unknown" if "unknown" in seen else "nohero")
    if all(len(nb) <= tbl[i][3] for i, nb in new.items()):   # cabe no lugar: só sobrescreve
        for i, nb in new.items():
            bs, s = tbl[i][2], tbl[i][3]
            m[bs:bs + s] = nb + b"\0" * (s - len(nb))
        return bytes(m), "fixed"
    # não cabe: remonta o arquivo com os blocos em sequência (alinhados em 16)
    order = sorted(range(cnt), key=lambda i: tbl[i][2])
    head = bytearray(m[:tbl[order[0]][2]])
    body = bytearray()
    pos = {}
    for i in order:
        data = new.get(i, bytes(m[tbl[i][2]:tbl[i][2] + tbl[i][3]]))
        while (len(head) + len(body)) % 16:
            body.append(0)
        pos[i] = (len(head) + len(body), len(data))
        body += data
    for i in range(cnt):
        tp = tbl[i][1]
        struct.pack_into("<II", head, tp + 4, pos[i][0] - (tp + 4), pos[i][1])
    out = head + body
    struct.pack_into("<I", out, 0, len(out))
    return bytes(out), "fixed"


# ---- VPK v2 -----------------------------------------------------------------------
def _entries(d):
    if len(d) < 12 or struct.unpack_from("<I", d, 0)[0] != 0x55AA1234:
        raise Skip("não é VPK")
    ver, tree = struct.unpack_from("<II", d, 4)
    if ver not in (1, 2):
        raise Skip(f"VPK v{ver} não suportado")
    hl = 28 if ver == 2 else 12
    end, p = hl + tree, hl

    def cstr():
        nonlocal p
        e = d.index(b"\0", p, end)
        s = d[p:e].decode("utf-8", "replace")
        p = e + 1
        return s
    out = []
    while ext := cstr():
        while folder := cstr():
            while name := cstr():
                crc, pre, arc, off, ln = struct.unpack_from("<IHHII", d, p)
                out.append({"path": (f"{name}.{ext}" if folder == " " else f"{folder}/{name}.{ext}"),
                            "rec": p, "arc": arc, "off": off, "len": ln})
                p += 18 + pre
    return hl, end, out


def fix_vpk(path):
    """Corrige os modelos de herói de um .vpk (v1 ou v2) no lugar. -> (estado, [modelos corrigidos])."""
    d = open(path, "rb").read()
    hl, end, files = _entries(d)
    if hl == 28:
        fsd, arch, other, sig = struct.unpack_from("<4I", d, 12)
        if sig:
            raise Skip("VPK assinado")
    else:   # v1: sem tamanhos de seção; os dados embutidos vão até o fim do arquivo
        fsd, arch, other = len(d) - end, 0, 0
    new = {}
    states = set()
    for f in files:
        lp = f["path"].lower()
        if not lp.endswith(".vmdl_c") or f["len"] < 64 or "backup" in lp:
            continue
        if f["arc"] != 0x7FFF:
            raise Skip("VPK com arquivos de dados separados")
        nm, st = fix_model(d[end + f["off"]:end + f["off"] + f["len"]])
        states.add(st)
        if nm is not None:
            new[f["path"]] = nm
    if not new:
        return ("already" if "already" in states else "unknown" if "unknown" in states else "nohero"), []
    tree = bytearray(d[hl:end])
    blob = bytearray()
    for f in sorted((f for f in files if f["arc"] == 0x7FFF), key=lambda f: f["off"]):
        data = new.get(f["path"], d[end + f["off"]:end + f["off"] + f["len"]])
        struct.pack_into("<I", tree, f["rec"] - hl, zlib.crc32(data) & 0xFFFFFFFF)
        struct.pack_into("<II", tree, f["rec"] - hl + 8, len(blob), len(data))
        blob += data
    hdr = bytearray(d[:hl])
    if hl == 28:
        struct.pack_into("<I", hdr, 12, len(blob))
    tail = d[end + fsd:]
    out = bytearray(hdr) + tree + blob + tail[:arch]
    if other == 48:
        out += hashlib.md5(tree).digest() + hashlib.md5(tail[:arch]).digest()
        out += hashlib.md5(out).digest()
    else:
        out += tail[arch:arch + other]
    tmp = f"{path}.tmp-camfix"
    with open(tmp, "wb") as fh:
        fh.write(out)
    os.replace(tmp, path)
    return "fixed", sorted(os.path.basename(p) for p in new)
