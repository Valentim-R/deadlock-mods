"""Decodificadores de vértices/índices do meshoptimizer (codec v0/v1). Porte do ValveResourceFormat/meshoptimizer."""
import numpy as np

_BITS_V0 = (0, 2, 4, 8)
_BITS_V1 = (0, 1, 2, 4, 8)


def _group(data, p, bits, out, o):
    """Decodifica um grupo de 16 bytes; devolve a nova posição em data."""
    if bits == 0:
        out[o:o + 16] = 0
        return p
    if bits == 8:
        out[o:o + 16] = np.frombuffer(data, np.uint8, 16, p)
        return p + 16
    hdr = 16 * bits // 8
    q = p + hdr
    sentinel = (1 << bits) - 1
    per = 8 // bits
    i = 0
    for hb in range(hdr):
        b = data[p + hb]
        for k in range(per):
            if bits == 1:
                v = (b >> k) & 1
            else:
                v = (b >> (8 - bits * (k + 1))) & sentinel
            if v == sentinel:
                v = data[q]
                q += 1
            out[o + i] = v
            i += 1
    return q


def _decode_bytes(data, p, out, n_aligned, bits_table):
    groups = n_aligned // 16
    hsize = (groups + 3) // 4
    header = data[p:p + hsize]
    p += hsize
    for g in range(groups):
        bk = (header[g // 4] >> ((g % 4) * 2)) & 3
        p = _group(data, p, bits_table[bk], out, g * 16)
    return p


def decode_vertex_buffer(count, size, data):
    data = bytes(data)
    assert data[0] & 0xF0 == 0xA0, hex(data[0])
    ver = data[0] & 0x0F
    tail = size + (0 if ver == 0 else size // 4)
    last = np.frombuffer(data, np.uint8, size, len(data) - tail).copy()
    channels = None if ver == 0 else data[len(data) - tail + size:len(data) - tail + size + size // 4]
    block = min((8192 // size) & ~15, 256)
    result = np.empty((count, size), np.uint8)
    p = 1
    voff = 0
    buf = np.zeros(256 * 4 + 16, np.uint8)
    while voff < count:
        n = min(block, count - voff)
        na = (n + 15) & ~15
        csize = 0 if ver == 0 else size // 4
        control = data[p:p + csize]
        p += csize
        blockout = result[voff:voff + n]
        for k in range(0, size, 4):
            cb = 0 if ver == 0 else control[k // 4]
            for j in range(4):
                ctrl = (cb >> (j * 2)) & 3
                seg = buf[j * n:j * n + na] if j * n + na <= len(buf) else None
                if ctrl == 3:
                    buf[j * n:j * n + n] = np.frombuffer(data, np.uint8, n, p)
                    p += n
                elif ctrl == 2:
                    buf[j * n:j * n + n] = 0
                else:
                    tmp = np.zeros(na, np.uint8)
                    p = _decode_bytes(data, p, tmp, na, _BITS_V0 if ver == 0 else _BITS_V1[ctrl:])
                    buf[j * n:j * n + n] = tmp[:n]
            ch = 0 if ver == 0 else channels[k // 4]
            planes = buf[:4 * n].reshape(4, n)
            mode = ch & 3
            if mode == 0:       # 4 canais de 1 byte, delta zigzag
                for j in range(4):
                    v = planes[j].astype(np.int32)
                    d = ((-(v & 1)) ^ (v >> 1)) & 0xFF
                    blockout[:, k + j] = ((np.cumsum(d) + last[k + j]) & 0xFF).astype(np.uint8)
            elif mode == 1:     # 2 canais de 2 bytes
                for j in range(0, 4, 2):
                    v = planes[j].astype(np.int32) | (planes[j + 1].astype(np.int32) << 8)
                    d = ((-(v & 1)) ^ (v >> 1)) & 0xFFFF
                    pv = int(last[k + j]) | (int(last[k + j + 1]) << 8)
                    r = (np.cumsum(d) + pv) & 0xFFFF
                    blockout[:, k + j] = (r & 0xFF).astype(np.uint8)
                    blockout[:, k + j + 1] = (r >> 8).astype(np.uint8)
            elif mode == 2:     # 1 canal de 4 bytes, xor + rotação
                rot = (32 - (ch >> 4)) & 31
                v = (planes[0].astype(np.uint32) | (planes[1].astype(np.uint32) << 8)
                     | (planes[2].astype(np.uint32) << 16) | (planes[3].astype(np.uint32) << 24))
                if rot:
                    v = ((v << np.uint32(rot)) | (v >> np.uint32(32 - rot))) & np.uint32(0xFFFFFFFF)
                pv = np.uint32(int.from_bytes(bytes(last[k:k + 4]), "little"))
                r = np.bitwise_xor.accumulate(v) ^ pv
                blockout[:, k:k + 4] = r.astype("<u4").view(np.uint8).reshape(n, 4)
            else:
                raise ValueError("canal inválido")
        last = blockout[n - 1].copy()
        voff += n
    return result


def _vbyte(data, p):
    lead = data[p]; p += 1
    if lead < 128:
        return lead, p
    r, sh = lead & 127, 7
    for _ in range(4):
        g = data[p]; p += 1
        r |= (g & 127) << sh
        sh += 7
        if g < 128:
            break
    return r, p


def decode_index_buffer(count, data):
    data = bytes(data)
    assert data[0] & 0xF0 == 0xE0, hex(data[0])
    ver = data[0] & 0x0F
    doff = 1 + count // 3
    code = data[1:doff]
    d = data[doff:]
    safe_end = len(d) - 16
    aux = d[safe_end:]
    ef = [(0, 0)] * 16
    vf = [0] * 16
    eo = vo = 0
    nxt = last = 0
    fecmax = 13 if ver >= 1 else 15
    pos = 0
    out = []
    M = 0xFFFFFFFF

    def dec(last, pos):
        v, pos = _vbyte(d, pos)
        return (last + ((v >> 1) ^ (-(v & 1)))) & M, pos

    for ct in code:
        if ct < 0xF0:
            fe = ct >> 4
            a, b = ef[(eo - 1 - fe) & 15]
            fec = ct & 15
            if fec < fecmax:
                if fec == 0:
                    c = nxt; nxt += 1
                    vf[vo] = c; vo = (vo + 1) & 15
                else:
                    c = vf[(vo - 1 - fec) & 15]
                    vf[vo] = c
            else:
                if fec != 15:
                    c = last = (last + fec * 2 - 27) & M
                else:
                    c, pos = dec(last, pos); last = c
                vf[vo] = c; vo = (vo + 1) & 15
            ef[eo] = (c, b); eo = (eo + 1) & 15
            ef[eo] = (a, c); eo = (eo + 1) & 15
            out += (a, b, c)
        elif ct < 0xFE:
            ca = aux[ct & 15]
            feb, fec = ca >> 4, ca & 15
            a = nxt; nxt += 1
            b = nxt if feb == 0 else vf[(vo - feb) & 15]
            if feb == 0: nxt += 1
            c = nxt if fec == 0 else vf[(vo - fec) & 15]
            if fec == 0: nxt += 1
            out += (a, b, c)
            vf[vo] = a; vo = (vo + 1) & 15
            vf[vo] = b; vo = (vo + (1 if feb == 0 else 0)) & 15
            vf[vo] = c; vo = (vo + (1 if fec == 0 else 0)) & 15
            ef[eo] = (b, a); eo = (eo + 1) & 15
            ef[eo] = (c, b); eo = (eo + 1) & 15
            ef[eo] = (a, c); eo = (eo + 1) & 15
        else:
            ca = d[pos]; pos += 1
            fea = 0 if ct == 0xFE else 15
            feb, fec = ca >> 4, ca & 15
            if ca == 0:
                nxt = 0
            if fea == 0:
                a = nxt; nxt += 1
            else:
                a = 0
            if feb == 0:
                b = nxt; nxt += 1
            else:
                b = vf[(vo - feb) & 15]
            if fec == 0:
                c = nxt; nxt += 1
            else:
                c = vf[(vo - fec) & 15]
            if fea == 15:
                a, pos = dec(last, pos); last = a
            if feb == 15:
                b, pos = dec(last, pos); last = b
            if fec == 15:
                c, pos = dec(last, pos); last = c
            out += (a, b, c)
            vf[vo] = a; vo = (vo + 1) & 15
            vf[vo] = b; vo = (vo + (1 if feb in (0, 15) else 0)) & 15
            vf[vo] = c; vo = (vo + (1 if fec in (0, 15) else 0)) & 15
            ef[eo] = (b, a); eo = (eo + 1) & 15
            ef[eo] = (c, b); eo = (eo + 1) & 15
            ef[eo] = (a, c); eo = (eo + 1) & 15
    assert pos == safe_end, (pos, safe_end)
    return np.array(out, np.uint32)
