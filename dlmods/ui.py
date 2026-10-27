"""Interface Qt do gerenciador."""
import hashlib
import re
import subprocess
import sys
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import (
    QByteArray, QEasingCurve, QEvent, QObject, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, QUrl, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetrics, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QRadialGradient, QPainterPath, QPalette, QPen,
    QPixmap, QTextLayout, QTextOption,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QGridLayout, QScrollArea, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QStyle, QStyledItemDelegate, QTextBrowser, QVBoxLayout, QWidget,
)

from . import gamebanana as gb
from . import net, updater
from .previews import Previews
from .store import CACHE, DOWNLOADS, STEAM_APPID, Store, game_running

pool = ThreadPoolExecutor(max_workers=6)
img_pool = ThreadPoolExecutor(max_workers=8)

# ---- tema ----------------------------------------------------------------------
# As cores vêm da paleta do sistema (platform theme do Qt: qtengine/Caelestia, KDE, qt6ct…).
# Sem tema nenhum (paleta padrão do Qt), fica o dourado próprio do app.
GOLD = {
    "bg": "#0f1013", "surface": "#17181c", "surface2": "#1e2026", "raised": "#262930", "border": "#2a2d35",
    "border2": "#3a3d46", "text": "#ece8e1", "muted": "#8e9099", "accent": "#d4ab5f", "accent2": "#e6c27a",
    "on_accent": "#0f1013", "danger": "#e06c6c",
}
C = {}   # paleta atual; mudada no lugar por set_theme()


def mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t)).name()


def palette_colors(pal):
    """Tokens do app a partir de uma QPalette do sistema; None se for a paleta crua do Qt."""
    window, base = pal.color(QPalette.Window), pal.color(QPalette.Base)
    if window.name() == "#efefef" and pal.color(QPalette.Highlight).name() == "#308cc6":
        return None
    dark = window.lightness() < 128
    bg = min(window, base, key=QColor.lightness) if dark else max(window, base, key=QColor.lightness)
    bg, text = bg.name(), pal.color(QPalette.Text).name()
    accent = pal.color(QPalette.Highlight).name()
    return {
        "bg": bg, "surface": mix(bg, text, .035), "surface2": mix(bg, text, .065), "raised": mix(bg, text, .10),
        "border": mix(bg, text, .11), "border2": mix(bg, text, .20), "text": text, "muted": mix(text, bg, .42),
        "accent": accent, "accent2": mix(accent, text, .3), "on_accent": pal.color(QPalette.HighlightedText).name(),
        "danger": "#e06c6c" if dark else "#c43c3c",
    }


def set_theme(colors):
    c = dict(colors)
    bg, text = c["bg"], c["text"]
    c.update({
        "accent_bg": mix(bg, c["accent"], .16), "accent_dim": mix(bg, c["accent"], .45),
        "danger_bg": mix(bg, c["danger"], .18), "danger_border": mix(bg, c["danger"], .4),
        "hover": mix(c["raised"], text, .06), "disabled": mix(bg, text, .35), "scroll": mix(bg, text, .14),
        "scroll_hover": mix(bg, text, .24), "accent_deep": mix(c["accent"], bg, .3), "deep": mix(bg, "#000000", .35),
    })
    C.clear()
    C.update(c)


set_theme(GOLD)

# Widgets cuja cor é aplicada em código (ícones etc.); refeitos quando o tema muda.
_themed = []


def themed(obj, fn):
    fn(obj)
    _themed.append((weakref.ref(obj), fn))
    return obj


def rerun_themed():
    alive = []
    for ref, fn in _themed:
        obj = ref()
        if obj is None:
            continue
        try:
            fn(obj)
        except RuntimeError:   # objeto C++ já destruído
            continue
        alive.append((ref, fn))
    _themed[:] = alive

ROLE_IMG = Qt.UserRole + 1
ROLE_INSTALLED = Qt.UserRole + 2


class Bus(QObject):
    """Ponte pra devolver resultados das threads pra thread da interface."""
    done = Signal(object, object)
    failed = Signal(object, str)


bus = Bus()


class Progress(QObject):
    changed = Signal(int, str)

def _deliver(cb, value):
    try:
        cb(value)
    except RuntimeError as e:
        # O widget que pediu (ex.: linha recriada) já foi destruído quando a resposta chegou.
        if "already deleted" not in str(e):
            raise


bus.done.connect(_deliver)
bus.failed.connect(_deliver)


def run(fn, on_done=None, on_error=None, executor=pool):
    def job():
        try:
            res = fn()
        except Exception as e:
            traceback.print_exc()
            if on_error:
                bus.failed.emit(on_error, str(e) or e.__class__.__name__)
            return
        if on_done:
            bus.done.emit(on_done, res)
    executor.submit(job)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def short(n):
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ---- ícones (traços no estilo Lucide, desenhados via QtSvg) -----------------------
ICONS = {
    "play": '<path d="M6 3.5v17l14-8.5z" fill="currentColor"/>',
    "search": '<circle cx="11" cy="11" r="7.5"/><path d="m21 21-4.3-4.3"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>'
             '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4'
              'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "file-plus": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
                 '<path d="M9 15h6"/><path d="M12 18v-6"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "layers": '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9'
              'a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>'
              '<path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "update": '<circle cx="12" cy="12" r="10"/><path d="m16 12-4-4-4 4"/><path d="M12 16V8"/>',
    "grip": '<circle cx="9" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="19" r="1"/>'
            '<circle cx="15" cy="5" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="19" r="1"/>',
    "left": '<path d="m15 18-6-6 6-6"/>',
    "right": '<path d="m9 18 6-6-6-6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "shirt": '<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10'
             'c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z"/>',
    "package": '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8'
               'a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/>'
               '<path d="M12 22V12"/>',
    "compass": '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-1.8 5.41a2 2 0 0 1-1.27 1.27L7.76 16.24l1.8-5.41'
               'a2 2 0 0 1 1.27-1.27z"/>',
    "library": '<rect width="7" height="7" x="3" y="3" rx="1.5"/><rect width="7" height="7" x="14" y="3" rx="1.5"/>'
               '<rect width="7" height="7" x="14" y="14" rx="1.5"/><rect width="7" height="7" x="3" y="14" rx="1.5"/>',
    "heart": '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2'
             'A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" fill="currentColor"/>',
    "eye": '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/>'
           '<circle cx="12" cy="12" r="3"/>',
    "star": '<path d="M11.53 2.3a.53.53 0 0 1 .95 0l2.31 4.68a2.12 2.12 0 0 0 1.6 1.16l5.16.76a.53.53 0 0 1 .3.9'
            'l-3.74 3.64a2.12 2.12 0 0 0-.61 1.88l.88 5.14a.53.53 0 0 1-.77.56l-4.62-2.43a2.12 2.12 0 0 0-1.97 0'
            'L6.4 21.01a.53.53 0 0 1-.77-.56l.88-5.14a2.12 2.12 0 0 0-.61-1.88L2.16 9.8a.53.53 0 0 1 .3-.91l5.16-.75'
            'a2.12 2.12 0 0 0 1.6-1.16z" fill="currentColor"/>',
}
_icon_cache = {}


def icon_pixmap(name, color, size=18, dpr=2.0):
    key = (name, color, size, dpr)
    if key not in _icon_cache:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
               f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" color="{color}">'
               f'{ICONS[name].replace("currentColor", color)}</svg>')
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        QSvgRenderer(QByteArray(svg.encode())).render(p)
        p.end()
        pm.setDevicePixelRatio(dpr)
        _icon_cache[key] = pm
    return _icon_cache[key]


def icon(name, color, size=18):
    return QIcon(icon_pixmap(name, color, size))


def icon_button(name, tip, obj="icon", color="text"):
    b = QPushButton()
    b.setObjectName(obj)
    themed(b, lambda b: b.setIcon(icon(name, C[color])))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(36, 36)
    return b


def button(text, name=None, obj=None, color="text"):
    b = QPushButton(("  " + text) if name else text)
    if obj:
        b.setObjectName(obj)
    if name:
        themed(b, lambda b: b.setIcon(icon(name, C[color])))
        b.setIconSize(QSize(16, 16))
    b.setCursor(Qt.PointingHandCursor)
    return b


def icon_label(name, color, size):
    return themed(QLabel(), lambda w: w.setPixmap(icon_pixmap(name, C[color], size)))


def logo_pixmap(size):
    svg = (Path(__file__).resolve().parent / "icon.svg").read_text(encoding="utf-8")
    for old, new in (("#c9a35b", C["accent"]), ("#15161a", C["bg"]), ("#e8e6e3", C["text"])):
        svg = svg.replace(old, new)
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


# ---- imagens com cache em disco ------------------------------------------------
_img_mem = {}


def load_image(url, cb, on_error=None):
    if not url:
        return
    if url in _img_mem:
        cb(_img_mem[url])
        return
    path = CACHE / "img" / (hashlib.sha1(url.encode()).hexdigest() + ".img")

    def fetch():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(net.get_bytes(url))
        img = QImage()
        if not img.loadFromData(path.read_bytes()):
            raise ValueError("imagem inválida")
        return img

    def ok(img):
        _img_mem[url] = img
        cb(img)
    def fail(_):
        path.unlink(missing_ok=True)
        if on_error:
            on_error()
    run(fetch, ok, fail, img_pool)


def thumb_pixmap(img, w, h):
    return QPixmap.fromImage(img).scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation).copy(0, 0, w, h)


def rounded_pixmap(img, w, h, radius, dpr=2.0):
    """Miniatura cortada pra preencher w×h, com cantos arredondados (antialias)."""
    src = QPixmap.fromImage(img).scaled(int(w * dpr), int(h * dpr), Qt.KeepAspectRatioByExpanding,
                                        Qt.SmoothTransformation)
    src = src.copy((src.width() - int(w * dpr)) // 2, (src.height() - int(h * dpr)) // 2, int(w * dpr), int(h * dpr))
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(out.rect()), radius * dpr, radius * dpr)
    p.fillPath(path, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def elide_lines(text, font, width, max_lines):
    """Quebra o texto em até max_lines linhas, com reticências na última."""
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append((line.textStart(), line.textLength()))
    layout.endLayout()
    out = [text[s:s + n].rstrip() for s, n in lines[:max_lines]]
    if len(lines) > max_lines:
        rest = text[lines[max_lines - 1][0]:]
        out[-1] = QFontMetrics(font).elidedText(rest, Qt.ElideRight, int(width))
    return out


def draw_pill(p, x, y, text, bg, fg, font, icon_name=None):
    """Desenha um selo arredondado com o topo-esquerdo em (x, y); devolve a largura."""
    fm = QFontMetrics(font)
    h = fm.height() + 6
    iw = 14 if icon_name else 0
    w = fm.horizontalAdvance(text) + 16 + (iw + 4 if icon_name else 0)
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
    p.fillPath(path, QColor(bg))
    tx = x + 8
    if icon_name:
        p.drawPixmap(int(tx), int(y + (h - iw) / 2), icon_pixmap(icon_name, fg, iw))
        tx += iw + 4
    p.setFont(font)
    p.setPen(QColor(fg))
    p.drawText(QRectF(tx, y, w, h), Qt.AlignVCenter | Qt.AlignLeft, text)
    return w


def font(size, weight=QFont.Normal, spacing=None):
    f = QFont(QApplication.font())
    f.setPointSizeF(size)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


# ---- interruptor liga/desliga ------------------------------------------------
class Switch(QAbstractButton):
    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v):
        self._pos = v
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on, t = QColor(C["border2"]), QColor(C["accent"]), self._pos
        track = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 46, 26), 13, 13)
        p.setBrush(QColor(C["on_accent"] if self._pos > .5 else C["text"]))
        p.drawEllipse(QRectF(3 + t * 20, 3, 20, 20))


# ---- aba com ícone e contador -----------------------------------------------
class TabButton(QPushButton):
    def __init__(self, text, icon_name):
        super().__init__()
        self.setObjectName("tab")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.icon_name = icon_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(8)
        self.ico = QLabel()
        self.lbl = QLabel(text)
        self.count = QLabel()
        self.count.setObjectName("count")
        self.count.hide()
        for wdg in (self.ico, self.lbl, self.count):
            wdg.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(wdg, 0, Qt.AlignVCenter)
        self.setFixedHeight(36)
        self.toggled.connect(self._restyle)
        themed(self, lambda t: t._restyle(t.isChecked()))

    def sizeHint(self):
        return self.layout().sizeHint()

    def set_count(self, n):
        self.count.setText(str(n))
        self.count.setVisible(n > 0)
        self.updateGeometry()

    def _restyle(self, on):
        color = C["text"] if on else C["muted"]
        self.ico.setPixmap(icon_pixmap(self.icon_name, C["accent"] if on else color, 17))
        self.lbl.setStyleSheet(f"color: {color}; font-weight: 600; background: transparent;")


# ---- galeria de imagens do diálogo de detalhes -------------------------------
class Gallery(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(560, 315)
        self.img = None
        self.message = "Carregando…"
        self.counter = ""
        self._cache = None
        self.prev_btn = icon_button("left", "Anterior", "round")
        self.next_btn = icon_button("right", "Próxima", "round")
        for b in (self.prev_btn, self.next_btn):
            b.setParent(self)

    def set_image(self, img):
        self.img, self._cache = img, None
        self.update()

    def set_message(self, text):
        self.img, self.message = None, text
        self.update()

    def resizeEvent(self, e):
        y = (self.height() - 36) // 2
        self.prev_btn.move(14, y)
        self.next_btn.move(self.width() - 50, y)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, QColor(C["deep"]))
        if self.img is None:
            p.setPen(QColor(C["muted"]))
            p.drawText(r, Qt.AlignCenter, self.message)
        else:
            key = (self.width(), self.height(), self.devicePixelRatioF())
            if not self._cache or self._cache[0] != key:
                dpr = self.devicePixelRatioF()
                src = QPixmap.fromImage(self.img)
                fit = src.scaled(int(r.width() * dpr), int(r.height() * dpr), Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
                fit.setDevicePixelRatio(dpr)
                # Fundo: a mesma imagem esticada e escurecida (fica bem melhor que barras pretas).
                bg = src.scaled(int(r.width() * dpr / 8), int(r.height() * dpr / 8), Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
                self._cache = (key, fit, bg)
            _, fit, bg = self._cache
            p.setClipPath(path)
            p.setOpacity(0.28)
            p.drawPixmap(r.toRect(), bg)
            p.setOpacity(1)
            fw, fh = fit.width() / fit.devicePixelRatio(), fit.height() / fit.devicePixelRatio()
            p.drawPixmap(int(r.x() + (r.width() - fw) / 2), int(r.y() + (r.height() - fh) / 2), fit)
            p.setClipping(False)
        p.setPen(QPen(QColor(C["border"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if self.counter:
            f = font(9, QFont.DemiBold)
            w = QFontMetrics(f).horizontalAdvance(self.counter) + 18
            draw_pill(p, (self.width() - w) / 2, self.height() - 34, self.counter, "#cc" + C["bg"][1:], C["text"], f)


# ---- tela de detalhes do mod ---------------------------------------------------
class DetailsDialog(QDialog):
    install_requested = Signal(object, object)   # info, [files]

    def __init__(self, parent, mod_id, installed=None):
        super().__init__(parent)
        self.setWindowTitle("Carregando…")
        self.resize(1160, 760)
        self.info = None
        self.installed = installed
        self.img_idx = 0

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(14)
        self.gallery = Gallery()
        self.prev_btn, self.next_btn = self.gallery.prev_btn, self.gallery.next_btn
        self.prev_btn.clicked.connect(lambda: self.show_img(self.img_idx - 1))
        self.next_btn.clicked.connect(lambda: self.show_img(self.img_idx + 1))
        self.desc = QTextBrowser()
        self.desc.setOpenExternalLinks(True)
        themed(self.desc, lambda d: d.document().setDefaultStyleSheet(
            f"a {{ color: {C['accent']}; }} h1, h2, h3 {{ color: {C['text']}; }} body {{ line-height: 140%; }}"))
        left.addWidget(self.gallery, 3)
        left.addWidget(self.desc, 2)
        root.addLayout(left, 1)

        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(370)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(20, 20, 20, 20)
        sl.setSpacing(10)
        self.crumb = QLabel()
        self.crumb.setObjectName("crumb")
        self.title = QLabel("Carregando…")
        self.title.setObjectName("h1")
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        sl.addWidget(self.crumb)
        sl.addWidget(self.title)
        sl.addWidget(self.sub)

        stats = QHBoxLayout()
        stats.setSpacing(8)
        self.stats = {}
        for key, caption in (("likes", "curtidas"), ("downloads", "downloads"), ("version", "versão")):
            box = QFrame()
            box.setObjectName("stat")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 8, 12, 8)
            bl.setSpacing(0)
            val = QLabel("—")
            val.setObjectName("statval")
            cap = QLabel(caption)
            cap.setObjectName("caption")
            bl.addWidget(val)
            bl.addWidget(cap)
            stats.addWidget(box, 1)
            self.stats[key] = val
        sl.addSpacing(4)
        sl.addLayout(stats)
        sl.addSpacing(10)

        cap = QLabel("ARQUIVOS")
        cap.setObjectName("section")
        hint = QLabel("marque o que baixar")
        hint.setObjectName("muted")
        row = QHBoxLayout()
        row.addWidget(cap)
        row.addStretch()
        row.addWidget(hint)
        sl.addLayout(row)
        self.files = QListWidget()
        self.files.setObjectName("files")
        self.files.setWordWrap(True)
        self.files.setSpacing(3)
        self.files.setSelectionMode(QAbstractItemView.NoSelection)
        self.files.itemClicked.connect(
            lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
        sl.addWidget(self.files, 1)
        # FOV FIX: corrige a câmera de skins feitas antes da atualização de 16/10/2026 (ver camfix)
        fov = QHBoxLayout()
        fov.setSpacing(10)
        self.fov_fix = Switch((installed or {}).get("camfix_on", True))
        fov_txt = QVBoxLayout()
        fov_txt.setSpacing(0)
        ft = QLabel("Aplicar FOV FIX")
        ft.setObjectName("rowtitle")
        fh = QLabel("Corrige a câmera travada de skins antigas")
        fh.setObjectName("muted")
        fh.setWordWrap(True)
        fov_txt.addWidget(ft)
        fov_txt.addWidget(fh)
        fov.addWidget(self.fov_fix, 0, Qt.AlignVCenter)
        fov.addLayout(fov_txt, 1)
        sl.addLayout(fov)
        self.install_btn = button("Baixar e instalar", "download", "primary", "on_accent")
        self.install_btn.setMinimumHeight(42)
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.do_install)
        site = button("Abrir no GameBanana", "external")
        site.clicked.connect(lambda: self.info and QDesktopServices.openUrl(QUrl(self.info["url"])))
        sl.addWidget(self.install_btn)
        sl.addWidget(site)
        root.addWidget(side)

        self.show_img(0)
        run(lambda: gb.details(mod_id), self.loaded, self.error)

    def error(self, msg):
        self.title.setText("Erro ao carregar")
        self.sub.setText(msg)
        self.gallery.set_message("")

    def loaded(self, info):
        self.info = info
        self.setWindowTitle(info["name"])
        self.title.setText(info["name"])
        self.crumb.setText((info["category"] + (f"  ›  {info['hero']}" if info["hero"] else "")).upper())
        sub = f"por <b>{info['author']}</b>"
        if info["nsfw"]:
            sub += f'  ·  <span style="color:{C["danger"]}; font-weight:600">+18</span>'
        self.sub.setText(sub)
        self.stats["likes"].setText(short(info["likes"]))
        self.stats["downloads"].setText(short(info["downloads"]))
        self.stats["version"].setText(info["version"] or "—")
        # Imagens da descrição não carregam aqui (QTextBrowser não busca na rede); tiro pra não ficar ícone quebrado.
        self.desc.setHtml(re.sub(r"<img[^>]*>", "", info["html"]) or f'<span style="color:{C["muted"]}">Sem descrição.</span>')
        self.show_img(0)
        installed_ids = {f["file_id"] for f in (self.installed or {}).get("files", [])}
        active = [f for f in info["files"] if not f["archived"]] or info["files"]
        newest = max(active, key=lambda f: f["date"]) if active else None
        for f in info["files"]:
            text = f"{f['name']}\n{human(f['size'])}"
            if f["archived"]:
                text += "  ·  arquivado"
            if f["file_id"] in installed_ids:
                text += "  ·  ✓ instalado"
            if f["description"]:
                text += f"\n{f['description']}"
            it = QListWidgetItem(text)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            checked = f["file_id"] in installed_ids if installed_ids else f is newest
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            it.setData(Qt.UserRole, f)
            self.files.addItem(it)
        self.install_btn.setText("Reinstalar / atualizar" if self.installed else "Baixar e instalar")
        self.install_btn.setEnabled(bool(info["files"]))

    def show_img(self, i):
        imgs = self.info["images"] if self.info else []
        self.prev_btn.setVisible(len(imgs) > 1)
        self.next_btn.setVisible(len(imgs) > 1)
        if not imgs:
            self.gallery.counter = ""
            self.gallery.set_message("Carregando…" if not self.info else "sem imagem")
            return
        self.img_idx = i % len(imgs)
        self.gallery.counter = f"{self.img_idx + 1} / {len(imgs)}" if len(imgs) > 1 else ""
        url = imgs[self.img_idx]

        def show(img, url=url):
            if self.info and self.info["images"][self.img_idx] == url:
                self.gallery.set_image(img)
        load_image(url, show)

    def do_install(self):
        chosen = [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())
                  if self.files.item(i).checkState() == Qt.Checked]
        if not chosen:
            QMessageBox.information(self, "Nada marcado", "Marca pelo menos um arquivo pra baixar.")
            return
        self.install_requested.emit(dict(self.info, camfix=self.fov_fix.isChecked()), chosen)
        self.accept()


# ---- escolha de variantes (.vpk) de um mod instalado ------------------------
class VpksDialog(QDialog):
    def __init__(self, parent, mod):
        super().__init__(parent)
        self.setWindowTitle(f"Arquivos de {mod['name']}")
        self.resize(580, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)
        title = QLabel("Variantes")
        title.setObjectName("h2")
        hint = QLabel("Marque quais arquivos ficam ativos. Mods com várias cores/versões "
                      "normalmente só funcionam com <b>um</b> marcado.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(hint)
        self.list = QListWidget()
        self.list.setObjectName("files")
        self.list.setSpacing(3)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.itemClicked.connect(
            lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
        for v in mod["vpks"]:
            it = QListWidgetItem(v["name"] + (f"\nde {v['archive']}" if v["archive"] != v["name"] else ""))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if v["enabled"] else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.button(QDialogButtonBox.Ok).setText("Salvar")
        bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def states(self):
        return [self.list.item(i).checkState() == Qt.Checked for i in range(self.list.count())]


# ---- linha de mod instalado -------------------------------------------------
class ModRow(QFrame):
    def __init__(self, win, mod):
        super().__init__()
        self.setObjectName("row")
        self.setAttribute(Qt.WA_Hover)
        self.win, self.mod = win, mod
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        lay.setSpacing(12)
        grip = icon_label("grip", "border2", 18)
        grip.setToolTip("Arraste pra mudar a prioridade")
        lay.addWidget(grip)
        self.pos_lbl = QLabel()
        self.pos_lbl.setObjectName("prio")
        self.pos_lbl.setAlignment(Qt.AlignCenter)
        self.pos_lbl.setFixedSize(28, 28)
        lay.addWidget(self.pos_lbl)
        self.thumb = QLabel()
        self.thumb.setFixedSize(144, 81)
        self.thumb.setObjectName("image")
        lay.addWidget(self.thumb)
        load_image(mod.get("thumb"), lambda img: self.thumb.setPixmap(rounded_pixmap(img, 144, 81, 8)))

        self.body = QWidget()
        info = QVBoxLayout(self.body)
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(6)
        name = QLabel(mod["name"])
        name.setObjectName("rowtitle")
        name.setWordWrap(True)
        info.addWidget(name)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        for text, obj in ((mod.get("category"), "chip"), (mod.get("hero"), "chipaccent")):
            if text:
                c = QLabel(text)
                c.setObjectName(obj)
                meta.addWidget(c, 0, Qt.AlignVCenter)
        on = sum(v["enabled"] for v in mod["vpks"])
        bits = [f"por {mod['author']}"] if mod.get("author") else []
        bits.append(f"{on}/{len(mod['vpks'])} arquivo(s) ativo(s)")
        sub = QLabel("  ·  ".join(bits))
        sub.setObjectName("muted")
        meta.addWidget(sub)
        meta.addStretch()
        info.addLayout(meta)
        if mod.get("update"):
            up = QLabel("⬆  atualização disponível")
            up.setObjectName("update")
            info.addWidget(up)
        lay.addWidget(self.body, 1)

        self.toggle = Switch(mod["enabled"])
        self.toggle.setToolTip("Ligar/desligar este mod")
        self.toggle.toggled.connect(self.on_toggle)
        lay.addWidget(self.toggle)
        lay.addSpacing(6)
        if len(mod["vpks"]) > 1:
            b = icon_button("layers", "Escolher variantes")
            b.clicked.connect(self.on_vpks)
            lay.addWidget(b)
        else:
            lay.addSpacing(36 + lay.spacing())   # mantém o interruptor alinhado entre as linhas
        if mod.get("remote"):
            if mod.get("update"):
                b = button("Atualizar", "update", "primary", "on_accent")
            else:
                b = icon_button("info", "Detalhes")
            b.clicked.connect(lambda: win.open_details(mod["id"]))
            lay.addWidget(b)
        rm = icon_button("trash", "Remover", "danger", "danger")
        rm.clicked.connect(self.on_remove)
        lay.addWidget(rm)
        self._dim(mod["enabled"])

    def _dim(self, enabled):
        # Mod desligado fica apagadinho.
        for wdg in (self.thumb, self.body):
            eff = QGraphicsOpacityEffect(wdg)
            eff.setOpacity(1.0 if enabled else 0.4)
            wdg.setGraphicsEffect(eff)

    def on_toggle(self, val):
        self.mod["enabled"] = val
        self._dim(val)
        self.win.store.save()
        self.win.apply()

    def on_vpks(self):
        self.win.edit_vpks(self.mod, self)

    def on_remove(self):
        self.win.remove_mod(self.mod, self)


# ---- skins agrupadas por personagem ----------------------------------------
def big_thumb(url):
    """Versão 530px da miniatura do GameBanana (a 220px fica borrada em card grande)."""
    return url.replace("/220-90_", "/530-90_") if url else url


def cover(img, w, h, dpr):
    pm = thumb_pixmap(img, int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    return pm


def _hsl(h, s, l, a=255):
    c = QColor.fromHslF(h % 1.0, min(1.0, s), min(1.0, l))
    c.setAlpha(a)
    return c


def stage_background(w, h_px, hue, name, dpr):
    """Fundo 'pôster com nome vazado': faixa diagonal na cor do herói e o nome repetido em contorno."""
    h, s = hue
    pm = QPixmap(int(w * dpr), int(h_px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    r = QRectF(0, 0, w, h_px)
    g = QLinearGradient(0, 0, 0, h_px)
    g.setColorAt(0, _hsl(h, s * .75, .09))
    g.setColorAt(1, _hsl(h, s * .6, .035))
    p.fillRect(r, g)
    band = QPainterPath()
    band.moveTo(0, h_px * .62)
    band.lineTo(w, h_px * .28)
    band.lineTo(w, h_px)
    band.lineTo(0, h_px)
    p.fillPath(band, _hsl(h, s, .30))
    stripe = QPainterPath()
    stripe.moveTo(0, h_px * .66)
    stripe.lineTo(w, h_px * .32)
    stripe.lineTo(w, h_px * .35)
    stripe.lineTo(0, h_px * .69)
    p.fillPath(stripe, _hsl(h, s, .7, 200))
    # nome em contorno, inclinado e repetido em linhas alternadas
    text = name.upper()
    size = 10
    while size < 120 and QFontMetricsF(font(size + 1, QFont.Black)).horizontalAdvance(text) < w * 1.6:
        size += 1
    f = font(size, QFont.Black, -1)
    fm = QFontMetricsF(f)
    p.translate(r.center())
    p.rotate(-22)
    p.translate(-r.center())
    pen = QPen(_hsl(h, s, .65, 55), 1.2)
    step = max(1, int(fm.height() * .8))
    for i, y in enumerate(range(-40, int(h_px) + 60, step)):
        path = QPainterPath()
        path.addText(QPointF(-60 - (i % 2) * 40, y), f, text)
        p.strokePath(path, pen)
    p.end()
    return pm


_stage_cache = {}


def paint_stage(p, r, path, render, thumb, cache, dpr, glow, hue=None, name="", figure=True):
    """Fundo na cor do herói + retrato renderizado, ou a imagem do GameBanana enquanto ele não sai."""
    if not hue:
        a = QColor(C["accent"])
        hue = (max(0.0, a.hslHueF()), max(.45, a.hslSaturationF()))
    h, s = hue
    p.save()
    p.setClipPath(path)
    bkey = (int(r.width()), int(r.height()), dpr, tuple(hue), name)
    if cache.get("bk") != bkey:
        bg = _stage_cache.get(bkey)   # global: os cards são recriados a cada atualização da aba
        if bg is None:
            if len(_stage_cache) > 200:
                _stage_cache.clear()
            bg = _stage_cache[bkey] = stage_background(r.width(), r.height(), hue, name, dpr)
        cache.update(bk=bkey, bg=bg)
    p.drawPixmap(r.topLeft(), cache["bg"])
    if glow > 1.0:
        p.fillRect(r, _hsl(h, s, .6, int(28 * (glow - 1) * 2)))
    if render is not None and not render.isNull():
        key = ("r", int(r.width()), int(r.height()), dpr, render.cacheKey())
        if cache.get("k") != key:
            cache.update(k=key, pm=cover(render, int(r.width()) + 1, int(r.height()) + 1, dpr))
        if figure:
            p.drawPixmap(r.topLeft(), cache["pm"])
    elif thumb is not None and not thumb.isNull():
        key = ("t", int(r.width()), int(r.height()), dpr, thumb.cacheKey())
        if cache.get("k") != key:
            cache.update(k=key, pm=cover(thumb, int(r.width()) + 1, int(r.height()) + 1, dpr))
        p.setOpacity(0.55)
        p.drawPixmap(r.topLeft(), cache["pm"])
        p.setOpacity(1)
    vg = QRadialGradient(r.center(), r.width() * .9)
    vg.setColorAt(.55, QColor(0, 0, 0, 0))
    vg.setColorAt(1, QColor(0, 0, 0, 140))
    p.fillRect(r, vg)
    p.restore()


class HeroCard(QWidget):
    """Card de um personagem: retrato 3D com a skin ativa (pose da tela de seleção do jogo)."""
    clicked = Signal()
    RATIO = 4 / 3

    def __init__(self, hero, skins, icon_url, previews=None):
        super().__init__()
        self.hero, self.skins = hero, skins
        self.active = [m for m in skins if m["enabled"]]
        self.render = self.thumb = self.icon = self.hue = self.full = self.crop = None
        self._cache = {}
        self.pop = 0.0   # 0..1: quanto o personagem já "saiu" do card (hover segurado)
        self._hold = QTimer(self, singleShot=True, interval=40)
        self._hold.timeout.connect(lambda: self._animate(1.0, 200, QEasingCurve.OutBack))
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._set_pop)
        self._anim.finished.connect(self._check_hover)
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        shown = self.active[0] if self.active else None
        if shown:
            url = shown.get("thumb")
            load_image(big_thumb(url), self._set_thumb, lambda: load_image(url, self._set_thumb))
        load_image(icon_url, self._set_icon)
        if previews:
            previews.request(hero, shown, self._set_render)

    def _set_render(self, img, hue=None, full=None, crop=None):
        self.render, self.hue = img, hue
        self.full, self.crop = full, crop   # imagem com espaço em volta, pro efeito de sair do card
        self.update()

    def _set_thumb(self, img):
        self.thumb = img
        self.update()

    def _set_icon(self, img):
        self.icon = img
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    # ---- "sair do card" (estilo Marvel Rivals) ----------------------------------
    def enterEvent(self, e):
        super().enterEvent(e)
        if self.full is not None:
            self._hold.start()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hold.stop()
        # mesmo com a animação recém-começada (pop ainda 0) tem que voltar, senão ela vai até o fim e fica
        if self.pop > 0 or self._anim.state() == QVariantAnimation.Running:
            self._animate(0.0, 160, QEasingCurve.InCubic)

    def _check_hover(self):
        """Terminou de sair mas o mouse já não está mais em cima (saiu rápido): volta."""
        if self._anim.endValue() == 1.0 and not self.underMouse():
            self._animate(0.0, 160, QEasingCurve.InCubic)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._hold.stop()
        self._anim.stop()
        self._set_pop(0.0)

    def _animate(self, to, ms, curve):
        self._anim.stop()
        self._anim.setStartValue(float(self.pop))
        self._anim.setEndValue(float(to))
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(curve)
        self._anim.start()

    def _set_pop(self, v):
        self.pop = max(0.0, float(v))
        layer = getattr(self.window(), "pop_layer", None)
        if layer is not None:
            layer.track(self if self.pop > 0.001 else None, self)
        self.update()

    def popped_pixmap(self):
        """(pixmap da imagem inteira, retângulo dela relativo ao card) pra camada de pop, ou None."""
        if self.full is None or not self.crop:
            return None
        x, y, w, h = self.crop
        k = self.width() / w                       # o busto ocupa o card inteiro (mesma proporção)
        rect = QRectF(-x * k, -y * k, self.full.width() * k, self.full.height() * k)
        key = (self.full.cacheKey(), int(rect.width()), self.devicePixelRatioF())
        if self._cache.get("popk") != key:
            dpr = self.devicePixelRatioF()
            pm = QPixmap.fromImage(self.full.scaled(int(rect.width() * dpr), int(rect.height() * dpr),
                                                    Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            pm.setDevicePixelRatio(dpr)
            self._cache.update(popk=key, poppm=pm)
        return self._cache["poppm"], rect

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        hover = self.underMouse()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        card = QPainterPath()
        card.addRoundedRect(r, 14, 14)
        popping = self.pop > 0.001 and self.popped_pixmap() is not None
        paint_stage(p, r, card, self.render, self.thumb, self._cache, self.devicePixelRatioF(), 1.4 if hover else 1.0,
                    self.hue, self.hero, figure=not popping)
        if not popping:   # durante o pop a camada por cima desenha o personagem e o texto
            self.paint_chrome(p, r, card, hover)
        else:             # a borda fica embaixo do personagem
            self.paint_border(p, card, True)

    def paint_border(self, p, card, hover):
        p.setPen(QPen(QColor(C["accent"] if hover else C["border"]), 1.2 if hover else 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(card)

    def paint_chrome(self, p, r, card, hover, border=True):
        """Faixa escura, ícone, nome, pílulas e (opcional) borda do card."""
        # faixa escura embaixo pro texto
        p.save()
        p.setClipPath(card)
        shade = QLinearGradient(0, r.bottom() - 92, 0, r.bottom())
        bg = QColor(C["bg"])
        bg.setAlpha(0)
        shade.setColorAt(0, bg)
        bg.setAlpha(235)
        shade.setColorAt(0.65, bg)
        shade.setColorAt(1, bg)
        p.fillRect(QRectF(r.left(), r.bottom() - 92, r.width(), 92), shade)
        p.restore()

        av = QRectF(r.x() + 12, r.bottom() - 50, 38, 38)
        circle = QPainterPath()
        circle.addEllipse(av)
        p.fillPath(circle, QColor(C["raised"]))
        if self.icon is not None:
            p.save()
            p.setClipPath(circle)
            p.drawImage(av.adjusted(2, 2, -2, -2), self.icon)
            p.restore()
        p.setPen(QPen(QColor(C["accent"] if self.active else C["border2"]), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(av)

        tx = av.right() + 10
        tw = r.right() - tx - 10
        f1, f2 = font(12, QFont.Bold), font(9)
        p.setFont(f1)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(tx, av.top() - 2, tw, 22), Qt.AlignLeft | Qt.AlignVCenter, self.hero)
        if len(self.active) == 1:
            sub, color = self.active[0]["name"], C["accent"]
        elif self.active:
            sub, color = f"{len(self.active)} skins ativas", C["accent"]
        elif self.skins:
            sub, color = "Padrão (sem skin)", C["muted"]
        else:
            sub, color = "Procurar skins", C["muted"]
        p.setFont(f2)
        p.setPen(QColor(color))
        p.drawText(QRectF(tx, av.top() + 19, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f2).elidedText(sub, Qt.ElideRight, int(tw)))

        bf = font(8, QFont.Bold, 0.6)
        n = len(self.skins)
        label = f"{n} SKINS" if n > 1 else "1 SKIN" if n else "SEM SKINS"
        w = QFontMetrics(bf).horizontalAdvance(label) + 16
        draw_pill(p, r.right() - w - 10, r.y() + 10, label, "#c8" + C["bg"][1:], C["text"], bf)
        if any(m.get("update") for m in self.skins):
            draw_pill(p, r.x() + 10, r.y() + 10, "ATUALIZAÇÃO", C["accent"], C["on_accent"], bf, "update")
        if border:
            self.paint_border(p, card, hover)


class PopLayer(QWidget):
    """Camada transparente por cima da janela: desenha o personagem do card em hover maior, saindo pela
    borda de cima (um widget não consegue pintar fora do próprio retângulo)."""

    LIFT = 0.16        # quanto o personagem cresce (fração do card)
    SIDE_SOLID = 0.15  # o que sai pelos lados fica sólido até essa distância da borda (fração da largura do card)
    SIDE_FADE = 0.6    # ...e some de vez nessa

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.cards = []   # vários ao mesmo tempo: o que está voltando e o que está saindo (mouse rápido)
        self.hide()

    def track(self, card, sender):
        if card is None:
            if sender in self.cards:
                self.cards.remove(sender)
        elif sender not in self.cards:
            self.cards.append(sender)
        self.setVisible(bool(self.cards))
        if self.cards:
            self.setGeometry(self.parent().rect())
            self.raise_()
        self.update()

    @staticmethod
    def _tinted(card, pm, color):
        """A silhueta do personagem pintada numa cor só (pra aura)."""
        key = (pm.cacheKey(), color.rgba())
        if card._cache.get("tk") != key:
            out = QPixmap(pm)
            q = QPainter(out)
            q.setCompositionMode(QPainter.CompositionMode_SourceIn)
            q.fillRect(QRectF(0, 0, out.width(), out.height()), color)
            q.end()
            card._cache.update(tk=key, tpm=out)
        return card._cache["tpm"]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        for c in sorted(self.cards, key=lambda c: c.pop):   # o mais "saído" por cima
            p.save()
            self._paint_card(p, c)
            p.restore()

    def _paint_card(self, p, c):
        try:
            got = c.popped_pixmap()
            if got is None or not c.isVisible():
                return
            pm, inner = got
            tl = c.mapTo(self.window(), QPointF(0, 0).toPoint())
        except RuntimeError:   # card destruído
            if c in self.cards:
                self.cards.remove(c)
            return
        tl = self.mapFrom(self.window(), tl)
        r = QRectF(tl.x() + 0.5, tl.y() + 0.5, c.width() - 1, c.height() - 1)
        k = c.pop
        s = 1 + self.LIFT * k
        # imagem inteira posicionada pro busto cair em cima do card, ampliada a partir da base do card
        ax, ay = r.center().x(), r.bottom()
        target = QRectF(ax + (r.left() + inner.left() - ax) * s, ay + (r.top() + inner.top() - ay) * s,
                        inner.width() * s, inner.height() * s)
        # A imagem tem folga automática em volta do personagem, então ele pode sair do card pra cima e pros
        # lados sem corte reto; só embaixo ela é cortada (lá termina o busto), na base do card. Abaixo do
        # topo do card fica dentro da área visível da grade; acima, pode cobrir qualquer coisa. Ao lado do
        # card ele some em degradê até a base, pra não aparecer o corte do busto.
        win = self.window()
        full = QRectF(target.left(), target.top(), target.width(), r.bottom() - target.top())
        upper, lower = QPainterPath(), QPainterPath()
        upper.addRect(QRectF(full.left(), full.top(), full.width(), r.top() - full.top()))
        lower.addRect(QRectF(full.left(), r.top(), full.width(), r.bottom() - r.top()))
        view = getattr(c, "viewport_rect", None)
        vr = view() if view is not None else None
        if vr is not None:
            g = QPainterPath()
            g.addRect(QRectF(self.mapFrom(win, vr.topLeft().toPoint()), vr.size()))
            lower = lower.intersected(g)
        inside = QPainterPath()
        inside.addRoundedRect(r, 14, 14)
        # dentro do card: sólido. Fora (em cima e dos lados): numa camada com máscara — some pros lados
        # a partir da borda do card e, abaixo do topo dele, também some descendo até a base.
        solid = lower.intersected(inside)
        outside = upper.united(lower.subtracted(inside))

        h, sat = c.hue or (QColor(C["accent"]).hslHueF(), 0.6)
        kk = min(k, 1.0)
        a = 1.04
        aura = QRectF(ax + (target.left() - ax) * a, ay + (target.top() - ay) * a, target.width() * a, target.height() * a)
        tint = self._tinted(c, pm, _hsl(h, sat, .6))

        def figure(q):
            q.setOpacity(0.8 * kk)   # aura: a silhueta um pouco maior, tingida na cor do herói
            q.drawPixmap(aura, tint, QRectF(pm.rect()))
            q.setOpacity(1)
            q.drawPixmap(target, pm, QRectF(pm.rect()))

        # brilho na cor do herói atrás do personagem (só dentro do card)
        p.save()
        p.setClipPath(inside)
        glow = QRadialGradient(QPointF(r.center().x(), r.top() + r.height() * 0.4), r.width() * 0.75)
        glow.setColorAt(0, _hsl(h, sat, .65, int(190 * kk)))
        glow.setColorAt(.5, _hsl(h, sat, .5, int(80 * kk)))
        glow.setColorAt(1, _hsl(h, sat, .4, 0))
        p.fillRect(r, glow)
        p.restore()
        # acima do card e dentro dele: o personagem inteiro
        p.save()
        p.setClipPath(solid)
        figure(p)
        p.restore()
        # fora do card: numa camada à parte, com as máscaras
        box = outside.boundingRect().toAlignedRect()
        if box.width() > 0 and box.height() > 0:
            dpr = self.devicePixelRatioF()
            layer = QPixmap(int(box.width() * dpr), int(box.height() * dpr))
            layer.setDevicePixelRatio(dpr)
            layer.fill(Qt.transparent)
            q = QPainter(layer)
            q.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
            q.translate(-box.left(), -box.top())
            q.setClipPath(outside)
            figure(q)
            q.setClipping(False)
            q.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            reach, solid_w = r.width() * self.SIDE_FADE, r.width() * self.SIDE_SOLID
            left, right = r.left() - reach, r.right() + reach
            span = right - left
            side = QLinearGradient(left, 0, right, 0)   # sólido no card e um pouco além, some pros lados
            side.setColorAt(0, QColor(0, 0, 0, 0))
            side.setColorAt((reach - solid_w) / span, QColor(0, 0, 0, 255))
            side.setColorAt(1 - (reach - solid_w) / span, QColor(0, 0, 0, 255))
            side.setColorAt(1, QColor(0, 0, 0, 0))
            q.fillRect(QRectF(box), side)
            down = QLinearGradient(0, r.top() + r.height() * 0.7, 0, r.bottom())   # abaixo do topo do card
            down.setColorAt(0, QColor(0, 0, 0, 255))
            down.setColorAt(1, QColor(0, 0, 0, 0))
            q.fillRect(QRectF(box.left(), r.top(), box.width(), box.bottom() - r.top() + 1), down)
            q.end()
            p.drawPixmap(box.topLeft(), layer)
        card = QPainterPath()
        card.addRoundedRect(r, 14, 14)
        c.paint_chrome(p, r, card, True, border=False)   # a borda o próprio card desenha, embaixo


class CardFlow(QScrollArea):
    """Grade de widgets de tamanho fixo cujas colunas esticam com a janela."""

    def __init__(self, min_w, extra_h, spacing=14, ratio=9 / 16, margin=4):
        super().__init__()
        self.min_w, self.extra_h, self.spacing, self.ratio, self.margin = min_w, extra_h, spacing, ratio, margin
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        inner = QWidget()
        inner.setObjectName("flow")
        self.grid = QGridLayout(inner)
        self.grid.setContentsMargins(margin, margin, margin, margin)
        self.grid.setSpacing(spacing)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setWidget(inner)
        self.cards = []

    def set_cards(self, cards):
        for c in self.cards:
            c.deleteLater()
        self.cards = cards
        self.relayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def relayout(self):
        avail = self.viewport().width() - 2 * self.margin
        cols = max(1, (avail + self.spacing) // (self.min_w + self.spacing))
        w = (avail - self.spacing * (cols - 1)) // cols
        h = round(w * self.ratio) + self.extra_h
        for i, c in enumerate(self.cards):
            c.setFixedSize(w, h)
            self.grid.addWidget(c, i // cols, i % cols)


class SkinTile(QWidget):
    """Opção da tela de seleção: retrato do herói com essa skin (ou o padrão), destacado se ativo."""
    clicked = Signal()

    def __init__(self, active, hero, mod, previews=None):
        super().__init__()
        self.active, self.hero = active, hero
        self.render = self.thumb = self.hue = None
        self._cache = {}
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        url = mod.get("thumb") if mod else None
        if url:
            load_image(big_thumb(url), self._set_thumb, lambda: load_image(url, self._set_thumb))
        if previews:
            previews.request(hero, mod, self._set_render)

    def _set_render(self, img, hue=None, full=None, crop=None):
        self.render, self.hue = img, hue
        self.update()

    def _set_thumb(self, img):
        self.thumb = img
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        hover = self.underMouse()
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        paint_stage(p, r, path, self.render, self.thumb, self._cache, self.devicePixelRatioF(),
                    1.3 if hover else 1.0, self.hue, self.hero)
        if self.render is None and self.thumb is None:
            p.setPen(QColor(C["muted"]))
            p.setFont(font(9))
            p.drawText(r, Qt.AlignCenter, "gerando prévia…")
        if not self.active and not hover:
            p.save()
            p.setClipPath(path)
            p.fillRect(r, QColor(0, 0, 0, 55))
            p.restore()
        if self.active:
            draw_pill(p, r.x() + 8, r.y() + 8, "ATIVA", C["accent"], C["on_accent"], font(8, QFont.Bold, 0.6), "check")
        pen = QPen(QColor(C["accent"] if self.active else C["accent2"] if hover else C["border"]),
                   3 if self.active else 1.5 if hover else 1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class SkinPicker(QDialog):
    """Tela de seleção de skin de um personagem: clicou, trocou."""

    def __init__(self, win, hero):
        super().__init__(win)
        self.win, self.hero = win, hero
        self.setWindowTitle(f"Skins — {hero}")
        self.resize(980, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.avatar = QLabel()
        self.avatar.setFixedSize(44, 44)
        self.icon = None
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel(hero)
        t.setObjectName("h1")
        sub = QLabel("Clique numa skin pra deixar ela ativa — as outras desse personagem são desligadas.")
        sub.setObjectName("muted")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addWidget(self.avatar)
        head.addLayout(titles, 1)
        lay.addLayout(head)
        self.flow = CardFlow(190, 64, ratio=4 / 3)
        lay.addWidget(self.flow, 1)
        self.build()
        load_image(win.hero_icons.get(hero), self._set_icon)

    def _set_icon(self, img):
        self.icon = img
        self.avatar.setPixmap(rounded_pixmap(img, 44, 44, 22))

    def skins(self):
        return [m for m in self.win.store.mods if self.win.kind_of(m) == "skins" and self.win.hero_of(m) == self.hero]

    def build(self):
        skins = self.skins()
        if not skins:
            self.accept()
            return
        cards = [self.card(None, "Padrão", "sem skin", not any(m["enabled"] for m in skins))]
        cards += [self.card(m, m["name"], f"por {m['author']}" if m.get("author") else "", m["enabled"]) for m in skins]
        self.flow.set_cards(cards)

    def card(self, mod, title, sub, active):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        w.tile = SkinTile(active, self.hero, mod, self.win.previews)
        w.tile.clicked.connect(lambda: self.choose(mod))
        v.addWidget(w.tile, 1)
        row = QHBoxLayout()
        row.setSpacing(4)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        name = QLabel(title)
        name.setObjectName("rowtitle")
        name.setToolTip(title)
        name.setMinimumWidth(10)
        meta = QLabel(sub)
        meta.setObjectName("muted")
        texts.addWidget(name)
        texts.addWidget(meta)
        row.addLayout(texts, 1)
        if mod:
            if len(mod["vpks"]) > 1:
                b = icon_button("layers", "Escolher variantes")
                b.clicked.connect(lambda: (self.win.edit_vpks(mod, self), self.build()))
                row.addWidget(b)
            if mod.get("remote"):
                if mod.get("update"):
                    b = icon_button("update", "Atualização disponível", "primary", "on_accent")
                else:
                    b = icon_button("info", "Detalhes")
                b.clicked.connect(lambda: self.win.open_details(mod["id"], self))
                row.addWidget(b)
            rm = icon_button("trash", "Remover", "danger", "danger")
            rm.clicked.connect(lambda: self.win.remove_mod(mod, self) and self.build())
            row.addWidget(rm)
        v.addLayout(row)
        return w

    def choose(self, mod):
        for m in self.skins():
            m["enabled"] = m is mod
        self.win.store.save()
        self.win.apply()
        self.win.refresh_installed()
        self.accept()


# ---- grade de cards do Explorar ----------------------------------------------
CARD_MIN_W = 250
CARD_PAD = 7       # espaço em volta de cada card
CARD_TEXT_H = 88   # área de texto embaixo da imagem


class CardGrid(QListWidget):
    """Lista em modo ícone cujas colunas esticam pra ocupar a largura toda."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_Hover)
        self.viewport().setCursor(Qt.PointingHandCursor)
        self.setItemDelegate(CardDelegate(self))
        self.setGridSize(QSize(CARD_MIN_W, 240))

    def resizeEvent(self, e):
        avail = self.viewport().width() - 1
        cols = max(1, avail // CARD_MIN_W)
        w = avail // cols
        h = int((w - 2 * CARD_PAD) * 9 / 16) + 2 * CARD_PAD + CARD_TEXT_H
        if self.gridSize() != QSize(w, h):
            self.setGridSize(QSize(w, h))
        super().resizeEvent(e)


class CardDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.title_font = font(10.5, QFont.DemiBold)
        self.meta_font = font(9)
        self.badge_font = font(8, QFont.Bold, 0.6)
        self._pix = {}

    def sizeHint(self, option, index):
        return self.view.gridSize()

    def _thumb(self, img, w, h, dpr):
        key = (img.cacheKey(), w, h, dpr)
        pm = self._pix.get(key)
        if pm is None:
            if len(self._pix) > 400:
                self._pix.clear()
            pm = thumb_pixmap(img, int(w * dpr), int(h * dpr))
            pm.setDevicePixelRatio(dpr)
            self._pix[key] = pm
        return pm

    def paint(self, p, opt, index):
        m = index.data(Qt.UserRole)
        img = index.data(ROLE_IMG)
        hover = bool(opt.state & QStyle.State_MouseOver)
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        r = QRectF(opt.rect).adjusted(CARD_PAD + 0.5, CARD_PAD + 0.5, -CARD_PAD - 0.5, -CARD_PAD - 0.5)
        card = QPainterPath()
        card.addRoundedRect(r, 12, 12)
        p.fillPath(card, QColor(C["surface2"] if hover else C["surface"]))

        # imagem (cortada pelo contorno do card)
        ih = round(r.width() * 9 / 16)
        ir = QRectF(r.x(), r.y(), r.width(), ih)
        p.setClipPath(card)
        if img is not None and not img.isNull():
            p.drawPixmap(ir.topLeft(), self._thumb(img, int(ir.width()) + 1, ih, p.device().devicePixelRatioF()))
        else:
            p.fillRect(ir, QColor(C["raised"]))
            p.drawPixmap(int(ir.center().x() - 14), int(ir.center().y() - 14), icon_pixmap("package", C["border2"], 28))
        if not hover:
            p.fillRect(ir, QColor(0, 0, 0, 25))
        p.setClipping(False)

        # selos sobre a imagem
        x = r.x() + 10
        if index.data(ROLE_INSTALLED):
            x += draw_pill(p, x, r.y() + 10, "INSTALADO", C["accent"], C["on_accent"], self.badge_font, "check") + 6
        if m["nsfw"]:
            draw_pill(p, x, r.y() + 10, "+18", "#d0b03a3a", "#ffffff", self.badge_font)
        if m["featured"]:
            s = 26
            sr = QRectF(r.right() - s - 10, r.y() + 10, s, s)
            path = QPainterPath()
            path.addEllipse(sr)
            p.fillPath(path, QColor("#c8" + C["bg"][1:]))
            p.drawPixmap(int(sr.x() + 6), int(sr.y() + 6), icon_pixmap("star", C["accent"], 14))

        # título (até 2 linhas)
        tx, tw = r.x() + 12, r.width() - 24
        y = ir.bottom() + 10
        p.setFont(self.title_font)
        p.setPen(QColor(C["text"]))
        lh = QFontMetrics(self.title_font).lineSpacing()
        for line in elide_lines(m["name"], self.title_font, tw, 2):
            p.drawText(QRectF(tx, y, tw, lh), Qt.AlignLeft | Qt.AlignVCenter, line)
            y += lh

        # rodapé: herói/categoria à esquerda, curtidas e visualizações à direita
        fm = QFontMetrics(self.meta_font)
        by = r.bottom() - 12 - fm.height()
        p.setFont(self.meta_font)
        right = r.right() - 12
        for ico, val in (("eye", m.get("views", 0)), ("heart", m["likes"])):
            t = short(val)
            tw2 = fm.horizontalAdvance(t)
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(right - tw2, by, tw2, fm.height()), Qt.AlignVCenter, t)
            right -= tw2 + 16
            p.drawPixmap(int(right), int(by + (fm.height() - 13) / 2), icon_pixmap(ico, C["muted"], 13))
            right -= 10
        label = fm.elidedText(m["hero"] or m["category"], Qt.ElideRight, int(right - tx - 6))
        p.setPen(QColor(C["accent"]))
        p.drawText(QRectF(tx, by, right - tx, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, label)

        p.setPen(QPen(QColor(C["accent"] if hover else C["border"]), 1.2 if hover else 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(card)
        p.restore()


# ---- janela principal ---------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Deadlock Mods")
        self.resize(1320, 860)
        self.store = Store()
        self.page = 1
        self.loading = False
        self.complete = False
        self.query_id = 0
        self.downloading = False
        self.queue = []
        self.previews = Previews(self.store, run)
        # vários retratos ficando prontos em sequência = uma sincronização só
        self._icons_timer = QTimer(self, singleShot=True, interval=1500)
        self._icons_timer.timeout.connect(self._reapply_for_icons)
        self.hero_icons = self.store.state.setdefault("hero_icons", {})   # herói -> url do ícone (GameBanana)
        self.hero_alias = {}   # sub-subcategoria (ex.: "Faithful") -> herói ("Yamato")
        self.game_heroes = self.store.state.get("game_heroes", [])   # heróis jogáveis, lidos dos arquivos do jogo

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        header = QFrame()
        header.setObjectName("header")
        top = QHBoxLayout(header)
        top.setContentsMargins(20, 12, 20, 12)
        top.setSpacing(14)
        logo = themed(QLabel(), lambda w: w.setPixmap(logo_pixmap(34)))
        brand = themed(QLabel(), lambda w: w.setText(f'DEADLOCK <span style="color:{C["accent"]}">MODS</span>'))
        brand.setObjectName("brand")
        top.addWidget(logo)
        top.addWidget(brand)
        top.addSpacing(18)
        seg = QFrame()
        seg.setObjectName("seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(4)
        self.tab_mine = TabButton("Meus mods", "library")
        self.tab_browse = TabButton("Explorar", "compass")
        sl.addWidget(self.tab_mine)
        sl.addWidget(self.tab_browse)
        self.tab_mine.clicked.connect(lambda: self.show_tab(0))
        self.tab_browse.clicked.connect(lambda: self.show_tab(1))
        top.addWidget(seg)
        top.addStretch()
        self.status = QLabel()
        self.status.setObjectName("status")
        top.addWidget(self.status, 0, Qt.AlignVCenter)
        play = button("Jogar", "play", "play", "on_accent")
        play.setIconSize(QSize(15, 15))
        play.clicked.connect(self.play)
        top.addWidget(play)
        root.addWidget(header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self.build_installed())
        self.stack.addWidget(self.build_browse())

        self.footer = QFrame()
        self.footer.setObjectName("footer")
        bottom = QHBoxLayout(self.footer)
        bottom.setContentsMargins(20, 10, 20, 10)
        bottom.setSpacing(12)
        self.dl_icon = icon_label("download", "accent", 16)
        self.dl_label = QLabel()
        self.dl_bar = QProgressBar()
        self.dl_bar.setTextVisible(False)
        self.dl_bar.setFixedWidth(320)
        self.dl_bar.hide()
        bottom.addWidget(self.dl_icon)
        bottom.addWidget(self.dl_label, 1)
        bottom.addWidget(self.dl_bar)
        root.addWidget(self.footer)
        self.footer.hide()
        self._footer_timer = QTimer(self, singleShot=True, interval=8000)
        self._footer_timer.timeout.connect(lambda: self.downloading or self.queue or self.footer.hide())

        self.pop_layer = PopLayer(self)
        self.hero_grid.verticalScrollBar().valueChanged.connect(self.pop_layer.update)

        self.show_tab(0)
        QTimer.singleShot(0, self.startup)
        QTimer.singleShot(5000, lambda: run(updater.check, self.offer_update))

    # ---- início ---------------------------------------------------------------
    def startup(self):
        if not self.store.game:
            QMessageBox.warning(self, "Deadlock não encontrado",
                                "Não achei a pasta do Deadlock. Escolhe a pasta “Deadlock” da Steam.")
            d = QFileDialog.getExistingDirectory(self, "Pasta do Deadlock")
            if d and (Path(d) / "game/citadel/gameinfo.gi").exists():
                self.store.state["game_path"] = d
                self.store.save()
            else:
                return
        if self.store.dmm_available():
            try:
                names = self.store.migrate_from_dmm()
                if names:
                    QMessageBox.information(self, "Mods importados",
                                            "Importei do gerenciador antigo:\n• " + "\n• ".join(names))
            except Exception as e:
                traceback.print_exc()
                QMessageBox.warning(self, "Importação", f"Não consegui importar do app antigo: {e}")
        self.apply(quiet=True)
        self.refresh_installed()
        run(self.read_game_heroes, self.set_game_heroes, lambda e: None)
        self.check_updates()
        self.load_categories()
        self.reload_browse()

    def read_game_heroes(self):
        from .s2 import heroes
        from .s2.vpk import VPK
        return heroes.Game(str(self.store.game / "game/citadel"), VPK).released()

    def set_game_heroes(self, names):
        if names == self.game_heroes:
            return
        self.game_heroes = self.store.state["game_heroes"] = names
        self.store.save()
        if self.store.state.get("installed_tab", "skins") == "skins":
            self.refresh_installed()

    def grid_viewport_rect(self):
        """Área visível da grade de heróis, em coordenadas da janela: o card em pop só aparece dentro
        dela (o personagem que sai por cima pode cobrir qualquer coisa)."""
        vp = self.hero_grid.viewport()
        return QRectF(vp.mapTo(self, QPointF(0, 0).toPoint()), QSizeF(vp.size()))

    def canon_hero(self, name):
        """Nome do jogo -> o do GameBanana quando for o mesmo herói ("The Doorman" -> "Doorman")."""
        key = name.lower().removeprefix("the ")
        return next((h for h in self.hero_icons if h.lower().removeprefix("the ") == key), name)

    def browse_hero(self, hero):
        """Explorar filtrado nas skins desse herói."""
        self.show_tab(1)
        for i in range(self.hero.count()):
            d = self.hero.itemData(i)
            if d and d.get("name", "").lower() == hero.lower():
                self.hero.setCurrentIndex(i)   # on_hero troca a categoria pra Skins e recarrega
                return

    def show_tab(self, i):
        self.stack.setCurrentIndex(i)
        self.tab_mine.setChecked(i == 0)
        self.tab_browse.setChecked(i == 1)

    def set_status(self, text):
        self.status.setText(text)

    def set_footer(self, text):
        self.dl_label.setText(text)
        self.footer.show()
        self._footer_timer.start()

    def apply(self, quiet=False):
        try:
            n = self.store.sync()
            fixed = getattr(self.store, "last_camfix", [])
            extra = f" · câmera corrigida em {len(fixed)} mod(s)" if fixed else ""
            self.set_status(f"{n} arquivo(s) de mod carregados no jogo{extra}")
            self._render_missing_icons()
        except Exception as e:
            traceback.print_exc()
            self.set_status(f"⚠ {e}")
            # na abertura só o "jogo aberto" fica quieto; outro erro ali passava despercebido na barra de status
            if not quiet or not game_running():
                QMessageBox.warning(self, "Não deu pra aplicar os mods", str(e))

    def _render_missing_icons(self):
        """Skins sem retrato 3D ainda não ganharam ícones: renderiza e aplica de novo quando ficar pronto."""
        asked = self.__dict__.setdefault("_icons_asked", set())   # uma vez por skin por sessão (sem laço se falhar)
        for hero, mod in getattr(self.store, "icons_missing", []):
            key = (hero, mod["id"], tuple(self.previews.vpk_paths(mod)))
            if key in asked:
                continue
            asked.add(key)
            self.previews.request(hero, mod, lambda *_: self._icons_timer.start())

    def _reapply_for_icons(self):
        if not game_running():
            self.apply(quiet=True)

    # ---- atualização pelo GitHub ---------------------------------------------
    def offer_update(self, info):
        if not info:
            return
        notes = info["notes"][:900] + ("…" if len(info["notes"]) > 900 else "")
        box = QMessageBox(self)
        box.setWindowTitle("Atualização disponível")
        box.setText(f"<b>Deadlock Mods {info['version']}</b> está disponível (você tem a {updater.__version__}).")
        if notes:
            box.setInformativeText(notes)
        if info["mode"] == "zip" or not info["asset"]:   # portátil: não dá pra trocar sozinho
            box.setStandardButtons(QMessageBox.Open | QMessageBox.Cancel)
            box.button(QMessageBox.Open).setText("Abrir página")
            box.button(QMessageBox.Cancel).setText("Depois")
            if box.exec() == QMessageBox.Open:
                QDesktopServices.openUrl(QUrl(info["url"]))
            return
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText("Atualizar agora")
        box.button(QMessageBox.Cancel).setText("Depois")
        if box.exec() != QMessageBox.Yes:
            return
        self.footer.show()
        self.dl_bar.show()
        self.dl_bar.setValue(0)
        prog = Progress()
        prog.changed.connect(lambda pct, txt: (self.dl_bar.setValue(pct), self.dl_label.setText(txt)))
        self._update_prog = prog

        def cb(done, total):
            if total:
                prog.changed.emit(int(done * 100 / total), f"baixando atualização {info['version']} ({human(done)} de {human(total)})")

        def done(path):
            self.dl_bar.hide()
            try:
                updater.install_and_restart(info, path)
            except Exception as e:
                QMessageBox.warning(self, "Não deu pra atualizar", str(e))
                return
            QApplication.quit()

        def failed(err):
            self.dl_bar.hide()
            self.set_footer(f"⚠ atualização falhou: {err}")
        run(lambda: updater.download(info, cb), done, failed)

    def play(self):
        if not game_running():
            self.apply()
        QDesktopServices.openUrl(QUrl(f"steam://rungameid/{STEAM_APPID}"))

    # ---- aba Meus mods -------------------------------------------------------
    def build_installed(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 20, 12)
        lay.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        # Sub-abas: skins separadas do resto (a prioridade continua sendo uma lista só).
        seg = QFrame()
        seg.setObjectName("seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(4)
        self.kind_tabs = {"skins": TabButton("Skins", "shirt"), "mods": TabButton("Mods", "package")}
        for kind, b in self.kind_tabs.items():
            b.clicked.connect(lambda _=False, kind=kind: self.show_kind(kind))
            sl.addWidget(b)
        bar.addWidget(seg, 0, Qt.AlignVCenter)
        bar.addStretch()
        for text, name, fn in (("Instalar arquivo…", "file-plus", self.install_file),
                               ("Verificar atualizações", "refresh", self.check_updates),
                               ("Pasta addons", "folder", self.open_addons)):
            b = button(text, name)
            b.clicked.connect(fn)
            bar.addWidget(b, 0, Qt.AlignVCenter)
        lay.addLayout(bar)
        self.inst_hint = QLabel("Arraste pra mudar a prioridade — o de cima ganha quando dois mods mexem na mesma coisa.")
        self.inst_hint.setObjectName("muted")
        lay.addWidget(self.inst_hint)
        self.installed = QListWidget()
        self.installed.setObjectName("installed")
        self.installed.setDragDropMode(QAbstractItemView.InternalMove)
        self.installed.setSelectionMode(QAbstractItemView.SingleSelection)
        self.installed.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.installed.setSpacing(4)
        self.installed.model().rowsMoved.connect(self.on_reorder)
        lay.addWidget(self.installed)
        self.hero_grid = CardFlow(210, 0, spacing=24, ratio=HeroCard.RATIO, margin=20)
        self.hero_grid.hide()
        lay.addWidget(self.hero_grid)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignCenter)
        el.setSpacing(10)
        ico = icon_label("package", "border2", 64)
        et = self.empty_title = QLabel()
        et.setObjectName("h2")
        es = QLabel("Dá uma olhada no Explorar e escolhe um pra começar.")
        es.setObjectName("muted")
        go = button("Explorar mods", "compass", "primary", "on_accent")
        go.clicked.connect(lambda: self.show_tab(1))
        for wdg in (ico, et, es, go):
            el.addWidget(wdg, 0, Qt.AlignHCenter)
        lay.addWidget(self.empty, 1)
        return w

    def hero_of(self, mod):
        h = mod.get("hero") or ""
        return self.hero_alias.get(h, h) or "Outros"

    def keep_one_skin(self, entry):
        """Skin recém-instalada e ativa vira a única ativa do personagem (as outras dele são desligadas)."""
        if self.kind_of(entry) != "skins" or not entry.get("enabled"):
            return
        hero = self.hero_of(entry)
        for m in self.store.mods:
            if m["id"] != entry["id"] and m["enabled"] and self.kind_of(m) == "skins" and self.hero_of(m) == hero:
                m["enabled"] = False
        self.store.save()

    def edit_vpks(self, mod, parent=None):
        d = VpksDialog(parent or self, mod)
        if d.exec():
            for v, st in zip(mod["vpks"], d.states()):
                v["enabled"] = st
            self.store.save()
            self.apply()
            self.refresh_installed()

    def remove_mod(self, mod, parent=None):
        if QMessageBox.question(parent or self, "Remover mod", f"Remover “{mod['name']}”?") != QMessageBox.Yes:
            return False
        self.store.remove(mod["id"])
        self.apply()
        self.refresh_installed()
        self.browse_mark_installed()
        return True

    def open_picker(self, hero):
        # Não modal: um diálogo modal bloqueava a janela de detalhes aberta a partir dele (parecia travado).
        old = getattr(self, "_picker", None)
        if old is not None:
            try:
                old.close()
            except RuntimeError:
                pass
        self._picker = SkinPicker(self, hero)
        self._picker.setAttribute(Qt.WA_DeleteOnClose)
        self._picker.show()

    @staticmethod
    def kind_of(mod):
        return "skins" if mod.get("category") == "Skins" else "mods"

    def show_kind(self, kind):
        self.store.state["installed_tab"] = kind
        self.store.save()
        self.refresh_installed()

    def refresh_installed(self):
        """Agenda a reconstrução da aba (várias chamadas seguidas viram uma só: cada uma recria ~45 cards)."""
        if not getattr(self, "_refresh_pending", False):
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh_installed_now)

    def _refresh_installed_now(self):
        self._refresh_pending = False
        kind = self.store.state.get("installed_tab", "skins")
        for k, b in self.kind_tabs.items():
            b.setChecked(k == kind)
            b.set_count(sum(self.kind_of(m) == k for m in self.store.mods))
        shown = [m for m in self.store.mods if self.kind_of(m) == kind]
        self.empty_title.setText("Nenhuma skin instalada ainda" if kind == "skins" else "Nenhum mod instalado ainda")
        self.installed.clear()
        by_hero = {}
        if kind == "skins":
            for m in shown:
                by_hero.setdefault(self.hero_of(m), []).append(m)
            for h in self.game_heroes:   # todo herói do jogo aparece, mesmo sem skin
                by_hero.setdefault(self.canon_hero(h), [])
            cards = []
            # primeiro quem tem skin instalada, depois por nome
            for hero in sorted(by_hero, key=lambda h: (not by_hero[h], h == "Outros", h.lower())):
                c = HeroCard(hero, by_hero[hero], self.hero_icons.get(hero), self.previews)
                c.viewport_rect = self.grid_viewport_rect
                if by_hero[hero]:
                    c.clicked.connect(lambda hero=hero: self.open_picker(hero))
                else:
                    c.clicked.connect(lambda hero=hero: self.browse_hero(hero))
                cards.append(c)
            self.hero_grid.set_cards(cards)
            shown_list = []
        else:
            self.hero_grid.set_cards([])
            shown_list = shown
        self.hero_grid.setVisible(bool(by_hero))
        self.inst_hint.setText("Clique num personagem pra trocar a skin dele." if kind == "skins" else
                               "Arraste pra mudar a prioridade — o de cima ganha quando dois mods mexem na mesma coisa.")
        for i, m in enumerate(shown_list):
            it = QListWidgetItem()
            it.setData(Qt.UserRole, m["id"])
            row = ModRow(self, m)
            row.pos_lbl.setText(str(i + 1))
            it.setSizeHint(QSize(0, 104))
            self.installed.addItem(it)
            self.installed.setItemWidget(it, row)
        self.empty.setVisible(not shown and not by_hero)
        self.installed.setVisible(bool(shown_list))
        self.inst_hint.setVisible(bool(by_hero) or len(shown_list) > 1)
        self.tab_mine.set_count(len(self.store.mods))

    def on_reorder(self, *_):
        # Só a aba visível foi reordenada: os mods dela trocam entre si nas mesmas posições da lista
        # geral e os da outra aba ficam onde estavam.
        shown = [self.installed.item(i).data(Qt.UserRole) for i in range(self.installed.count())]
        in_tab, it = set(shown), iter(shown)
        self.store.move([next(it) if m["id"] in in_tab else m["id"] for m in self.store.mods])
        self.apply()
        QTimer.singleShot(0, self.refresh_installed)

    def open_addons(self):
        if self.store.addons:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.addons)))

    def install_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Instalar mod de um arquivo", str(Path.home() / "Downloads"),
                                              "Mods (*.zip *.rar *.7z *.vpk)")
        if not path:
            return
        box = QMessageBox(QMessageBox.Question, "Instalar mod", f"Instalar “{Path(path).name}”?",
                          QMessageBox.Ok | QMessageBox.Cancel, self)
        fov = QCheckBox("Aplicar FOV FIX (corrige a câmera travada de skins antigas)")
        fov.setChecked(True)
        box.setCheckBox(fov)
        if box.exec() != QMessageBox.Ok:
            return
        camfix = fov.isChecked()

        def done(entry):
            self.keep_one_skin(entry)
            self.apply()
            self.store.state["installed_tab"] = self.kind_of(entry)
            self.refresh_installed()
        run(lambda: self.store.install_local(path, camfix), done, lambda e: QMessageBox.warning(self, "Erro", e))

    def check_updates(self):
        remote = [m for m in self.store.mods if m.get("remote")]
        if not remote:
            return
        self.set_status("verificando atualizações…")

        def work():
            found = []
            for m in remote:
                try:
                    info = gb.details(m["id"])
                except Exception:
                    continue
                remote_ids = {f["file_id"] for f in info["files"]}
                mine = {f["file_id"] for f in m["files"]}
                # Autor trocou o arquivo que a gente tem (id sumiu) = tem versão nova.
                m["update"] = bool(mine) and not mine <= remote_ids
                if not m.get("thumb"):
                    m["thumb"] = info["thumb"]
                if m["name"] == f"Mod {m['id']}":   # importado sem metadados do app antigo
                    m.update({k: info[k] for k in ("name", "author", "category", "hero")})
                if m["update"]:
                    found.append(m["name"])
            return found

        def done(found):
            self.store.save()
            self.refresh_installed()
            self.set_status(f"{len(found)} atualização(ões) disponível(is)" if found else "tudo atualizado ✓")
        run(work, done, lambda e: self.set_status(f"⚠ {e}"))

    # ---- aba Explorar --------------------------------------------------------
    def build_browse(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 18, 13, 8)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 7, 0)
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Pesquisar mods… (ou cole um link do GameBanana)")
        themed(self.search.addAction(QIcon(), QLineEdit.LeadingPosition),
               lambda a: a.setIcon(icon("search", C["muted"])))
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.on_search)
        self.search.textChanged.connect(lambda t: not t and self.reload_browse())
        self.sort = QComboBox()
        for label, key in gb.SORTS.items():
            self.sort.addItem(label, key)
        self.search_sort = QComboBox()
        for label, key in gb.SEARCH_SORTS.items():
            self.search_sort.addItem(label, key)
        self.search_sort.hide()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.sort)
        bar.addWidget(self.search_sort)
        lay.addLayout(bar)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 7, 0)
        filters.setSpacing(8)
        self.cat = QComboBox()
        self.cat.addItem("Todas as categorias", None)
        self.hero = QComboBox()
        self.hero.addItem("Todos os heróis", None)
        self.hero.setMinimumContentsLength(14)
        self.subcat = QComboBox()
        self.subcat.addItem("Todas as subcategorias", None)
        self.subcat.hide()
        # Chips liga/desliga (mesma API de checkbox: isChecked/setChecked/toggled).
        self.only_featured = button("Destaques", "star", "chip", "accent")
        self.hide_nsfw = button("Esconder +18", None, "chip")
        self.hide_obsolete = button("Esconder obsoletos", None, "chip")
        self.hide_installed = button("Esconder instalados", None, "chip")
        f = self.store.state.setdefault("filters", {})
        for box, key in ((self.only_featured, "featured"), (self.hide_nsfw, "hide_nsfw"),
                         (self.hide_obsolete, "hide_obsolete"), (self.hide_installed, "hide_installed")):
            box.setCheckable(True)
            box.setChecked(f.get(key, False))
            box.toggled.connect(lambda val, key=key: self.on_filter_toggle(key, val))
        self.cat.currentIndexChanged.connect(self.on_category)
        self.hero.currentIndexChanged.connect(self.on_hero)
        self.subcat.currentIndexChanged.connect(lambda _: self.reload_browse())
        self.sort.currentIndexChanged.connect(lambda _: self.reload_browse())
        self.search_sort.currentIndexChanged.connect(lambda _: self.reload_browse())
        clear = button("Limpar", "x", "ghost", "muted")
        clear.clicked.connect(self.clear_filters)
        for wdg in (self.cat, self.hero, self.subcat):
            filters.addWidget(wdg)
        filters.addSpacing(6)
        for wdg in (self.only_featured, self.hide_nsfw, self.hide_obsolete, self.hide_installed):
            filters.addWidget(wdg)
        filters.addStretch()
        filters.addWidget(clear)
        lay.addLayout(filters)

        self.grid = CardGrid()
        self.grid.itemClicked.connect(lambda it: self.open_details(it.data(Qt.UserRole)["id"]))
        self.grid.verticalScrollBar().valueChanged.connect(self.on_scroll)
        lay.addWidget(self.grid)
        self.browse_info = QLabel()
        self.browse_info.setObjectName("muted")
        lay.addWidget(self.browse_info)
        return w

    def load_categories(self):
        def done(cats):
            self.cat.blockSignals(True)
            for c in cats:
                self.cat.addItem(f"{c['name']} ({c['count']})", c)
            self.cat.blockSignals(False)
            # Só Skins é dividida por herói no GameBanana; a lista de heróis vem dela.
            self.skins = next((c for c in cats if c["name"] == "Skins"), None)
            if self.skins:
                run(lambda: gb.categories(self.skins["id"]), self.fill_heroes, lambda e: None)
        run(gb.categories, done, lambda e: None)

    def fill_heroes(self, heroes):
        self.hero.blockSignals(True)
        for h in heroes:
            h["names"] = {h["name"]}
            self.hero_icons[h["name"]] = h.get("icon")
            self.hero.addItem(f"{h['name']} ({h['count']})", h)
            # Alguns heróis têm sub-subcategorias (ex.: Yamato › Faithful/Vanilla+); a busca
            # por texto devolve o nome mais fundo, então guardo esses nomes também.
            if h["children"]:
                run(lambda h=h: gb.categories(h["id"]), lambda subs, h=h: self.add_hero_subs(h, subs), lambda e: None)
        self.hero.blockSignals(False)
        self.store.save()
        if self.store.state.get("installed_tab", "skins") == "skins":
            self.refresh_installed()   # agora com ícones dos heróis

    def add_hero_subs(self, h, subs):
        h["names"].update(x["name"] for x in subs)
        known = any(x["name"] in {m.get("hero") for m in self.store.mods} for x in subs)
        for x in subs:
            self.hero_alias[x["name"]] = h["name"]
        if known:
            self.refresh_installed()

    def on_filter_toggle(self, key, val):
        self.store.state.setdefault("filters", {})[key] = val
        self.store.save()
        self.reload_browse()

    def clear_filters(self):
        for wdg in (self.cat, self.hero, self.subcat):
            wdg.blockSignals(True)
            wdg.setCurrentIndex(0)
            wdg.blockSignals(False)
        self.subcat.hide()
        for box in (self.only_featured, self.hide_nsfw, self.hide_obsolete, self.hide_installed):
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        self.store.state["filters"] = {}
        self.store.save()
        self.reload_browse()

    def on_hero(self, _):
        # Escolher herói implica categoria Skins.
        if self.hero.currentData() and getattr(self, "skins", None):
            idx = next(i for i in range(self.cat.count()) if (self.cat.itemData(i) or {}).get("id") == self.skins["id"])
            if self.cat.currentIndex() != idx:
                self.cat.blockSignals(True)
                self.cat.setCurrentIndex(idx)
                self.cat.blockSignals(False)
                self.subcat.hide()
        self.reload_browse()

    def on_category(self, _):
        c = self.cat.currentData()
        is_skins = bool(c and getattr(self, "skins", None) and c["id"] == self.skins["id"])
        if not is_skins and self.hero.currentIndex():
            self.hero.blockSignals(True)
            self.hero.setCurrentIndex(0)
            self.hero.blockSignals(False)
        self.subcat.blockSignals(True)
        self.subcat.clear()
        self.subcat.addItem("Todas as subcategorias", None)
        self.subcat.blockSignals(False)
        show_sub = bool(c and c["children"]) and not is_skins
        self.subcat.setVisible(show_sub)
        if show_sub:
            def done(subs):
                self.subcat.blockSignals(True)
                for sc in subs:
                    self.subcat.addItem(f"{sc['name']} ({sc['count']})", sc)
                self.subcat.blockSignals(False)
            run(lambda: gb.categories(c["id"]), done, lambda e: None)
        self.reload_browse()

    def on_search(self):
        text = self.search.text().strip()
        m = gb.MOD_URL.search(text)
        if m:
            self.open_details(m.group(1))
            return
        self.reload_browse()

    def reload_browse(self):
        self.query_id += 1
        self.page = 1
        self.complete = False
        self.loading = False
        self.fetched = 0
        self.grid.clear()
        self.load_page()

    def passes(self, m, searching):
        """Filtros feitos do nosso lado (a busca por texto do site ignora categoria/herói)."""
        if self.hide_nsfw.isChecked() and m["nsfw"]:
            return False
        if self.hide_obsolete.isChecked() and m["obsolete"]:
            return False
        if self.hide_installed.isChecked() and self.store.get(m["id"]):
            return False
        if searching:
            cat, hero, sub = self.cat.currentData(), self.hero.currentData(), self.subcat.currentData()
            if cat and m["category"] != cat["name"]:
                return False
            if hero and m["hero"] not in hero.get("names", {hero["name"]}):
                return False
            if sub and m["hero"] != sub["name"]:
                return False
            if self.only_featured.isChecked() and not m["featured"]:
                return False
        return True

    def load_page(self):
        if self.loading or self.complete:
            return
        self.loading = True
        qid, page = self.query_id, self.page
        text = self.search.text().strip()
        searching = bool(text) and not gb.MOD_URL.search(text)
        self.sort.setVisible(not searching)
        self.search_sort.setVisible(searching)
        cat = ((self.hero.currentData() or self.subcat.currentData() or self.cat.currentData()) or {}).get("id")
        sort, order = self.sort.currentData(), self.search_sort.currentData()
        featured = self.only_featured.isChecked()
        self.browse_info.setText("carregando…")

        def fetch():
            return gb.search(text, page, order) if searching else gb.browse(page, sort, cat, featured)

        def done(res):
            if qid != self.query_id:
                return
            mods, total, complete = res
            self.loading = False
            self.page += 1
            self.fetched += len(mods)
            self.complete = complete or not mods
            for m in mods:
                if self.passes(m, searching):
                    self.add_card(m)
            hidden = self.fetched - self.grid.count()
            info = f"{self.grid.count()} mods mostrados · {self.fetched} de {total} carregados"
            if hidden:
                info += f" · {hidden} escondidos pelos filtros"
            self.browse_info.setText(info)
            # Tela ainda vazia (ou filtros escondendo muita coisa): busca mais, com limite.
            if not self.complete and self.grid.count() < 60 and self.page <= 20:
                QTimer.singleShot(0, self.load_page)

        def fail(msg):
            if qid == self.query_id:
                self.loading = False
                self.browse_info.setText(f"⚠ erro: {msg}")
        run(fetch, done, fail)

    def add_card(self, m):
        it = QListWidgetItem(m["name"])
        it.setData(Qt.UserRole, m)
        it.setData(ROLE_INSTALLED, bool(self.store.get(m["id"])))
        it.setToolTip(f"{m['name']}\npor {m['author']}")
        self.grid.addItem(it)
        load_image(m["thumb"], lambda img, it=it: it.setData(ROLE_IMG, img))

    def browse_mark_installed(self):
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            it.setData(ROLE_INSTALLED, bool(self.store.get(it.data(Qt.UserRole)["id"])))

    def on_scroll(self, val):
        sb = self.grid.verticalScrollBar()
        if val > sb.maximum() - 400:
            self.load_page()

    # ---- detalhes e downloads ------------------------------------------------
    def open_details(self, mod_id, parent=None):
        # Uma janela de detalhes por vez: abrir outro mod substitui a que estava aberta.
        old = getattr(self, "_details", None)
        if old is not None:
            try:
                old.close()
            except RuntimeError:   # já destruída
                pass
        d = DetailsDialog(parent or self, mod_id, self.store.get(mod_id))
        d.setAttribute(Qt.WA_DeleteOnClose)
        d.install_requested.connect(self.enqueue)
        self._details = d
        d.show()

    def enqueue(self, info, files):
        self.queue.append((info, files))
        self.set_footer(f"na fila: {info['name']}")
        self.next_download()

    def next_download(self):
        if self.downloading or not self.queue:
            return
        self.downloading = True
        info, files = self.queue.pop(0)
        self.footer.show()
        self.dl_bar.show()
        self.dl_bar.setValue(0)
        total_all = sum(f["size"] for f in files) or 1

        prog = Progress()
        prog.changed.connect(lambda pct, txt: (self.dl_bar.setValue(pct), self.dl_label.setText(txt)))
        self._prog = prog

        def work():
            got, base = [], 0
            for f in files:
                dest = DOWNLOADS / f"{f['file_id']}_{f['name']}"

                def cb(done, total, f=f, base=base):
                    prog.changed.emit(int((base + done) * 100 / total_all),
                                      f"baixando {info['name']} — {f['name']} ({human(base + done)} de {human(total_all)})")
                net.download(f["url"], dest, f.get("md5"), cb)
                base += f["size"]
                got.append((f, dest))
            prog.changed.emit(100, f"instalando {info['name']}…")
            try:
                entry = self.store.install(info, got)
            finally:
                for _, p in got:
                    p.unlink(missing_ok=True)
            return entry

        def done(entry):
            self.downloading = False
            self.dl_bar.hide()
            self.set_footer(f"✓ {entry['name']} instalado")
            self.keep_one_skin(entry)
            self.apply()
            self.store.state["installed_tab"] = self.kind_of(entry)
            self.refresh_installed()
            self.browse_mark_installed()
            if len(entry["vpks"]) > 1:
                self.edit_vpks(entry)
            self.next_download()

        def fail(msg):
            self.downloading = False
            self.dl_bar.hide()
            self.set_footer(f"⚠ falhou: {info['name']}")
            QMessageBox.warning(self, "Download falhou", f"{info['name']}:\n{msg}")
            self.next_download()
        run(work, done, fail)


STYLE = """
* { font-size: 10.5pt; }
QMainWindow, QDialog { background: @bg; }
QWidget { color: @text; }
QToolTip { background: @raised; color: @text; border: 1px solid @border2; border-radius: 6px; padding: 5px 8px; }

QFrame#header { background: @surface; border-bottom: 1px solid @border; }
QLabel#brand { font-size: 14pt; font-weight: 800; letter-spacing: 2px; }
QFrame#seg { background: @bg; border: 1px solid @border; border-radius: 11px; }
QPushButton#tab { background: transparent; border: none; border-radius: 8px; padding: 0; }
QPushButton#tab:hover { background: @surface2; }
QPushButton#tab:checked { background: @raised; }
QLabel#count { background: @accent_bg; color: @accent; border-radius: 9px; padding: 0 7px; font-size: 9pt; font-weight: 700; min-height: 18px; }
QLabel#status { color: @muted; background: @bg; border: 1px solid @border; border-radius: 13px; padding: 4px 12px; font-size: 9.5pt; }
QPushButton#play { background: @accent; color: @on_accent; border: none; border-radius: 10px; padding: 9px 22px; font-size: 11pt; font-weight: 800; letter-spacing: 1px; }
QPushButton#play:hover { background: @accent2; }
QFrame#footer { background: @surface; border-top: 1px solid @border; }

QLabel#h1 { font-size: 17pt; font-weight: 700; }
QLabel#h2 { font-size: 14pt; font-weight: 700; }
QLabel#muted { color: @muted; }
QLabel#caption { color: @muted; font-size: 9pt; }
QLabel#section { color: @muted; font-size: 9pt; font-weight: 700; letter-spacing: 1.5px; }
QLabel#crumb { color: @accent; font-size: 9pt; font-weight: 700; letter-spacing: 1.2px; }
QLabel#update { color: @accent2; font-weight: 600; }
QLabel#image { background: @raised; border-radius: 8px; }
QLabel#prio { background: @accent_bg; color: @accent; border-radius: 14px; font-weight: 800; }
QLabel#rowtitle { font-size: 11.5pt; font-weight: 700; }
QLabel#chip { background: @raised; color: @muted; border-radius: 9px; padding: 2px 9px; font-size: 9pt; font-weight: 600; }
QLabel#chipaccent { background: @accent_bg; color: @accent; border-radius: 9px; padding: 2px 9px; font-size: 9pt; font-weight: 600; }
QLabel#statval { font-size: 13pt; font-weight: 700; }
QFrame#side { background: @surface; border: 1px solid @border; border-radius: 12px; }
QFrame#stat { background: @bg; border: 1px solid @border; border-radius: 9px; }

QFrame#row { background: @surface; border: 1px solid @border; border-radius: 12px; }
QFrame#row:hover { border-color: @border2; background: @surface2; }

QListWidget { background: transparent; border: none; outline: none; }
QScrollArea { background: transparent; border: none; }
QWidget#flow { background: transparent; }
QListWidget#installed::item, QListWidget#installed::item:selected, QListWidget#installed::item:hover { background: transparent; }
QListWidget#files::item { background: @bg; border: 1px solid @border; border-radius: 9px; padding: 10px 8px; margin: 1px 0; }
QListWidget#files::item:hover { border-color: @border2; }
QListWidget#files::item:checked { border-color: @accent; }
QListWidget#files::item:selected { color: @text; }

QPushButton { background: @raised; border: 1px solid @border; border-radius: 8px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: @hover; border-color: @border2; }
QPushButton:pressed { background: @surface2; }
QPushButton:disabled { color: @disabled; background: @surface2; }
QPushButton#primary { background: @accent; color: @on_accent; border: none; font-weight: 700; }
QPushButton#primary:hover { background: @accent2; }
QPushButton#primary:disabled { background: @accent_dim; color: @surface; }
QPushButton#icon { padding: 0; border-radius: 9px; }
QPushButton#danger { padding: 0; border-radius: 9px; }
QPushButton#danger:hover { background: @danger_bg; border-color: @danger_border; }
QPushButton#round { padding: 0; border-radius: 18px; background: @bg; border: 1px solid @border2; }
QPushButton#round:hover { background: @raised; border-color: @accent; }
QPushButton#ghost { background: transparent; border: none; color: @muted; }
QPushButton#ghost:hover { color: @text; background: @surface2; }
QPushButton#chip { background: @surface; border: 1px solid @border; border-radius: 15px; padding: 6px 14px; color: @muted; }
QPushButton#chip:hover { border-color: @border2; color: @text; }
QPushButton#chip:checked { background: @accent_bg; border-color: @accent; color: @accent; }

QLineEdit, QComboBox { background: @surface; border: 1px solid @border; border-radius: 9px; padding: 7px 10px; }
QLineEdit:hover, QComboBox:hover { border-color: @border2; }
QLineEdit:focus { border-color: @accent; }
QLineEdit#search { padding: 9px 10px; font-size: 11pt; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url(@chevron); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: @surface2; border: 1px solid @border2; border-radius: 8px; padding: 4px; outline: none; selection-background-color: @accent_bg; selection-color: @accent; }
QTextBrowser { background: @surface; border: 1px solid @border; border-radius: 12px; padding: 10px 12px; }
QProgressBar { background: @bg; border: none; border-radius: 4px; max-height: 8px; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 @accent_deep, stop:1 @accent2); border-radius: 4px; }
QListWidget::indicator { width: 18px; height: 18px; border: 1px solid @border2; border-radius: 5px; background: @raised; }
QListWidget::indicator:checked { background: @accent; border-color: @accent; image: url(@check); }
QListWidget::indicator:hover { border-color: @accent; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @scroll; border-radius: 3px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: @scroll_hover; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @scroll; border-radius: 3px; min-width: 40px; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; border: none; width: 0; height: 0; }
"""


def _stylesheet():
    # Ícones usados pelo QSS precisam existir como arquivo (nome com a cor, senão o Qt usa o cache antigo).
    d = CACHE / "ui"
    d.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, color in (("down", C["muted"]), ("check", C["on_accent"])):
        path = d / f"{name}-{color[1:]}.png"
        if not path.exists():
            icon_pixmap(name, color, 16, 2.0).save(str(path))
        files[name] = path.as_posix()
    css = STYLE.replace("@chevron", files["down"]).replace("@check", files["check"])
    for key in sorted(C, key=len, reverse=True):   # accent2 antes de accent
        css = css.replace("@" + key, C[key])
    return css


class Theme(QObject):
    """Segue a paleta do sistema e reaplica o visual quando ela muda (ex.: Caelestia trocando o wallpaper)."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.colors = None
        self.timer = QTimer(self, singleShot=True, interval=150)
        self.timer.timeout.connect(self.refresh)
        # No Windows a paleta nativa é o cinza/azul padrão: o dourado fica melhor.
        system = None if sys.platform == "win32" else palette_colors(app.palette())
        if system is None:
            # Sem tema do sistema: impõe a paleta dourada (aí não tem mudança pra acompanhar).
            pal = app.palette()
            for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "surface2"),
                              (QPalette.Button, "raised"), (QPalette.ButtonText, "text"), (QPalette.Text, "text"),
                              (QPalette.WindowText, "text"), (QPalette.PlaceholderText, "muted"),
                              (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "on_accent"),
                              (QPalette.Link, "accent")):
                pal.setColor(role, QColor(GOLD.get(key) or C[key]))
            app.setPalette(pal)
        else:
            app.installEventFilter(self)
        self.refresh()

    def eventFilter(self, obj, e):
        if obj is self.app and e.type() in (QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self.timer.start()
        return False

    def refresh(self):
        colors = (None if sys.platform == "win32" else palette_colors(self.app.palette())) or GOLD
        if colors == self.colors:
            return
        first = self.colors is None
        self.colors = colors
        set_theme(colors)
        self.app.setStyleSheet(_stylesheet())
        if not first:
            rerun_themed()
            for w in self.app.allWidgets():
                w.update()


def main():
    if sys.platform == "win32":
        # Ícone próprio na barra de tarefas (senão agrupa como "python").
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeadlockMods.App")
    app = QApplication(sys.argv)
    app.setApplicationName("Deadlock Mods")
    app.setDesktopFileName("deadlock-mods")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "icon.svg")))
    app.theme = Theme(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
