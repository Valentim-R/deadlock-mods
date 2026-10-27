"""Texturas .vtex_c do Source 2: lê só a mipmap pedida, já no formato comprimido que a GPU usa (BCn)."""
import struct

from . import kv3
from .vpk import block_list

BLOCK = {1: 8, 2: 16, 19: 16, 20: 16, 21: 16, 27: 8, 4: 4, 28: 4, 10: 8, 33: 1, 3: 1, 22: 2}
COMPRESSED = {1, 2, 19, 20, 21, 27}   # DXT1, DXT5, BC6H, BC7, ATI2N (BC5), ATI1N (BC4)


def mip_size(fmt, w, h):
    if fmt in COMPRESSED:
        w, h = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
        return (w * h >> 4) * BLOCK[fmt]
    return w * h * BLOCK.get(fmt, 4)


class Texture:
    """Cabeçalho da textura; `head` só precisa conter os blocos do recurso (os dados vêm depois)."""

    def __init__(self, head):
        _, off, sz = next(b for b in block_list(head) if b[0] == "DATA")
        p = off
        _ver, self.flags = struct.unpack_from("<HH", head, p)
        p += 4 + 16
        self.width, self.height, self.depth = struct.unpack_from("<HHH", head, p)
        p += 6
        self.format, self.mips = head[p], head[p + 1]
        p += 2 + 4
        xoff, xcount = struct.unpack_from("<II", head, p)
        self.comp_method, self.comp_sizes = 0, None
        q = p + xoff
        for i in range(xcount):
            typ, o, _s = struct.unpack_from("<III", head, q + i * 12)
            if typ == 4:   # COMPRESSED_MIP_SIZE
                base = q + i * 12 + 4 + o
                method, moff, n = struct.unpack_from("<III", head, base)
                self.comp_method = method
                self.comp_sizes = list(struct.unpack_from(f"<{n}i", head, base + 4 + moff))
        self.data_off = off + sz
        self.faces = 6 if self.flags & 4 else 1

    def size(self, mip):
        return max(1, self.width >> mip), max(1, self.height >> mip)

    def best_mip(self, max_size):
        lvl = 0
        while lvl < self.mips - 1 and max(self.size(lvl)) > max_size:
            lvl += 1
        return lvl

    def mip_range(self, level):
        """(offset no arquivo, bytes guardados, bytes descomprimidos) da mipmap `level`."""
        p = self.data_off
        for j in range(self.mips - 1, level, -1):
            s = mip_size(self.format, *self.size(j)) * self.faces
            if self.comp_sizes and self.comp_sizes[j] < s:
                s = self.comp_sizes[j]
            p += s
        want = mip_size(self.format, *self.size(level)) * self.faces
        stored = want
        if self.comp_sizes and self.comp_sizes[level] < want:
            stored = self.comp_sizes[level]
        return p, stored, want


def load_mip(read, path, max_size=1024):
    """read(path, start, length) -> (formato, largura, altura, bytes) da maior mip que cabe em max_size."""
    head = read(path, 0, 16384)
    t = Texture(head)
    lvl = t.best_mip(max_size)
    off, stored, want = t.mip_range(lvl)
    data = read(path, off, stored)
    if stored < want:
        data = bytes(kv3.lz4_block(data, bytearray(), want)) if t.comp_method == 1 else kv3.zstd_decompress(data)
    w, h = t.size(lvl)
    return t.format, w, h, data
