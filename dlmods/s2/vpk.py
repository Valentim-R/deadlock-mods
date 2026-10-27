"""Leitor de pacotes VPK (v1/v2) e da lista de blocos dos recursos compilados do Source 2."""
import struct


class VPK:
    def __init__(self, path):
        self.path = str(path)
        with open(self.path, "rb") as f:
            sig, ver, tree = struct.unpack("<III", f.read(12))
            if sig != 0x55AA1234:
                raise ValueError(f"não é um VPK: {self.path}")
            hdr = 12 + (16 if ver == 2 else 0)
            f.seek(hdr)
            raw = f.read(tree)
        self.data_start = hdr + tree
        self.files = {}
        p = 0

        def cstr():
            nonlocal p
            e = raw.index(b"\0", p)
            s = raw[p:e].decode("utf-8", "replace")
            p = e + 1
            return s
        while ext := cstr():
            while d := cstr():
                while n := cstr():
                    crc, pre, arc, off, ln, _ = struct.unpack_from("<IHHIIH", raw, p)
                    p += 18
                    pdata = raw[p:p + pre]
                    p += pre
                    name = f"{d}/{n}.{ext}" if d.strip() else f"{n}.{ext}"
                    self.files[name.lower()] = (arc, off, ln, pdata)

    def _archive(self, arc):
        return self.path if arc == 0x7FFF else self.path.replace("_dir.vpk", f"_{arc:03d}.vpk")

    def size(self, name):
        arc, off, ln, pdata = self.files[name.lower()]
        return len(pdata) + ln

    def read(self, name, start=0, length=None):
        """Lê o arquivo inteiro, ou só [start, start+length)."""
        arc, off, ln, pdata = self.files[name.lower()]
        total = len(pdata) + ln
        if length is None:
            length = total - start
        length = max(0, min(length, total - start))
        out = bytearray()
        if start < len(pdata):
            take = pdata[start:start + length]
            out += take
            start, length = 0, length - len(take)
        else:
            start -= len(pdata)
        if length > 0 and ln:
            base = self.data_start + off if arc == 0x7FFF else off
            with open(self._archive(arc), "rb") as f:
                f.seek(base + start)
                out += f.read(length)
        return bytes(out)


def block_list(data):
    """[(tipo, offset absoluto, tamanho)] dos blocos de um recurso compilado (_c)."""
    size, hv, ver, boff, bcount = struct.unpack_from("<IHHII", data, 0)
    out = []
    for i in range(bcount):
        p = 8 + boff + i * 12
        off, sz = struct.unpack_from("<II", data, p + 4)
        out.append((data[p:p + 4].decode("ascii", "replace"), p + 4 + off, sz))
    return out
