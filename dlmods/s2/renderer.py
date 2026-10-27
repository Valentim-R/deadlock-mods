"""Renderizador OpenGL fora da tela, imitando a iluminação NPR dos heróis do Deadlock."""
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QMatrix4x4, QOffscreenSurface, QOpenGLContext, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader, QOpenGLShaderProgram,
    QOpenGLTexture, QOpenGLVertexArrayObject,
)

from . import model as s2model
from . import vtex

GL_FLOAT, GL_TRIANGLES = 0x1406, 0x0004
GL_DEPTH_TEST, GL_CULL_FACE, GL_BLEND = 0x0B71, 0x0B44, 0x0BE2
GL_FRONT, GL_BACK = 0x0404, 0x0405
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_MULTISAMPLE = 0x809D

VERT = """
#version 330
in vec3 aPos; in vec3 aNormal; in vec4 aTangent; in vec2 aUv; in vec4 aColor;
uniform mat4 uMvp; uniform float uExtrude;
out vec3 vPos; out vec3 vNormal; out vec4 vTangent; out vec2 vUv; out vec4 vColor;
void main() {
    vec3 p = aPos + aNormal * uExtrude;
    vPos = aPos; vNormal = aNormal; vTangent = aTangent; vUv = aUv; vColor = aColor;
    gl_Position = uMvp * vec4(p, 1.0);
}
"""

FRAG = """
#version 330
in vec3 vPos; in vec3 vNormal; in vec4 vTangent; in vec2 vUv; in vec4 vColor;
uniform int uVertexColor;
uniform sampler2D tColor, tNormal, tAo, tRim, tSss, tIllum;
uniform vec3 uTint, uEye, uOutline;
uniform int uOutlinePass, uAlphaTest, uNumLights;
uniform float uOpacity, uSelfIllum;
uniform int uConstAlpha;
uniform vec3 uLightPos[8];
uniform vec3 uLightColor[8];
uniform vec3 uAmbient;
out vec4 frag;

float gain(float x, float k) {
    bool up = x > 0.5; float f = up ? 1.0 - x : x;
    float c = pow(0.5, 1.0 - k) * pow(f, k);
    return up ? 1.0 - c : c;
}
float toon(float ndl, float wrap, float sharp) {
    float w = clamp(0.5 + (((wrap - 0.5) + ndl) - 0.5) * 2.0, 0.0, 1.0);
    return gain(w, 1.0 / (1.0 - sharp));
}
vec3 hemiOct(vec2 e) {
    vec2 t = vec2(e.x + e.y - 1.003922, e.x - e.y);
    vec3 v = normalize(vec3(t, 1.0 - abs(t.x) - abs(t.y)));
    v.y = -v.y;
    return v;
}
void main() {
    vec4 col = texture(tColor, vUv);
    if (uAlphaTest == 1 && col.a < 0.5) discard;
    if (uOutlinePass == 1) { frag = vec4(uOutline, 1.0); return; }
    vec3 N = normalize(vNormal);
    if (!gl_FrontFacing) N = -N;
    vec3 T = normalize(vTangent.xyz - N * dot(vTangent.xyz, N));
    vec3 B = cross(N, T) * vTangent.w;
    vec4 nr = texture(tNormal, vUv);
    N = normalize(mat3(T, B, N) * hemiOct(nr.rg));
    float ao = texture(tAo, vUv).r;
    vec2 tr = texture(tRim, vUv).rg;
    vec3 albedo = col.rgb * mix(vec3(1.0), uTint, tr.x);
    if (uVertexColor == 1) albedo *= vColor.rgb;
    vec3 sss = texture(tSss, vUv).rgb;
    vec3 V = normalize(uEye - vPos);

    vec3 shadeTint = mix(vec3(1.0), sss, 0.65);
    vec3 H0 = normalize(V + vec3(0.0, 0.0, 0.3));
    vec3 light = vec3(0.0);
    vec3 rimc = vec3(0.0);
    float rough = clamp(nr.b, 0.05, 1.0);
    float spec = 0.0;
    for (int i = 0; i < uNumLights; i++) {
        vec3 L = normalize(uLightPos[i] - vPos);
        float ndl = dot(N, L);
        float d = toon(ndl, 0.5, 0.6);
        light += uLightColor[i] * mix(shadeTint * 0.15, vec3(1.0), d);
        // rim: luz vindo de trás em relação à câmera
        float back = clamp(-dot(L, V) * 0.5 + 0.5, 0.0, 1.0);
        float rimw = pow(clamp((ndl + 0.3) / 1.69, 0.0, 1.0), 3.0);
        rimc += uLightColor[i] * rimw * back * pow(1.0 - max(dot(N, V), 0.0), 1.5);
        vec3 H = normalize(L + V);
        spec += pow(max(dot(N, H), 0.0), mix(64.0, 4.0, rough)) * (1.0 - rough) * max(dot(uLightColor[i], vec3(0.333)), 0.0);
    }
    light = max(light, vec3(0.0));
    spec = floor(clamp(spec, 0.0, 1.0) * 3.0 + 0.5) / 3.0 * 0.2;
    vec3 c = albedo * (uAmbient * mix(sss, vec3(1.0), 0.5) + light) * mix(1.0, ao, 0.7)
           + spec + max(rimc, vec3(0.0)) * mix(0.4, 1.0, tr.y) * ao * mix(vec3(1.0), albedo, 0.3);
    c += albedo * uSelfIllum * texture(tIllum, vUv).r;
    frag = vec4(pow(c, vec3(1.0 / 2.2)), uOpacity < 0.0 ? 1.0 : (uConstAlpha == 1 ? uOpacity : col.a * uOpacity));
}
"""

TEX_FORMATS = {
    1: QOpenGLTexture.TextureFormat.RGBA_DXT1, 2: QOpenGLTexture.TextureFormat.RGBA_DXT5,
    20: QOpenGLTexture.TextureFormat.RGB_BP_UNorm, 21: QOpenGLTexture.TextureFormat.RG_ATI2N_UNorm,
    27: QOpenGLTexture.TextureFormat.R_ATI1N_UNorm,
}


def _forward(angles):
    p, y = np.radians(angles[0]), np.radians(angles[1])
    return [np.cos(p) * np.cos(y), np.cos(p) * np.sin(y), -np.sin(p)]


class Renderer:
    def __init__(self):
        self.ctx = QOpenGLContext()
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setRenderableType(QSurfaceFormat.OpenGL)   # no Wayland o padrão é OpenGL ES (não aceita GLSL 330)
        self.ctx.setFormat(fmt)
        if not self.ctx.create():
            raise RuntimeError("não deu pra criar contexto OpenGL")
        self.surface = QOffscreenSurface()
        self.surface.setFormat(self.ctx.format())
        self.surface.create()
        self.ctx.makeCurrent(self.surface)
        self.gl = self.ctx.functions()
        self.prog = QOpenGLShaderProgram()
        self.prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self.prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        if not self.prog.link():
            raise RuntimeError(self.prog.log() or "falha ao compilar shaders")
        self.textures = {}
        self.white = self._solid((255, 255, 255, 255))
        self.flat_normal = self._solid((128, 128, 255, 255))
        self.black = self._solid((0, 0, 0, 255))

    def _u(self, name, value):
        loc = self.prog.uniformLocation(name)
        if loc < 0:
            return
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int):
            self.prog.setUniformValue1i(loc, value)
        elif isinstance(value, float):
            self.prog.setUniformValue1f(loc, value)
        else:
            self.prog.setUniformValue(loc, value)

    def _solid(self, rgba):
        img = QImage(4, 4, QImage.Format_RGBA8888)
        img.fill(int.from_bytes(bytes([rgba[3], rgba[0], rgba[1], rgba[2]]), "big"))
        t = QOpenGLTexture(img)
        return t

    def texture(self, path, mip):
        """Sobe pra GPU uma mip já lida por vtex.load_mip (formato, largura, altura, bytes)."""
        if path in self.textures:
            return self.textures[path]
        t = None
        fmt_id, w, h, buf = mip
        fmt = TEX_FORMATS.get(fmt_id)
        if fmt is not None:
            t = QOpenGLTexture(QOpenGLTexture.Target2D)
            t.setFormat(fmt)
            t.setSize(w, h)
            t.setMipLevels(1)
            t.allocateStorage()
            t.setCompressedData(0, len(buf), buf)
        elif fmt_id in (4, 28):
            img = QImage(buf, w, h, QImage.Format_RGBA8888 if fmt_id == 4 else QImage.Format_ARGB32).copy()
            t = QOpenGLTexture(img)
        if t is not None:
            t.setMinMagFilters(QOpenGLTexture.Linear, QOpenGLTexture.Linear)
            t.setWrapMode(QOpenGLTexture.Repeat)
        self.textures[path] = t
        return t

    def material(self, info):
        """Material preparado por material_info() -> texturas na GPU + parâmetros."""
        out = {k: v for k, v in info.items() if k != "textures"}
        for key, tex in info.get("textures", {}).items():
            out[key] = self.texture(*tex) if tex else None
        return out

    @staticmethod
    def _auto_pad(allp, view, fov, aspect, near, dist):
        """Quanto (em frações do enquadramento) a geometria passa pra esquerda/direita/cima; embaixo fixo."""
        proj = QMatrix4x4()
        proj.perspective(fov, aspect, near, dist * 4)
        m = np.array((proj * view).data(), float).reshape(4, 4).T   # QMatrix4x4.data() é coluna a coluna
        pts = allp[:: max(1, len(allp) // 20000)]
        clip = np.concatenate([pts, np.ones((len(pts), 1))], 1) @ m.T
        ndc = clip[:, :2] / np.maximum(clip[:, 3:4], 1e-6)
        cap = 1.2
        def need(v):
            return float(min(cap, max(0.0, (v - 1) / 2) + 0.04))
        return (need(-ndc[:, 0].min()), need(ndc[:, 0].max()), 0.08, need(ndc[:, 1].max()))

    def clear(self):
        """Libera as texturas da GPU (chamar entre heróis)."""
        for t in self.textures.values():
            if t is not None:
                t.destroy()
        self.textures.clear()

    def render(self, draws, materials, size=(768, 1024), yaw_deg=0.0, pitch_deg=4.0, fov=26.0, ssaa=2, scene=None, frame=1.0,
               neutral=False, head=None, front=0.0, head_weight=0.7, turn=0.0, pad=(0, 0, 0, 0)):
        """frame: fração do corpo visível a partir da cabeça (1 = inteiro, 0.45 ≈ busto).
        neutral: ignora as luzes da cena e usa um kit de estúdio branco (a direção da câmera continua a da cena).
        pad: (esquerda, direita, baixo, cima) em frações de size — alarga a imagem em volta do enquadramento
        sem mudar câmera nem escala; o enquadramento original fica em pad_rect(size, pad)."""
        gl = self.gl
        W0, H0 = size[0] * ssaa, size[1] * ssaa
        allp = np.concatenate([d["pos"][d["idx"]] for d in draws])
        lo, hi = allp.min(0), allp.max(0)
        center = (lo + hi) / 2
        height = hi[2] - lo[2]
        width = max(hi[0] - lo[0], hi[1] - lo[1])
        aspect = W0 / H0
        if scene:
            cam = scene["camera"]
            d = -np.array(cam["dir"] if "dir" in cam else _forward(cam["angles"]))   # da cena pro olho
            d[2] = np.clip(d[2], -0.25, 0.25)
            if front:   # gira a câmera um pouco pra frente do herói (o modelo olha pra +X)
                h = d[:2] / (np.linalg.norm(d[:2]) or 1.0)
                h = (1 - front) * h + front * np.array([1.0, 0.0])
                d[:2] = h / (np.linalg.norm(h) or 1.0) * np.linalg.norm(d[:2])
            if turn:   # giro extra da câmera em volta do herói (graus)
                a = np.radians(turn)
                d[:2] = [d[0] * np.cos(a) - d[1] * np.sin(a), d[0] * np.sin(a) + d[1] * np.cos(a)]
            d /= np.linalg.norm(d)
        else:
            yaw, pitch = np.radians(yaw_deg), np.radians(pitch_deg)
            d = np.array([np.cos(yaw) * np.cos(pitch), np.sin(yaw) * np.cos(pitch), np.sin(pitch)])
        # enquadra só a parte de cima do corpo quando frame < 1
        if frame < 1.0:
            top = (head[2] + height * 0.13) if head is not None else hi[2]
            band = allp[(allp[:, 2] >= top - frame * height) & (allp[:, 2] <= top)]
            sel = band if len(band) else allp
            center = (sel.min(0) + sel.max(0)) / 2
            if head is not None:   # mantém a cabeça perto do centro horizontal
                center[:2] = center[:2] * (1 - head_weight) + np.asarray(head[:2]) * head_weight
        else:
            sel = allp
        right = np.cross(d, [0, 0, 1.0]); right /= np.linalg.norm(right)
        up = np.cross(right, d)
        rel = sel - center
        margin = 1.03 if frame >= 1.0 else 1.0
        hw = np.abs(rel @ right).max() * margin
        hh = np.abs(rel @ up).max() * margin
        t = np.tan(np.radians(fov / 2))
        if frame < 1.0:   # zoom: enquadra pela altura e deixa braços/cauda vazarem pros lados
            hh = frame * height / 2
            dist = hh / t + max(0.0, (rel @ d).max()) * 0.3
        else:
            dist = max(hh / t, hw / (t * aspect)) + max(0.0, (rel @ d).max()) * 0.5
        eye = center + d * dist
        near = dist * 0.2
        th = near * t
        tw = th * aspect
        view = QMatrix4x4()
        view.lookAt(QVector3D(*eye), QVector3D(*center), QVector3D(0, 0, 1))
        if pad == "auto":   # alarga só o que a geometria passa do enquadramento (guarda-chuva, orelhas...)
            pad = self._auto_pad(allp, view, fov, aspect, near, dist)
        pl, pr, pb, pt = pad
        self.last_pad = pad
        proj = QMatrix4x4()
        proj.frustum(-tw * (1 + 2 * pl), tw * (1 + 2 * pr), -th * (1 + 2 * pb), th * (1 + 2 * pt), near, dist * 4)
        W, H = round(W0 * (1 + pl + pr)), round(H0 * (1 + pb + pt))
        ff = QOpenGLFramebufferObjectFormat()
        ff.setAttachment(QOpenGLFramebufferObject.Depth)
        ff.setSamples(4)
        fbo = QOpenGLFramebufferObject(QSize(W, H), ff)
        fbo.bind()
        gl.glViewport(0, 0, W, H)
        gl.glClearColor(0, 0, 0, 0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glEnable(GL_MULTISAMPLE)

        mvp = proj * view
        self.last_mvp = np.array(mvp.data(), float).reshape(4, 4).T   # pra project()

        self.prog.bind()
        self._u("uMvp", mvp)
        self._u("uEye", QVector3D(*eye))
        R = height * 1.5
        studio = [   # luz principal, preenchimento e contraluz, relativas à câmera
            {"pos": center + (d * 0.8 + right * 0.7 + up * 0.7) * R, "color": np.array([1.0, 0.97, 0.93])},
            {"pos": center + (d * 0.9 - right * 0.9 - up * 0.1) * R, "color": np.array([0.38, 0.40, 0.46])},
            {"pos": center + (-d * 1.0 - right * 0.5 + up * 0.6) * R, "color": np.array([1.0, 1.0, 1.0])},
            {"pos": center + (-d * 1.0 + right * 0.7 + up * 0.2) * R, "color": np.array([0.6, 0.62, 0.7])},
        ]
        lights = studio if neutral else ((scene or {}).get("lights") or studio)
        lights = lights[:8]
        pos_sum = sum(np.clip(l["color"], 0, None).mean() for l in lights) or 1.0
        k = 1.25 / pos_sum
        self._u("uNumLights", len(lights))
        for i, l in enumerate(lights):
            self.prog.setUniformValue(self.prog.uniformLocation(f"uLightPos[{i}]"), QVector3D(*l["pos"]))
            col = l["color"] * k
            col = col * 0.5 + col.mean() * 0.5     # meio dessaturado: mantém o clima da cena sem esconder a cor da skin
            self.prog.setUniformValue(self.prog.uniformLocation(f"uLightColor[{i}]"), QVector3D(*col))
        self._u("uAmbient", QVector3D(0.16, 0.16, 0.18))
        for i, name in enumerate(("tColor", "tNormal", "tAo", "tRim", "tSss", "tIllum")):
            self.prog.setUniformValue(name, i)

        vaos = []
        for d in draws:
            idx = d["idx"]
            colr = d.get("color")
            colr = colr[idx] if colr is not None else np.ones((len(idx), 4), np.float32)
            v = np.concatenate([d["pos"][idx], d["normal"][idx], d["tangent"][idx], d["uv"][idx], colr], 1).astype(np.float32)
            vao = QOpenGLVertexArrayObject()
            vao.create()
            vao.bind()
            vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            vbo.create()
            vbo.bind()
            raw = v.tobytes()
            vbo.allocate(raw, len(raw))
            stride = 16 * 4
            for name, off, n in (("aPos", 0, 3), ("aNormal", 3, 3), ("aTangent", 6, 4), ("aUv", 10, 2), ("aColor", 12, 4)):
                loc = self.prog.attributeLocation(name)
                if loc >= 0:
                    self.prog.enableAttributeArray(loc)
                    self.prog.setAttributeBuffer(loc, GL_FLOAT, off * 4, n, stride)
            vao.release()
            vaos.append((vao, vbo, len(v), materials.get(d["material"], {})))

        def bind_textures(mat):
            for unit, key, fallback in ((0, "color", self.white), (1, "normal", self.flat_normal), (2, "ao", self.white),
                                        (3, "rim", self.black), (4, "sss", self.white), (5, "illum", self.black)):
                t = mat.get(key) or fallback
                t.bind(unit)

        # contorno (casco invertido) primeiro
        gl.glEnable(GL_CULL_FACE)
        gl.glCullFace(GL_FRONT)
        self._u("uOutlinePass", 1)
        for vao, vbo, n, mat in vaos:
            if not mat.get("outline") or mat.get("translucent"):   # vidro com contorno vira bola sólida
                continue
            bind_textures(mat)
            self._u("uExtrude", float(height * 0.0012))
            self._u("uOutline", QVector3D(*mat["outline"]))
            self._u("uAlphaTest", 1 if mat.get("alphatest") else 0)
            vao.bind()
            gl.glDrawArrays(GL_TRIANGLES, 0, n)
            vao.release()
        gl.glDisable(GL_CULL_FACE)
        self._u("uOutlinePass", 0)
        self._u("uExtrude", 0.0)

        def draw(vao, n, mat, opacity):
            bind_textures(mat)
            self._u("uTint", QVector3D(*mat.get("tint", (1, 1, 1))))
            self._u("uAlphaTest", 1 if mat.get("alphatest") else 0)
            self._u("uOpacity", float(opacity))
            self._u("uConstAlpha", 1 if mat.get("glass") else 0)   # vidro: o alfa da textura é ~0
            self._u("uVertexColor", 1 if mat.get("vcolor") else 0)
            self._u("uSelfIllum", float(mat.get("selfillum", 0.0)))
            vao.bind()
            gl.glDrawArrays(GL_TRIANGLES, 0, n)
            vao.release()

        for vao, vbo, n, mat in vaos:
            if mat.get("translucent"):
                continue
            if mat.get("hull"):   # malha de contorno (casco invertido): só as faces de trás aparecem
                gl.glEnable(GL_CULL_FACE)
                gl.glCullFace(GL_FRONT)
                draw(vao, n, mat, -1.0)
                gl.glDisable(GL_CULL_FACE)
            else:
                draw(vao, n, mat, -1.0)
        # translúcidos (vidro etc.) por último, misturando com o que já foi desenhado
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(False)
        for vao, vbo, n, mat in vaos:
            if mat.get("translucent"):
                draw(vao, n, mat, mat.get("opacity", 1.0))
        gl.glDepthMask(True)
        gl.glDisable(GL_BLEND)

        fbo.release()
        img = fbo.toImage()
        for vao, vbo, _, _ in vaos:
            vbo.destroy()
            vao.destroy()
        if ssaa > 1:
            from PySide6.QtCore import Qt
            w, h = pad_rect(size, pad)[2:]
            img = img.scaled(round(w * (1 + pl + pr)), round(h * (1 + pb + pt)), Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation)
        return img


def project(mvp, point):
    """Ponto 3D -> (x, y) em frações da imagem do último render (origem em cima à esquerda)."""
    c = mvp @ np.append(np.asarray(point, float), 1.0)
    return (c[0] / c[3] + 1) / 2, (1 - c[1] / c[3]) / 2


def pad_rect(size, pad):
    """(x, y, w, h) do enquadramento original dentro da imagem alargada por pad (origem em cima à esquerda)."""
    pl, _pr, _pb, pt = pad
    return (round(size[0] * pl), round(size[1] * pt), size[0], size[1])


def material_info(read, path):
    """Lê um .vmat e as mips das texturas que o shader usa. Não toca em OpenGL (pode rodar em thread)."""
    try:
        m = s2model.resource_kv(read(path + "_c"))
    except Exception:
        return {}
    ints = {p["m_name"]: p["m_nValue"] for p in m.get("m_intParams", [])}
    vecs = {p["m_name"]: p["m_value"] for p in m.get("m_vectorParams", [])}
    floats = {p["m_name"]: p["m_flValue"] for p in m.get("m_floatParams", [])}
    texs = {p["m_name"]: p["m_pValue"] for p in m.get("m_textureParams", [])}

    def tex(name, max_size=1024):
        pth = texs.get(name)
        if not pth:
            return None
        try:
            return pth, vtex.load_mip(read, pth + "_c", max_size)
        except Exception:
            return None
    base = path.rsplit("/", 1)[-1].lower()
    glass = "glass" in base and "hourglass" not in base
    out = {
        "textures": {
            "color": tex("g_tColor"), "normal": tex("g_tNormalRoughness") or tex("g_tNormal"),
            "ao": tex("g_tAmbientOcclusion", 512), "rim": tex("g_tTintMaskRimLightMask", 512),
            "sss": tex("g_tNprTransmissiveColor", 512),
            "illum": tex("g_tSelfIllumMask", 512) if ints.get("F_SELF_ILLUM") else None,
        },
        "tint": tuple(vecs.get("g_vColorTint1", [1, 1, 1])[:3]),
        "alphatest": bool(ints.get("F_ALPHA_TEST")),
        # vidro de verdade: translúcido declarado, ou "glass" que escreve profundidade antes de misturar
        # (o corpo do Paradox tem essa flag e é opaco, então ela sozinha não basta)
        "translucent": bool(any(ints.get(f) for f in ("F_TRANSLUCENT", "F_ADVANCED_TRANSLUCENCY", "F_ADDITIVE_BLEND", "F_GLASS"))
                            or (ints.get("F_WRITE_DEPTH_BEFORE_ALPHA_BLENDING") and glass)),
        "glass": glass,
        "vcolor": bool(ints.get("F_VERTEX_COLOR")),
        "opacity": (0.45 if glass else 0.6 if ints.get("F_GLASS")
                    else float(floats.get("g_flOpacityScale", 1.0))),
        "selfillum": 0.6 if ints.get("F_SELF_ILLUM") else 0.0,
        "hull": "outline" in base,
    }
    if ints.get("F_SOLID_COLOR_OUTLINE"):
        t = vecs.get("g_vSolidOutlineTint", [0.1, 0.08, 0.08])
        lum = sum(t[:3]) / 3
        out["outline"] = tuple((x * 0.4 + lum * 0.6) * 0.35 for x in t[:3])   # mais fino, escuro e menos saturado
    return out
