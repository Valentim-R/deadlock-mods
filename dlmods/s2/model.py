"""Carrega um modelo de herói do Source 2 (Deadlock), posa com um clip e devolve malhas já deformadas."""
import re

import numpy as np

from . import kv3
from . import meshopt
from .vpk import block_list


def resource_kv(data, kind="DATA", index=0):
    bl = [b for b in block_list(data) if b[0] == kind]
    _, off, sz = bl[index]
    return kv3.parse(data[off:off + sz])


DUP_SUFFIX = re.compile(r"[._]0\d\d$")


# ---- quaternions (x, y, z, w) ----------------------------------------------
def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def qmat(q):
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def trs(t, q, s=1.0):
    m = np.eye(4)
    m[:3, :3] = qmat(np.asarray(q, float)) * s
    m[:3, 3] = t
    return m


# ---- clip -------------------------------------------------------------------
def decode_quat(u):
    rmin, rlen = -1 / np.sqrt(2), 2 / np.sqrt(2)
    a = (u[0] & 0x7FFF) * rlen / 0x7FFF + rmin
    b = (u[1] & 0x7FFF) * rlen / 0x7FFF + rmin
    c = u[2] * rlen / 0x7FFF + rmin
    w = np.sqrt(max(0.0, 1 - a * a - b * b - c * c))
    i = ((u[0] >> 14) & 2) | (u[1] >> 15)
    return [(w, a, b, c), (a, w, b, c), (a, b, w, c), (a, b, c, w)][i]


def clip_pose(clip, frame=0):
    """Lista de (t, q, s) em espaço do pai, um por osso do esqueleto NM."""
    data = clip.get("m_compressedPoseData") or b""
    words = np.frombuffer(data, "<u2") if data else np.zeros(0, np.uint16)
    offs = clip.get("m_compressedPoseOffsets") or [0]
    p = offs[frame if -len(offs) <= frame < len(offs) else -1] if len(words) else 0
    out = []
    for tr in clip["m_trackCompressionSettings"]:
        rx, ry, rz = tr["m_translationRangeX"], tr["m_translationRangeY"], tr["m_translationRangeZ"]
        start = np.array([rx["m_flRangeStart"], ry["m_flRangeStart"], rz["m_flRangeStart"]])
        length = np.array([rx["m_flRangeLength"], ry["m_flRangeLength"], rz["m_flRangeLength"]])
        q = np.array(tr["m_constantRotation"], float)
        t = start
        s = tr["m_scaleRange"]["m_flRangeStart"]
        if not tr["m_bIsRotationStatic"]:
            q = np.array(decode_quat([int(x) for x in words[p:p + 3]]), float); p += 3
        if not tr["m_bIsTranslationStatic"]:
            t = start + words[p:p + 3] / 65535.0 * length; p += 3
        if not tr["m_bIsScaleStatic"]:
            s = tr["m_scaleRange"]["m_flRangeStart"] + words[p] / 65535.0 * tr["m_scaleRange"]["m_flRangeLength"]; p += 1
        out.append((t, q, s))
    return out


def world(parents, locals_):
    w = [None] * len(parents)
    for i, par in enumerate(parents):
        m = trs(*locals_[i])
        w[i] = m if par < 0 else w[par] @ m
    return w


# ---- vértices ------------------------------------------------------------------
FORMATS = {   # DXGI -> (dtype numpy, componentes)
    2: ("<f4", 4), 3: ("<u4", 4), 4: ("<i4", 4), 6: ("<f4", 3), 7: ("<u4", 3), 8: ("<i4", 3), 10: ("<f2", 4), 11: ("<u2", 4), 12: ("<u2", 4), 13: ("<i2", 4), 14: ("<i2", 4),
    16: ("<f4", 2), 28: ("u1", 4), 30: ("u1", 4), 31: ("i1", 4), 32: ("i1", 4), 34: ("<f2", 2), 35: ("<u2", 2),
    36: ("<u2", 2), 37: ("<i2", 2), 38: ("<i2", 2), 41: ("<f4", 1), 42: ("<u4", 1), 54: ("<f2", 1), 61: ("u1", 1),
}


def read_attr(raw, layout, name, index=0):
    for f in layout:
        sem = f["m_pSemanticName"].upper()
        if sem == "BLENDWEIGHTS":
            sem = "BLENDWEIGHT"
        if sem == name and f["m_nSemanticIndex"] == index:
            dt, n = FORMATS[f["m_Format"]]
            size = np.dtype(dt).itemsize * n
            col = raw[:, f["m_nOffset"]:f["m_nOffset"] + size].copy()
            return col.view(dt).reshape(len(raw), n), f["m_Format"]
    return None, None


def decode_normals(packed):
    """Normal + tangente comprimidas (DXGI R32_UINT, 'CompressedTangentFrame'). Porte do VRF."""
    p = packed.astype(np.uint32).ravel()
    sign = (p & 1).astype(np.float64)
    tb = ((p >> 1) & 0x7FF).astype(np.float64)
    nx = ((p >> 12) & 0x3FF) / 1023.0 * 2 - 1
    ny = ((p >> 22) & 0x3FF) / 1023.0 * 2 - 1
    nz = 1 - np.abs(nx) - np.abs(ny)
    c = np.clip(-nz, 0, 1)
    nx = nx + np.where(nx >= 0, -c, c)
    ny = ny + np.where(ny >= 0, -c, c)
    n = np.stack([nx, ny, nz], 1)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    ts = np.where(n[:, 2] >= 0, 1.0, -1.0)
    rcp = 1.0 / (ts + n[:, 2])
    ut = np.stack([-ts * n[:, 0] * n[:, 0] * rcp + 1, -ts * n[:, 0] * n[:, 1] * rcp, -ts * n[:, 0]], 1)
    ang = tb / 2047.0 * 2 * np.pi
    t = ut * np.cos(ang)[:, None] + np.cross(n, ut) * np.sin(ang)[:, None]
    return n, np.concatenate([t, np.where(sign == 0, -1.0, 1.0)[:, None]], 1)


class Model:
    def __init__(self, vmdl_bytes, nmskel_bytes):
        self.raw = vmdl_bytes
        self.blocks = block_list(vmdl_bytes)
        self.data = resource_kv(vmdl_bytes)
        self.ctrl = resource_kv(vmdl_bytes, "CTRL")
        sk = resource_kv(nmskel_bytes)
        self.nm_names = sk["m_boneIDs"]
        self.nm_parents = sk["m_parentIndices"]
        self.nm_ref = [(np.array(r[:3]), np.array(r[4:8]), r[3]) for r in sk["m_parentSpaceReferencePose"]]
        ms = self.data["m_modelSkeleton"]
        self.bone_names = ms["m_boneName"]
        self.bone_parents = ms["m_nParent"]

    def block(self, i):
        _, off, sz = self.blocks[i]
        return self.raw[off:off + sz]

    def skin_matrices(self, pose, deltas=None):
        if deltas:   # sequência antiga: já vem por osso do modelo (inclui twist/cloth que o NM não tem)
            self.posed = {n: (deltas[n] @ w)[:3, 3] for n, w in zip(self.nm_names, world(self.nm_parents, self.nm_ref))
                          if n in deltas}
            mats = []
            for i, name in enumerate(self.bone_names):
                j = i   # cópias do Blender ("pelvis_001") seguem o original; o resto sobe até um osso animado
                while j >= 0 and self.bone_names[j] not in deltas and DUP_SUFFIX.sub("", self.bone_names[j]) not in deltas:
                    j = self.bone_parents[j]
                if j < 0:
                    mats.append(np.eye(4))
                else:
                    n = self.bone_names[j]
                    mats.append(deltas[n] if n in deltas else deltas[DUP_SUFFIX.sub("", n)])
            return np.array(mats)
        pose = list(pose) + self.nm_ref[len(pose):]
        ref_w = world(self.nm_parents, self.nm_ref)
        pose_w = world(self.nm_parents, pose)
        self.posed = {n: pose_w[i][:3, 3] for i, n in enumerate(self.nm_names)}
        nm = {n: pose_w[i] @ np.linalg.inv(ref_w[i]) for i, n in enumerate(self.nm_names)}
        mats = []
        for i, name in enumerate(self.bone_names):
            # cópias de osso feitas no Blender ("pelvis_001", "head.001") seguem o osso original;
            # o resto sobe na hierarquia até achar um osso animado
            j = i
            while j >= 0:
                base = DUP_SUFFIX.sub("", self.bone_names[j])
                if self.bone_names[j] in nm or base in nm:
                    break
                j = self.bone_parents[j]
            if j < 0:
                mats.append(np.eye(4))
            else:
                n = self.bone_names[j]
                mats.append(nm[n] if n in nm else nm[DUP_SUFFIX.sub("", n)])
        return np.array(mats)

    def meshes(self, lod=0, group_mask=None):
        lods = self.data["m_refLODGroupMasks"]
        groups = self.data["m_refMeshGroupMasks"]
        if group_mask is None:
            group_mask = self.data["m_nDefaultMeshGroupMask"]
        for em in self.ctrl["embedded_meshes"]:
            i = em["m_nMeshIndex"]
            if not (lods[i] & (1 << lod)) or not (groups[i] & group_mask):
                continue
            yield i, em

    @staticmethod
    def _vertices(raw, layout, mats, remap):
        pos = read_attr(raw, layout, "POSITION")[0][:, :3].astype(np.float64)
        uv_raw, uvfmt = read_attr(raw, layout, "TEXCOORD")
        uv = uv_raw[:, :2].astype(np.float32) / {35: 65535.0, 37: 32767.0, 28: 255.0}.get(uvfmt, 1.0)
        nrm_raw, nfmt = read_attr(raw, layout, "NORMAL")
        if nfmt == 42:
            nrm, tan = decode_normals(nrm_raw)
        elif nrm_raw is not None:
            nrm = nrm_raw[:, :3].astype(np.float64)
            t_raw, _ = read_attr(raw, layout, "TANGENT")
            tan = t_raw[:, :4].astype(np.float64) if t_raw is not None else np.tile([1.0, 0, 0, 1], (len(pos), 1))
        else:
            nrm, tan = np.tile([0, 0, 1.0], (len(pos), 1)), np.tile([1.0, 0, 0, 1], (len(pos), 1))
        col_raw, cfmt = read_attr(raw, layout, "COLOR")   # cor por vértice (ex.: cabelo do Doorman)
        if col_raw is None:
            col = np.ones((len(pos), 4), np.float32)
        else:
            col = col_raw[:, :4].astype(np.float32) / {28: 255.0, 11: 65535.0}.get(cfmt, 1.0)
            if col.shape[1] < 4:
                col = np.concatenate([col, np.ones((len(col), 4 - col.shape[1]), np.float32)], 1)
        if mats is None:
            return pos, nrm, tan, uv, col
        idxs, wts = [], []
        for k in range(2):
            bi, bfmt = read_attr(raw, layout, "BLENDINDICES", k)
            if bi is not None:
                if bfmt == 12:      # R16G16B16A16_UINT = 8 ossos em bytes
                    bi = bi.view(np.uint8).reshape(len(raw), 8)
                elif bfmt == 4:     # R32G32B32A32_SINT = 8 ossos em uint16 (modelos com mais de 256 ossos)
                    bi = bi.view(np.uint16).reshape(len(raw), 8)
                elif bfmt == 38:    # R16G16_SINT = 2 ossos (VRF repete o segundo)
                    bi = bi[:, [0, 1, 1, 1]]
                idxs.append(bi.astype(int))
            bw, wfmt = read_attr(raw, layout, "BLENDWEIGHT", k)
            if bw is not None:
                if wfmt == 11 and idxs and idxs[-1].shape[1] == 8:
                    bw = bw.view(np.uint8).reshape(len(raw), 8)
                    wfmt = 28
                scale = {28: 255.0, 11: 65535.0, 35: 65535.0}.get(wfmt, 1.0)
                wts.append(bw.astype(np.float64) / scale)
        if not idxs:
            return pos, nrm, tan, uv, col
        bi = np.concatenate(idxs, 1)
        if wts:
            w = np.concatenate(wts, 1)[:, :bi.shape[1]]
            if w.shape[1] < bi.shape[1]:
                w = np.concatenate([w, np.zeros((len(w), bi.shape[1] - w.shape[1]))], 1)
        else:
            w = np.zeros(bi.shape); w[:, 0] = 1
        w /= w.sum(1, keepdims=True) + 1e-9
        rt = np.array(remap, int)
        bones = rt[np.clip(bi, 0, len(rt) - 1)]
        M = np.einsum("vk,vkij->vij", w, mats[np.clip(bones, 0, len(mats) - 1)])
        ph = np.concatenate([pos, np.ones((len(pos), 1))], 1)
        pos = np.einsum("vij,vj->vi", M, ph)[:, :3]
        nrm = np.einsum("vij,vj->vi", M[:, :3, :3], nrm)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-9
        t3 = np.einsum("vij,vj->vi", M[:, :3, :3], tan[:, :3])
        tan = np.concatenate([t3 / (np.linalg.norm(t3, axis=1, keepdims=True) + 1e-9), tan[:, 3:]], 1)
        return pos, nrm, tan, uv, col

    def group_mask(self, bodygroups):
        """Máscara de malhas com os bodygroups da cena (ex.: head = 1 -> cabeça de UI)."""
        mask = self.data["m_nDefaultMeshGroupMask"]
        groups = self.data.get("m_meshGroups") or []
        for key, val in (bodygroups or {}).items():
            bits = {}
            for i, g in enumerate(groups):
                mm = re.match(r"(.+)_@(\d+)_#", g)
                if mm and mm.group(1).lower() == key.lower():
                    bits[int(mm.group(2))] = i
            if val in bits:
                for i in bits.values():
                    mask &= ~(1 << i)
                mask |= 1 << bits[val]
        return mask

    def nm_pose(self, deltas):
        """Deltas da sequência antiga ({osso: 4x4}) -> lista (t, q, s) no espaço do pai, na ordem do NM."""
        ref_w = world(self.nm_parents, self.nm_ref)
        out, w = [], [None] * len(self.nm_names)
        for i, n in enumerate(self.nm_names):
            par = self.nm_parents[i]
            if n in deltas:
                w[i] = deltas[n] @ ref_w[i]
            else:   # osso que a sequência não tem: segue o pai com a pose de referência
                w[i] = trs(*self.nm_ref[i]) if par < 0 else w[par] @ trs(*self.nm_ref[i])
            local = w[i] if par < 0 else np.linalg.inv(w[par]) @ w[i]
            out.append(mat_tqs(local))
        return out

    def build(self, pose=None, lod=0, group_mask=None, deltas=None):
        """Lista de draws: dict(pos, normal, uv, idx, material)."""
        mats = self.skin_matrices(pose, deltas) if (pose is not None or deltas) else None
        remap, starts = self.data["m_remappingTable"], self.data["m_remappingTableStarts"]
        out = []
        cache = {}
        for i, em in self.meshes(lod, group_mask):
            mdat = kv3.parse(self.block(em["m_nDataBlock"]))
            vbs = []
            for vb in em["m_vertexBuffers"]:
                b = self.block(vb["m_nBlockIndex"])
                if vb.get("m_bCompressedZSTD"):
                    b = kv3.zstd_decompress(b)
                if vb["m_bMeshoptCompressed"]:
                    raw = meshopt.decode_vertex_buffer(vb["m_nElementCount"], vb["m_nElementSizeInBytes"], b)
                else:
                    raw = np.frombuffer(b, np.uint8).reshape(vb["m_nElementCount"], vb["m_nElementSizeInBytes"])
                vbs.append((raw, vb["m_inputLayoutFields"]))
            ibs = []
            for ib in em["m_indexBuffers"]:
                b = self.block(ib["m_nBlockIndex"])
                if ib.get("m_bCompressedZSTD"):
                    b = kv3.zstd_decompress(b)
                if ib["m_bMeshoptCompressed"]:
                    ibs.append(meshopt.decode_index_buffer(ib["m_nElementCount"], b))
                else:
                    ibs.append(np.frombuffer(b, "<u2" if ib["m_nElementSizeInBytes"] == 2 else "<u4").astype(np.uint32))
            for so in mdat["m_sceneObjects"]:
                for dc in so["m_drawCalls"]:
                    hb = dc["m_vertexBuffers"][0]["m_hBuffer"]
                    raw, layout = vbs[hb]
                    key = (i, hb)
                    if key not in cache:
                        cache[key] = self._vertices(raw, layout, mats, remap[starts[i]:] if starts else remap)
                    pos, nrm, tan, uv, col = cache[key]
                    ib = ibs[dc["m_indexBuffer"]["m_hBuffer"]]
                    idx = ib[dc["m_nStartIndex"]:dc["m_nStartIndex"] + dc["m_nIndexCount"]] + dc["m_nBaseVertex"]
                    out.append({"pos": pos, "normal": nrm, "tangent": tan, "uv": uv, "color": col, "idx": idx,
                                "material": dc["m_material"]})
        return out


# ---- sequências antigas embutidas no vmdl (ASEQ/ANIM/AGRP) ----------------------
def _legacy_quat(b):
    """Quaternion comprimido em 6 bytes (formato da Source 2 antiga, igual ao VRF)."""
    c = np.sin(np.pi / 4) / 16384.0
    i = [b[0] + ((b[1] & 63) << 8), b[2] + ((b[3] & 63) << 8), b[4] + ((b[5] & 63) << 8)]
    x, y, z = (c * (v - 16384) if (b[k * 2 + 1] & 64) == 0 else c * v for k, v in enumerate(i))
    w = np.sqrt(max(0.0, 1 - x * x - y * y - z * z))
    if b[5] & 128:
        w = -w
    if b[1] & 128:
        return (y, z, w, x) if b[3] & 128 else (z, w, x, y)
    return (w, x, y, z) if b[3] & 128 else (x, y, z, w)


def _seg_values(dec, data, n, f=0):
    """Valores do frame f (local ao bloco) de um segmento: lista com n itens (vetor/quat/float)."""
    f32, f16 = np.frombuffer, lambda d, k: np.frombuffer(d[:k * 2], "<f2").astype(np.float64)
    # decoders animados guardam um bloco de n elementos por frame; os estáticos só um
    step = {"CCompressedAnimVector3": 6, "CCompressedAnimQuaternion": 6, "CCompressedFullQuaternion": 16,
            "CCompressedFullVector3": 12, "CCompressedFullFloat": 4}.get(dec)
    if step and f:
        data = data[f * n * step:]
    elif dec == "CCompressedDeltaVector3" and f:
        data = data[:n * 12] + data[n * 12 + f * n * 6:]
    if dec in ("CCompressedStaticFullVector3", "CCompressedFullVector3"):
        return f32(data[:n * 12], "<f4").reshape(n, 3).astype(np.float64)
    if dec in ("CCompressedStaticVector3", "CCompressedAnimVector3"):
        return f16(data, n * 3).reshape(n, 3)
    if dec == "CCompressedDeltaVector3":
        base = f32(data[:n * 12], "<f4").reshape(n, 3).astype(np.float64)
        return base + f16(data[n * 12:], n * 3).reshape(n, 3)
    if dec in ("CCompressedStaticQuaternion", "CCompressedAnimQuaternion"):
        return [_legacy_quat(data[k * 6:k * 6 + 6]) for k in range(n)]
    if dec == "CCompressedFullQuaternion":
        return f32(data[:n * 16], "<f4").reshape(n, 4).astype(np.float64)
    if dec in ("CCompressedStaticFloat", "CCompressedFullFloat"):
        return f32(data[:n * 4], "<f4").astype(np.float64)
    return None


def sequence_names(vmdl_bytes):
    try:
        return [s.lower() for s in resource_kv(vmdl_bytes, "ASEQ")["m_localSequenceNameArray"]]
    except (IndexError, KeyError):
        return []


def legacy_pose(vmdl_bytes, seq, frame=-1):
    """Um frame (padrão: o último) de uma sequência embutida -> {osso: pose_mundo @ inv(bind_mundo)}, ou None.
    O esqueleto antigo usa outra convenção de eixos por osso (e raiz girada), então a pose sai em
    "deltas" no espaço do modelo, que independem disso."""
    try:
        A = resource_kv(vmdl_bytes, "ANIM")
        Q = resource_kv(vmdl_bytes, "ASEQ")
        dk = resource_kv(vmdl_bytes, "AGRP")["m_decodeKey"]
    except (IndexError, KeyError):
        return None
    seq = seq.lower()
    names = [a["m_name"].lower() for a in A["m_animArray"]]
    if seq in names:
        anim = A["m_animArray"][names.index(seq)]
    else:
        sq = [s.lower() for s in Q["m_localSequenceNameArray"]]
        if seq not in sq:
            return None
        ref = Q["m_localS1SeqDescArray"][sq.index(seq)]["m_fetch"]["m_localReferenceArray"]
        if not ref or not 0 < ref[0] <= len(names):
            return None
        anim = A["m_animArray"][ref[0] - 1]   # a referência conta a partir de 1
    decoders = [d["m_szName"] for d in A["m_decoderArray"]]
    bones = dk["m_boneArray"]
    pose = {b["m_name"]: [np.array(b["m_pos"], float), np.array(b["m_quat"], float), float(b.get("m_scale", 1.0))]
            for b in bones}
    chans = dk["m_dataChannelArray"]
    # os ids no segmento são elementos globais; o canal diz qual osso cada um é
    local = [{e: k for k, e in enumerate(ch["m_nElementIndexArray"])} for ch in chans]
    blocks = anim["m_pData"]["m_frameblockArray"]
    if not blocks:
        return None
    nf = max(1, int(anim["m_pData"].get("m_nFrames", 1)))
    frame = frame % nf   # -1 = último frame (a cena toca a animação uma vez e para no fim)
    blk = next((b for b in blocks if b["m_nStartFrame"] <= frame <= b["m_nEndFrame"]), blocks[-1])
    lf = min(frame, blk["m_nEndFrame"]) - blk["m_nStartFrame"]
    for si in blk["m_segmentIndexArray"]:
        seg = A["m_segmentArray"][si]
        c = bytes(seg["m_container"])
        dec_i, _card, nb, _tot = np.frombuffer(c[:8], "<i2")
        ids = np.frombuffer(c[8:8 + nb * 2], "<i2")
        vals = _seg_values(decoders[dec_i], c[8 + nb * 2:], int(nb), lf)
        if vals is None:
            continue
        ch = chans[seg["m_nLocalChannel"]]
        attr, enames, emap = ch["m_szVariableName"], ch["m_szElementNameArray"], local[seg["m_nLocalChannel"]]
        for k, e in enumerate(ids):
            idx = emap.get(int(e))
            if idx is None or idx >= len(enames):
                continue
            p = pose.get(enames[idx])
            if p is None:
                continue
            if attr == "Position":
                p[0] = np.asarray(vals[k], float)
            elif attr == "Angle":
                p[1] = np.asarray(vals[k], float)
            elif attr == "Scale":
                p[2] = float(np.asarray(vals[k]).ravel()[0])
    names = [b["m_name"] for b in bones]
    parents = [b["m_parent"] for b in bones]
    bind_w = world(parents, [(np.array(b["m_pos"], float), np.array(b["m_quat"], float), float(b.get("m_scale", 1.0)))
                             for b in bones])
    pose_w = world(parents, [pose[n] for n in names])
    return {n: pose_w[i] @ np.linalg.inv(bind_w[i]) for i, n in enumerate(names)}


def mat_tqs(mat):
    """4x4 -> (t, q xyzw, escala uniforme)."""
    s = float(np.linalg.norm(mat[:3, 0])) or 1.0
    r = mat[:3, :3] / s
    tr = np.trace(r)
    if tr > 0:
        k = np.sqrt(tr + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / k, (r[0, 2] - r[2, 0]) / k, (r[1, 0] - r[0, 1]) / k, 0.25 * k]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        k = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * k, (r[0, 1] + r[1, 0]) / k, (r[0, 2] + r[2, 0]) / k, (r[2, 1] - r[1, 2]) / k]
    elif r[1, 1] > r[2, 2]:
        k = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / k, 0.25 * k, (r[1, 2] + r[2, 1]) / k, (r[0, 2] - r[2, 0]) / k]
    else:
        k = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / k, (r[1, 2] + r[2, 1]) / k, 0.25 * k, (r[1, 0] - r[0, 1]) / k]
    return mat[:3, 3].copy(), np.array(q), s
