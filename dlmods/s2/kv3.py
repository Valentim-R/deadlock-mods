"""Leitor de KV3 binário (Source 2), versões 1–5. Baseado no ValveResourceFormat (BinaryKV3.cs)."""
import struct
import uuid

try:
    from compression import zstd as _zstd
except ImportError:          # Python < 3.14 (ex.: o do AppImage): mesmo módulo via backports.zstd
    try:
        from backports import zstd as _zstd
    except ImportError:
        _zstd = None


def lz4_block(src, out, size):
    """Descomprime um bloco LZ4 cru acrescentando em `out` (que pode já ter histórico)."""
    i, n = 0, len(src)
    end = len(out) + size
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n or len(out) >= end:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if off >= ml:
            out += out[start:start + ml]
        else:
            for k in range(ml):
                out.append(out[start + k])
    return out


def zstd_decompress(data):
    if _zstd is None:
        raise RuntimeError("zstd indisponível")
    return _zstd.decompress(data)


class Lanes:
    __slots__ = ("b1", "b2", "b4", "b8", "p1", "p2", "p4", "p8")

    def __init__(self, b1=b"", b2=b"", b4=b"", b8=b""):
        self.b1, self.b2, self.b4, self.b8 = b1, b2, b4, b8
        self.p1 = self.p2 = self.p4 = self.p8 = 0

    def u8(self):
        v = self.b1[self.p1]; self.p1 += 1; return v

    def s8(self):
        v = struct.unpack_from("<b", self.b1, self.p1)[0]; self.p1 += 1; return v

    def take2(self, fmt):
        v = struct.unpack_from(fmt, self.b2, self.p2)[0]; self.p2 += 2; return v

    def take4(self, fmt):
        v = struct.unpack_from(fmt, self.b4, self.p4)[0]; self.p4 += 4; return v

    def take8(self, fmt):
        v = struct.unpack_from(fmt, self.b8, self.p8)[0]; self.p8 += 8; return v

    def arr(self, lane, fmt, count, size):
        buf = getattr(self, "b" + lane)
        p = getattr(self, "p" + lane)
        v = list(struct.unpack_from(f"<{count}{fmt}", buf, p))
        setattr(self, "p" + lane, p + count * size)
        return v


def _align(o, a):
    return (o + a - 1) // a * a


class Reader:
    def __init__(self, data):
        self.d = memoryview(data)
        self.p = 0

    def u16(self):
        v = struct.unpack_from("<H", self.d, self.p)[0]; self.p += 2; return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v

    def read(self, n):
        v = bytes(self.d[self.p:self.p + n]); self.p += n; return v


def parse(data):
    r = Reader(data)
    magic = r.u32()
    if magic == 0x03564B56:
        raise NotImplementedError("VKV3 legado")
    ver = magic & 0xFF
    assert magic & 0xFFFFFF00 == 0x4B563300, hex(magic)
    fmt_guid = uuid.UUID(bytes_le=r.read(16))
    comp = r.u32()
    dict_id = frame = 0
    c1 = c4 = c8 = ctypes = cobj = carr = 0
    unc_total = comp_total = cblocks = blob_bytes = 0
    if ver == 1:
        c1, c4, c8, unc_total = r.i32(), r.i32(), r.i32(), r.i32()
        comp_total = len(data) - r.p
    else:
        dict_id, frame = r.u16(), r.u16()
        c1, c4, c8, ctypes = r.i32(), r.i32(), r.i32(), r.i32()
        cobj, carr = r.u16(), r.u16()
        unc_total, comp_total, cblocks, blob_bytes = r.i32(), r.i32(), r.i32(), r.i32()
    c2 = sizes_bytes = 0
    if ver >= 4:
        c2, sizes_bytes = r.i32(), r.i32()
    if ver >= 5:
        unc1, cmp1, unc2, cmp2 = r.i32(), r.i32(), r.i32(), r.i32()
        b2c1, b2c2, b2c4, b2c8 = r.i32(), r.i32(), r.i32(), r.i32()
        _nodes, cobj2, _carr2, _elems = r.i32(), r.i32(), r.i32(), r.i32()
    else:
        unc1, cmp1 = unc_total, comp_total

    def decomp(n_unc, n_cmp):
        if comp == 0:
            return r.read(n_unc)
        if comp == 1:
            return bytes(lz4_block(r.read(n_cmp), bytearray(), n_unc))
        return zstd_decompress(r.read(n_cmp))

    zstd_pre5 = ver < 5 and comp == 2
    buf1 = decomp(unc1, cmp1)
    zstd_rest = buf1[unc1:] if zstd_pre5 else b""
    buf1 = buf1[:unc1]

    off = 0
    lanes1 = {}
    if c1:
        lanes1["b1"] = buf1[off:off + c1]; off += c1
    if c2:
        off = _align(off, 2); lanes1["b2"] = buf1[off:off + c2 * 2]; off += c2 * 2
    if c4:
        off = _align(off, 4); lanes1["b4"] = buf1[off:off + c4 * 4]; off += c4 * 4
    if c8:
        off = _align(off, 8); lanes1["b8"] = buf1[off:off + c8 * 8]; off += c8 * 8
    elif ver < 5:
        off = _align(off, 8)
    L1 = Lanes(**lanes1)
    nstr = L1.take4("<i")
    strings = []
    blob_sizes_buf = b""
    if ver >= 5:
        for _ in range(nstr):
            e = L1.b1.index(b"\0", L1.p1)
            strings.append(L1.b1[L1.p1:e].decode("utf-8", "replace")); L1.p1 = e + 1
        buf2 = decomp(unc2, cmp2)
        o = cobj2 * 4
        objlens = buf2[:o]
        lanes2 = {}
        if b2c1:
            lanes2["b1"] = buf2[o:o + b2c1]; o += b2c1
        if b2c2:
            o = _align(o, 2); lanes2["b2"] = buf2[o:o + b2c2 * 2]; o += b2c2 * 2
        if b2c4:
            o = _align(o, 4); lanes2["b4"] = buf2[o:o + b2c4 * 4]; o += b2c4 * 4
        if b2c8:
            o = _align(o, 8); lanes2["b8"] = buf2[o:o + b2c8 * 8]; o += b2c8 * 8
        types = buf2[o:o + ctypes]; o += ctypes
        main, aux = Lanes(**lanes2), L1
        if cblocks:
            blob_sizes_buf = buf2[o:]
    else:
        start = off
        for _ in range(nstr):
            e = buf1.index(b"\0", off)
            strings.append(buf1[off:e].decode("utf-8", "replace")); off = e + 1
        tl = (unc_total - off - 4) if ver == 1 else (ctypes - off + start)
        types = buf1[off:off + tl]; off += tl
        objlens = None
        main = aux = L1
        if cblocks:
            blob_sizes_buf = buf1[off:]

    blobs = []
    if cblocks:
        lens = struct.unpack_from(f"<{cblocks}i", blob_sizes_buf, 0)
        bp = cblocks * 4 + 4
        if comp == 0:
            for n in lens:
                blobs.append(r.read(n))
        elif comp == 1:
            hist = bytearray()
            for n in lens:
                start = len(hist)
                while len(hist) - start < n:
                    clen = struct.unpack_from("<H", blob_sizes_buf, bp)[0]; bp += 2
                    lz4_block(r.read(clen), hist, min(frame, n - (len(hist) - start)))
                blobs.append(bytes(hist[start:start + n]))
        else:
            if ver >= 5:
                raw = zstd_decompress(r.read(comp_total - cmp1 - cmp2))
            else:
                raw = zstd_rest
            p = 0
            for n in lens:
                blobs.append(raw[p:p + n]); p += n

    st = {"types": types, "tp": 0, "objlens": objlens, "op": 0, "blob": 0}

    def rtype():
        b = types[st["tp"]]; st["tp"] += 1
        if ver >= 3:
            if b & 0x80:
                st["tp"] += 1
            if b & 0x40:
                st["tp"] += 1
            return b & 0x3F
        if b & 0x80:
            st["tp"] += 1
        return b & 0x7F

    def s(i):
        return strings[i] if 0 <= i < len(strings) else ""

    def packed(t, lane, n):
        if t == 2: return [x != 0 for x in lane.arr("1", "B", n, 1)]
        if t == 22: return lane.arr("1", "b", n, 1)
        if t == 23: return lane.arr("1", "B", n, 1)
        if t == 20: return lane.arr("2", "h", n, 2)
        if t == 21: return lane.arr("2", "H", n, 2)
        if t == 11: return lane.arr("4", "i", n, 4)
        if t == 12: return lane.arr("4", "I", n, 4)
        if t == 19: return lane.arr("4", "f", n, 4)
        if t == 3: return lane.arr("8", "q", n, 8)
        if t == 4: return lane.arr("8", "Q", n, 8)
        if t == 5: return lane.arr("8", "d", n, 8)
        return None

    def value(t, lane):
        if t == 1: return None
        if t == 13: return True
        if t == 14: return False
        if t in (15, 26): return 0
        if t in (16, 27): return 1
        if t == 17: return 0.0
        if t == 18: return 1.0
        if t == 2: return lane.u8() != 0
        if t == 22: return lane.s8()
        if t == 23: return lane.u8()
        if t == 20: return lane.take2("<h")
        if t == 21: return lane.take2("<H")
        if t == 11: return lane.take4("<i")
        if t == 12: return lane.take4("<I")
        if t == 19: return lane.take4("<f")
        if t == 3: return lane.take8("<q")
        if t == 4: return lane.take8("<Q")
        if t == 5: return lane.take8("<d")
        if t == 6: return s(main.take4("<i"))
        if t == 7:
            if ver < 2:
                n = main.take4("<i"); v = main.b1[main.p1:main.p1 + n]; main.p1 += n; return bytes(v)
            v = blobs[st["blob"]]; st["blob"] += 1; return v
        if t == 8:
            n = main.take4("<i")
            return [value(rtype(), main) for _ in range(n)]
        if t in (10, 24, 25):
            n = main.take4("<i") if t == 10 else main.u8()
            sub = rtype()
            el = aux if t == 25 else main
            pk = packed(sub, el, n)
            if pk is not None:
                return pk
            return [value(sub, el) for _ in range(n)]
        if t == 9:
            if ver >= 5:
                n = struct.unpack_from("<i", st["objlens"], st["op"])[0]; st["op"] += 4
            else:
                n = main.take4("<i")
            o = {}
            for _ in range(n):
                ct = rtype()
                k = s(main.take4("<i"))
                o[k] = value(ct, main)
            return o
        raise ValueError(f"tipo KV3 desconhecido {t}")

    return value(rtype(), main)
