#!/usr/bin/env python3
"""
foto RC — a glass-styled batch photo converter.
Reads: RAW (via rawpy), HEIC/HEIF (via pillow-heif), JPEG, PNG, WebP, AVIF, TIFF, BMP, GIF, JPEG 2000…
Writes: JPEG, PNG, WebP, AVIF, HEIC, JPEG 2000, TIFF, BMP, GIF (whatever your install supports)
"""
import io
import math
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps, features

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except ImportError:
    HAS_HEIF = False

try:
    import rawpy
    HAS_RAW = True
except ImportError:
    HAS_RAW = False

from PySide6.QtCore import (Qt, QTimer, QThread, Signal, Property, QPointF, QRectF, QUrl,
                            QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup)
from PySide6.QtGui import (QPainter, QColor, QLinearGradient, QRadialGradient, QConicalGradient,
                           QGradient, QPainterPath, QPen, QBrush, QPixmap, QImage, QFont,
                           QFontMetrics, QDesktopServices)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
                               QSlider, QComboBox, QListView, QVBoxLayout, QHBoxLayout,
                               QFileDialog, QGraphicsOpacityEffect, QSizeGrip, QMessageBox,
                               QSizePolicy)

APP_NAME = "foto RC"
ACCENT_A, ACCENT_B, ACCENT_C = QColor(255, 214, 10), QColor(198, 244, 50), QColor(46, 232, 111)

# ───────────────────────────── Image engine ─────────────────────────────
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf",
            ".rw2", ".raf", ".pef", ".srw", ".x3f", ".3fr", ".erf", ".kdc", ".mrw", ".iiq", ".rwl"}

Image.init()


def feature_ok(name):
    try:
        return bool(features.check(name))
    except Exception:
        return False


FORMAT_SPECS = [  # label, Pillow format, extension, kind
    ("JPEG", "JPEG", ".jpg", "lossy"),
    ("PNG", "PNG", ".png", "lossless"),
    ("WebP", "WEBP", ".webp", "lossy"),
    ("AVIF", "AVIF", ".avif", "lossy"),
    ("HEIC / HEIF", "HEIF", ".heic", "lossy"),
    ("JPEG 2000", "JPEG2000", ".jp2", "lossy"),
    ("TIFF", "TIFF", ".tif", "lossless"),
    ("BMP", "BMP", ".bmp", "lossless"),
    ("GIF", "GIF", ".gif", "palette"),
]

QUALITY_TIPS = {
    "JPEG": "Higher = sharper & bigger. 80–90 is the sweet spot.",
    "WEBP": "100 switches WebP to fully lossless mode.",
    "AVIF": "AVIF stays gorgeous even at 50–70.",
    "HEIF": "HEIC is ~2× as efficient as JPEG at the same quality.",
    "JPEG2000": "100 = mathematically lossless.",
    "GIF": "Quality sets the palette size (2–256 colors).",
}


def available_formats():
    out = []
    for label, fmt, ext, kind in FORMAT_SPECS:
        if fmt not in Image.SAVE:
            continue
        if fmt == "WEBP" and not feature_ok("webp"):
            continue
        if fmt == "AVIF" and not feature_ok("avif"):
            continue
        if fmt == "JPEG2000" and not feature_ok("jpg_2000"):
            continue
        out.append(dict(label=label, fmt=fmt, ext=ext, kind=kind))
    return out


def input_extensions():
    exts = {e.lower() for e, f in Image.registered_extensions().items() if f in Image.OPEN}
    if HAS_RAW:
        exts |= RAW_EXTS
    return exts


INPUT_EXTS = input_extensions()


def load_image(path, preview=False):
    ext = Path(path).suffix.lower()
    if ext in RAW_EXTS:
        if not HAS_RAW:
            raise RuntimeError("install 'rawpy' to open RAW files")
        with rawpy.imread(path) as raw:
            if preview:  # embedded JPEG thumbnail = instant preview
                try:
                    th = raw.extract_thumb()
                    if th.format == rawpy.ThumbFormat.JPEG:
                        img = Image.open(io.BytesIO(th.data))
                        return ImageOps.exif_transpose(img)
                    if th.format == rawpy.ThumbFormat.BITMAP:
                        return Image.fromarray(th.data)
                except Exception:
                    pass
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        return Image.fromarray(rgb)
    img = Image.open(path)
    if preview:
        img.draft("RGB", (1000, 1000))  # fast JPEG decode for previews
    try:
        img.seek(0)  # first frame of animated files
    except Exception:
        pass
    img.load()
    return ImageOps.exif_transpose(img) or img


def probe_size(path):
    if Path(path).suffix.lower() in RAW_EXTS and HAS_RAW:
        with rawpy.imread(path) as raw:
            s = raw.sizes
            return (s.height, s.width) if s.flip in (5, 6) else (s.width, s.height)
    with Image.open(path) as im:
        w, h = im.size
        try:
            if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                w, h = h, w
        except Exception:
            pass
        return w, h


def normalize_mode(img):
    m = img.mode
    if m in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
        return img.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    if m == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if m == "LA":
        return img.convert("RGBA")
    if m in ("1", "F"):
        return img.convert("L")
    if m not in ("L", "RGB", "RGBA"):
        return img.convert("RGB")
    return img


def adapt_for_format(img, fmt, quality):
    if fmt == "JPEG":
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            return bg
        return img
    if fmt in ("WEBP", "AVIF", "HEIF") and img.mode == "L":
        return img.convert("RGB")
    if fmt == "GIF":
        colors = max(2, round(256 * quality / 100))
        if img.mode == "RGBA":
            return img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
        return img.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                                           dither=Image.Dither.FLOYDSTEINBERG)
    return img


def save_options(fmt, q):
    if fmt == "JPEG":
        return dict(quality=q, optimize=True, progressive=True, subsampling=0 if q >= 90 else 2)
    if fmt == "WEBP":
        return dict(quality=q, method=6, lossless=(q == 100))
    if fmt == "AVIF":
        return dict(quality=q, speed=6)
    if fmt == "HEIF":
        return dict(quality=q)
    if fmt == "JPEG2000":
        if q >= 100:
            return dict(irreversible=False)
        return dict(irreversible=True, quality_mode="rates", quality_layers=[1 + (100 - q) * 0.6])
    if fmt == "PNG":
        return dict(optimize=True)
    if fmt == "TIFF":
        return dict(compression="tiff_adobe_deflate")
    if fmt == "GIF":
        return dict(optimize=True)
    return {}


ICC_OK = {"JPEG", "PNG", "WEBP", "AVIF", "TIFF"}
EXIF_OK = {"JPEG", "PNG", "WEBP", "AVIF", "HEIF"}


def unique_path(folder, stem, ext):
    folder.mkdir(parents=True, exist_ok=True)
    cand, i = folder / f"{stem}{ext}", 2
    while cand.exists():
        cand, i = folder / f"{stem}-{i}{ext}", i + 1
    return cand


def convert_one(src, spec, scale, quality, out_dir):
    fmt = spec["fmt"]
    img = load_image(src)
    icc, exif = img.info.get("icc_profile"), img.info.get("exif")
    img = normalize_mode(img)
    w, h = img.size
    img = img.resize((max(1, round(w * scale / 100)), max(1, round(h * scale / 100))),
                     Image.Resampling.LANCZOS)
    img = adapt_for_format(img, fmt, quality)
    opts = save_options(fmt, quality)
    meta = {}
    if icc and fmt in ICC_OK:
        meta["icc_profile"] = icc
    if exif and isinstance(exif, bytes) and fmt in EXIF_OK:
        meta["exif"] = exif
    folder = Path(out_dir) if out_dir else Path(src).parent
    dest = unique_path(folder, Path(src).stem + "_fotoRC", spec["ext"])
    try:
        img.save(dest, fmt, **opts, **meta)
    except Exception:
        try:  # retry without metadata
            img.save(dest, fmt, **opts)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
    return dest


class ConvertWorker(QThread):
    progress = Signal(int, int, str)
    done = Signal(int, list, str)

    def __init__(self, files, spec, scale, quality, out_dir):
        super().__init__()
        self.files, self.spec, self.scale, self.quality, self.out_dir = files, spec, scale, quality, out_dir

    def run(self):
        ok, errors, last_dir = 0, [], ""
        for i, f in enumerate(self.files, 1):
            if self.isInterruptionRequested():
                break
            try:
                last_dir = str(convert_one(f, self.spec, self.scale, self.quality, self.out_dir).parent)
                ok += 1
            except Exception as e:
                errors.append(f"{Path(f).name}: {e}")
            self.progress.emit(i, len(self.files), Path(f).name)
        self.done.emit(ok, errors, last_dir)


# ───────────────────────────── Glass UI widgets ─────────────────────────────
# Note: the aurora background repaints the window ~30×/s, so time-based
# effects below (shimmer, marching dashes, logo spin) animate for free.

def paint_glass(p, rect, radius, glow=0.0, inset=6):
    r = rect.adjusted(inset, inset, -inset, -inset)
    p.setPen(Qt.NoPen)
    for i in range(inset, 0, -1):  # soft drop shadow
        p.setBrush(QColor(0, 0, 0, 10))
        p.drawRoundedRect(r.adjusted(-i, -i + 2, i, i + 2), radius + i, radius + i)
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    fill = QLinearGradient(r.topLeft(), r.bottomRight())
    fill.setColorAt(0, QColor(255, 255, 255, int(42 + 14 * glow)))
    fill.setColorAt(1, QColor(255, 255, 255, int(12 + 8 * glow)))
    p.fillPath(path, fill)
    sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + min(120, r.height() * 0.5)))
    sheen.setColorAt(0, QColor(255, 255, 255, 38))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(path, sheen)
    border = QLinearGradient(r.topLeft(), r.bottomRight())
    border.setColorAt(0, QColor(255, 255, 255, int(150 + 80 * glow)))
    border.setColorAt(0.5, QColor(255, 255, 255, int(30 + 30 * glow)))
    border.setColorAt(1, QColor(0, 210, 255, int(60 + 120 * glow)))
    p.setPen(QPen(QBrush(border), 1.2))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


class AuroraBackground(QWidget):
    ORBS = [  # color, size, speed-x, speed-y, phase
        (ACCENT_A, 0.55, 0.21, 0.17, 0.0),
        (ACCENT_B, 0.65, 0.13, 0.19, 2.1),
        (ACCENT_C, 0.45, 0.17, 0.11, 4.2),
        (QColor(44, 255, 179), 0.35, 0.09, 0.23, 1.3),
    ]

    def __init__(self):
        super().__init__()
        self.t = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        self.t += 0.033
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 26, 26)
        p.setClipPath(clip)
        base = QLinearGradient(r.topLeft(), r.bottomRight())
        base.setColorAt(0, QColor(12, 10, 32))
        base.setColorAt(0.5, QColor(28, 22, 64))
        base.setColorAt(1, QColor(10, 18, 40))
        p.fillRect(r, base)
        w, h, m = r.width(), r.height(), max(r.width(), r.height())
        for color, size, sx, sy, ph in self.ORBS:
            cx = w * (0.5 + 0.4 * math.sin(self.t * sx + ph))
            cy = h * (0.5 + 0.4 * math.cos(self.t * sy + ph * 1.3))
            g = QRadialGradient(QPointF(cx, cy), m * size * (0.9 + 0.1 * math.sin(self.t * 0.7 + ph)))
            for stop, a in ((0, 150), (0.45, 40), (1, 0)):
                c = QColor(color)
                c.setAlpha(a)
                g.setColorAt(stop, c)
            p.fillRect(r, g)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 26, 26)


class GlassCard(QFrame):
    def __init__(self, radius=22):
        super().__init__()
        self._radius, self._glow = radius, 0.0
        self._anim = QPropertyAnimation(self, b"glow", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get_glow(self):
        return self._glow

    def _set_glow(self, v):
        self._glow = v
        self.update()

    glow = Property(float, _get_glow, _set_glow)

    def animate_glow(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._glow)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self.animate_glow(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.animate_glow(0.0)
        super().leaveEvent(e)

    def paint_extra(self, p):
        pass

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()), self._radius, self._glow)
        self.paint_extra(p)
        p.end()


class DropZone(GlassCard):
    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self._dragging = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 34, 34, 30)
        lay.setSpacing(8)
        self.preview = QLabel("↑")
        self.preview.setObjectName("bigIcon")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(220, 220)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.title = QLabel("Drop photos here")
        self.title.setObjectName("dropTitle")
        self.sub = QLabel("or click to browse · folders work too")
        self.sub.setObjectName("hint")
        for lbl in (self.title, self.sub):
            lbl.setAlignment(Qt.AlignCenter)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.hide()
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.clear_btn)
        row.addStretch()
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addLayout(row)

    def paint_extra(self, p):
        r = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        speed = 40 if self._dragging else 8
        pen = QPen(QColor(255, 255, 255, 210 if self._dragging else int(60 + 70 * self._glow)), 1.6)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([6, 6])
        pen.setDashOffset(-(time.monotonic() * speed) % 12)  # marching ants
        p.setPen(pen)
        p.setBrush(QColor(0, 210, 255, 28) if self._dragging else Qt.NoBrush)
        p.drawRoundedRect(r, 16, 16)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._dragging = True
            self.animate_glow(1.0)

    def dragLeaveEvent(self, e):
        self._dragging = False
        self.animate_glow(0.0)

    def dropEvent(self, e):
        self._dragging = False
        self.filesDropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class GlowButton(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(72)
        self._hover = 0.0
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(220)
        f = self.font()
        f.setPointSize(14)
        f.setBold(True)
        self.setFont(f)

    def _get(self):
        return self._hover

    def _set(self, v):
        self._hover = v
        self.update()

    hover = Property(float, _get, _set)

    def _go(self, v):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(v)
        self._anim.start()

    def enterEvent(self, e):
        self._go(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go(0.0)
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        if self.isDown():
            r.adjust(4, 2, -4, -2)
        rad = r.height() / 2
        on = self.isEnabled()
        p.setPen(Qt.NoPen)
        if on:  # neon halo
            for i in range(9, 0, -1):
                c = QColor(ACCENT_B)
                c.setAlpha(int((5 + 9 * self._hover) * (10 - i) / 9))
                p.setBrush(c)
                p.drawRoundedRect(r.adjusted(-i, -i, i, i), rad + i, rad + i)
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        g = QLinearGradient(r.topLeft(), r.topRight())
        if on:
            k = 100 + int(18 * self._hover)
            g.setColorAt(0, ACCENT_A.lighter(k))
            g.setColorAt(0.5, ACCENT_B.lighter(k))
            g.setColorAt(1, ACCENT_C.lighter(k))
        else:
            g.setColorAt(0, QColor(90, 90, 120))
            g.setColorAt(1, QColor(70, 70, 100))
        p.fillPath(path, g)
        p.save()
        p.setClipPath(path)
        sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.center().y()))
        sheen.setColorAt(0, QColor(255, 255, 255, 95))
        sheen.setColorAt(1, QColor(255, 255, 255, 8))
        p.fillRect(QRectF(r.left(), r.top(), r.width(), r.height() / 2), sheen)
        u = min(1.0, ((time.monotonic() % 3.2) / 3.2) / 0.45)  # sweep, then pause
        x = r.left() - 90 + u * (r.width() + 180)
        band = QLinearGradient(QPointF(x - 50, r.top()), QPointF(x + 50, r.bottom()))
        band.setColorAt(0, QColor(255, 255, 255, 0))
        band.setColorAt(0.5, QColor(255, 255, 255, 120 if on else 40))
        band.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(r, band)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 130), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(QColor(255, 255, 255, 255 if on else 150))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())


class GlassProgress(QWidget):
    def __init__(self):
        super().__init__()
        self._v = 0.0
        self.setFixedHeight(12)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(450)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get(self):
        return self._v

    def _set(self, v):
        self._v = v
        self.update()

    value = Property(float, _get, _set)

    def animate_to(self, v):
        self._anim.stop()
        self._anim.setStartValue(self._v)
        self._anim.setEndValue(v)
        self._anim.start()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = r.height() / 2
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(r, rad, rad)
        if self._v > 0.001:
            fr = QRectF(r.left(), r.top(), max(r.height(), r.width() * self._v), r.height())
            path = QPainterPath()
            path.addRoundedRect(fr, rad, rad)
            g = QLinearGradient(fr.topLeft(), fr.topRight())
            g.setColorAt(0, ACCENT_A)
            g.setColorAt(0.6, ACCENT_B)
            g.setColorAt(1, ACCENT_C)
            p.fillPath(path, g)
            x = fr.left() + ((time.monotonic() * 0.8) % 1.0) * fr.width()
            gloss = QLinearGradient(x - 40, 0, x + 40, 0)
            gloss.setColorAt(0, QColor(255, 255, 255, 0))
            gloss.setColorAt(0.5, QColor(255, 255, 255, 130))
            gloss.setColorAt(1, QColor(255, 255, 255, 0))
            p.save()
            p.setClipPath(path)
            p.fillRect(fr, gloss)
            p.restore()


class LogoMark(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(36, 36)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(4, 4, 28, 28)
        cg = QConicalGradient(r.center(), (time.monotonic() * 90) % 360)
        for s, c in ((0, ACCENT_A), (0.33, ACCENT_B), (0.66, ACCENT_C), (1, ACCENT_A)):
            cg.setColorAt(s, c)
        p.setPen(QPen(QBrush(cg), 4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        lens = QRadialGradient(r.center() - QPointF(3, 3), 12)
        lens.setColorAt(0, QColor(255, 255, 255, 230))
        lens.setColorAt(0.35, QColor(120, 200, 255, 170))
        lens.setColorAt(1, QColor(40, 30, 90, 40))
        p.setPen(Qt.NoPen)
        p.setBrush(lens)
        p.drawEllipse(r.adjusted(7, 7, -7, -7))


class GradientTitle(QWidget):
    def __init__(self, text, size=22):
        super().__init__()
        self.text = text
        self.f = QFont()
        self.f.setPointSize(size)
        self.f.setWeight(QFont.Weight.Black)
        fm = QFontMetrics(self.f)
        self.setFixedSize(fm.horizontalAdvance(text) + 8, fm.height() + 4)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.f)
        path = QPainterPath()
        path.addText(2, fm.ascent() + 1, self.f, self.text)
        w = max(1, self.width())
        off = (time.monotonic() * 40) % w  # flowing gradient
        g = QLinearGradient(off, 0, off + w, 0)
        g.setSpread(QGradient.RepeatSpread)
        for s, c in ((0, ACCENT_A), (0.33, ACCENT_B), (0.66, ACCENT_C), (1, ACCENT_A)):
            g.setColorAt(s, c)
        p.fillPath(path, g)


class TitleBar(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setFixedHeight(50)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(10)
        h.addWidget(LogoMark())
        h.addWidget(GradientTitle("foto resize & convert"))
        sub = QLabel("photo converter made by turtle")
        sub.setObjectName("hint")
        h.addWidget(sub)
        h.addStretch()
        for name, slot, tip in (("min", win.showMinimized, "Minimize"), ("close", win.close, "Close")):
            b = QPushButton()
            b.setObjectName(name)
            b.setFixedSize(14, 14)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            h.addWidget(b)

    def mousePressEvent(self, e):
        handle = self.window().windowHandle()
        if e.button() == Qt.LeftButton and handle:
            handle.startSystemMove()


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignCenter)
        self.setContentsMargins(26, 14, 26, 14)
        self.eff = QGraphicsOpacityEffect(self)
        self.eff.setOpacity(0.0)
        self.setGraphicsEffect(self.eff)
        self.group = None
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, QColor(30, 24, 70, 220))
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.drawPath(path)
        p.end()
        super().paintEvent(e)

    def place(self):
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 110)

    def pop(self, text):
        self.setText(text)
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        if self.group:
            self.group.stop()
        g = QSequentialAnimationGroup(self)
        a = QPropertyAnimation(self.eff, b"opacity")
        a.setDuration(260)
        a.setStartValue(self.eff.opacity())
        a.setEndValue(1.0)
        g.addAnimation(a)
        g.addPause(2600)
        b = QPropertyAnimation(self.eff, b"opacity")
        b.setDuration(500)
        b.setStartValue(1.0)
        b.setEndValue(0.0)
        g.addAnimation(b)
        g.finished.connect(self.hide)
        g.start()
        self.group = g


QSS = """
* { color: #EEF1FF; font-family: 'Segoe UI', 'SF Pro Display', 'Inter', 'Helvetica Neue', Arial; font-size: 13px; }
QLabel#section { color: rgba(255,255,255,150); font-size: 11px; font-weight: 700; }
QLabel#value { font-size: 22px; font-weight: 800; color: white; }
QLabel#hint { color: rgba(255,255,255,140); font-size: 12px; }
QLabel#dropTitle { font-size: 20px; font-weight: 700; }
QLabel#bigIcon { font-size: 64px; color: rgba(255,255,255,170); }
QLabel#toast { color: white; font-weight: 600; }
QLabel#path { background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,40);
              border-radius: 10px; padding: 8px 12px; color: rgba(255,255,255,210); }
QLabel#chipOn  { background: rgba(44,255,179,35); border: 1px solid rgba(44,255,179,130);
                 border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 700; }
QLabel#chipOff { background: rgba(255,255,255,12); border: 1px solid rgba(255,255,255,40);
                 border-radius: 10px; padding: 3px 10px; font-size: 11px; color: rgba(255,255,255,110); }
QComboBox { background: rgba(255,255,255,22); border: 1px solid rgba(255,255,255,60); border-radius: 12px;
            padding: 9px 14px; font-size: 14px; font-weight: 600; }
QComboBox:hover { border: 1px solid rgba(0,210,255,190); background: rgba(255,255,255,34); }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { width: 0; height: 0; border-left: 5px solid transparent;
                        border-right: 5px solid transparent; border-top: 6px solid white; margin-right: 12px; }
QComboBox QAbstractItemView { background: #1d1a3a; border: 1px solid rgba(255,255,255,50);
                              padding: 6px; outline: 0; }
QComboBox QAbstractItemView::item { padding: 8px; border-radius: 6px; }
QComboBox QAbstractItemView::item:selected { background: rgba(122,92,255,170); }
QSlider { min-height: 30px; }
QSlider::groove:horizontal { height: 8px; border-radius: 4px; background: rgba(255,255,255,28);
                             border: 1px solid rgba(255,255,255,45); }
QSlider::sub-page:horizontal { border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFD60A, stop:0.5 #C6F432, stop:1 #2EE86F); }
QSlider::handle:horizontal { width: 20px; height: 20px; margin: -7px 0; border-radius: 10px;
    background: qradialgradient(cx:0.4, cy:0.35, radius:0.8, stop:0 #ffffff, stop:1 #cdd6ff);
    border: 2px solid rgba(255,255,255,230); }
QSlider::handle:horizontal:hover { background: #ffffff; border: 2px solid #C6F432; }
QSlider::sub-page:horizontal:disabled { background: rgba(255,255,255,40); }
QSlider::handle:horizontal:disabled { background: rgba(255,255,255,90); border: 2px solid rgba(255,255,255,60); }
QPushButton#ghost { background: rgba(255,255,255,20); border: 1px solid rgba(255,255,255,60);
                    border-radius: 10px; padding: 8px 14px; font-weight: 600; }
QPushButton#ghost:hover { background: rgba(255,255,255,40); border-color: rgba(0,210,255,200); }
QPushButton#ghost:pressed { background: rgba(255,255,255,60); }
QPushButton#close { background: #ff5f57; border: none; border-radius: 7px; }
QPushButton#close:hover { background: #ff8a84; }
QPushButton#min { background: #febc2e; border: none; border-radius: 7px; }
QPushButton#min:hover { background: #ffd36b; }
QToolTip { background: #1d1a3a; color: white; border: 1px solid rgba(255,255,255,60); padding: 4px; }
"""


def section(text):
    lbl = QLabel(text.upper())
    lbl.setObjectName("section")
    f = lbl.font()
    f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
    lbl.setFont(f)
    return lbl


def rounded_pixmap(pix, radius=16):
    out = QPixmap(pix.size())
    out.setDevicePixelRatio(pix.devicePixelRatio())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    dpr = pix.devicePixelRatio()
    path.addRoundedRect(QRectF(0, 0, pix.width() / dpr, pix.height() / dpr), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


# ───────────────────────────── Main window ─────────────────────────────
class FotoRC(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1100, 740)
        self.setMinimumSize(960, 660)
        self.files, self.out_dir, self.worker = [], None, None
        self.first_dims, self.last_dir = None, ""
        self._closing_anim, self._closing_done, self._anims = None, False, []
        self.formats = available_formats()

        self.bg = AuroraBackground()
        self.bg.setStyleSheet(QSS)
        self.setCentralWidget(self.bg)
        root = QVBoxLayout(self.bg)
        root.setContentsMargins(22, 12, 22, 16)
        root.setSpacing(10)
        root.addWidget(TitleBar(self))

        body = QHBoxLayout()
        body.setSpacing(12)
        self.drop = DropZone()
        self.drop.filesDropped.connect(self.add_files)
        self.drop.clicked.connect(self.pick_files)
        self.drop.clear_btn.clicked.connect(self.clear_files)
        body.addWidget(self.drop, 11)
        self.settings = self._build_settings()
        body.addWidget(self.settings, 9)
        root.addLayout(body, 1)

        self.progress_card = self._build_progress()
        root.addWidget(self.progress_card)
        self.convert_btn = GlowButton("Convert")
        self.convert_btn.clicked.connect(self.start)
        root.addWidget(self.convert_btn)

        self.grip = QSizeGrip(self.bg)
        self.grip.setFixedSize(18, 18)
        self.toast = Toast(self.bg)
        self._on_format()
        self._update_scale()

    # ---------- layout builders ----------
    def _build_settings(self):
        card = GlassCard()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(32, 28, 32, 26)
        lay.setSpacing(8)

        lay.addWidget(section("Output format"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.setView(QListView())
        self.fmt_combo.setCursor(Qt.PointingHandCursor)
        for f in self.formats:
            self.fmt_combo.addItem(f["label"])
        labels = [f["label"] for f in self.formats]
        if "JPEG" in labels:
            self.fmt_combo.setCurrentIndex(labels.index("JPEG"))
        self.fmt_combo.currentIndexChanged.connect(self._on_format)
        lay.addWidget(self.fmt_combo)
        self.fmt_hint = QLabel()
        self.fmt_hint.setObjectName("hint")
        lay.addWidget(self.fmt_hint)
        lay.addSpacing(12)

        row = QHBoxLayout()
        row.addWidget(section("Scale"))
        row.addStretch()
        self.scale_val = QLabel()
        self.scale_val.setObjectName("value")
        row.addWidget(self.scale_val)
        lay.addLayout(row)
        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(50, 99)
        self.scale.setValue(80)
        self.scale.setCursor(Qt.PointingHandCursor)
        self.scale.valueChanged.connect(self._update_scale)
        lay.addWidget(self.scale)
        self.dims = QLabel()
        self.dims.setObjectName("hint")
        lay.addWidget(self.dims)
        lay.addSpacing(12)

        row = QHBoxLayout()
        row.addWidget(section("Quality"))
        row.addStretch()
        self.q_val = QLabel()
        self.q_val.setObjectName("value")
        row.addWidget(self.q_val)
        lay.addLayout(row)
        self.quality = QSlider(Qt.Horizontal)
        self.quality.setRange(1, 100)
        self.quality.setValue(85)
        self.quality.setCursor(Qt.PointingHandCursor)
        self.quality.valueChanged.connect(self._update_quality)
        lay.addWidget(self.quality)
        self.q_hint = QLabel()
        self.q_hint.setObjectName("hint")
        self.q_hint.setWordWrap(True)
        lay.addWidget(self.q_hint)
        lay.addSpacing(12)

        lay.addWidget(section("Save to"))
        row = QHBoxLayout()
        self.out_label = QLabel("Same folder as each photo")
        self.out_label.setObjectName("path")
        row.addWidget(self.out_label, 1)
        b = QPushButton("Browse")
        b.setObjectName("ghost")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.pick_out_dir)
        row.addWidget(b)
        r = QPushButton("↺")
        r.setObjectName("ghost")
        r.setToolTip("Reset to source folder")
        r.setCursor(Qt.PointingHandCursor)
        r.clicked.connect(self.reset_out_dir)
        row.addWidget(r)
        lay.addLayout(row)
        lay.addStretch()

        chips = QHBoxLayout()
        chips.setSpacing(6)
        caps = [("RAW in", HAS_RAW), ("HEIC", HAS_HEIF), ("AVIF", feature_ok("avif")),
                ("WebP", feature_ok("webp")), ("JP2", feature_ok("jpg_2000"))]
        for name, ok in caps:
            c = QLabel(("✓ " if ok else "✕ ") + name)
            c.setObjectName("chipOn" if ok else "chipOff")
            chips.addWidget(c)
        chips.addStretch()
        lay.addLayout(chips)
        return card

    def _build_progress(self):
        card = GlassCard(radius=18)
        h = QHBoxLayout(card)
        h.setContentsMargins(30, 18, 30, 18)
        h.setSpacing(14)
        self.status = QLabel("visit https://a-creative.studio")
        self.status.setMinimumWidth(240)
        h.addWidget(self.status)
        self.bar = GlassProgress()
        h.addWidget(self.bar, 1)
        self.count = QLabel("0 / 0")
        self.count.setObjectName("hint")
        h.addWidget(self.count)
        self.open_btn = QPushButton("Open folder")
        self.open_btn.setObjectName("ghost")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_dir)))
        self.open_btn.hide()
        h.addWidget(self.open_btn)
        return card

    # ---------- animations ----------
    def intro(self):
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(500)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.start()
        self._anims = [fade]
        for i, w in enumerate((self.drop, self.settings, self.progress_card, self.convert_btn)):
            eff = QGraphicsOpacityEffect(w)
            eff.setOpacity(0.0)
            w.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", self)
            a.setDuration(650)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.finished.connect(lambda w=w: w.setGraphicsEffect(None))
            QTimer.singleShot(150 + 140 * i, a.start)
            self._anims.append(a)

    def _fade_in(self, widget, ms=380):
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(ms)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.finished.connect(lambda: widget.setGraphicsEffect(None))
        a.start()
        self._anims.append(a)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.grip.move(self.bg.width() - 24, self.bg.height() - 24)
        if self.toast.isVisible():
            self.toast.place()

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(8000)
        if self._closing_done:
            e.accept()
            return
        e.ignore()
        if not self._closing_anim:
            a = QPropertyAnimation(self, b"windowOpacity", self)
            a.setDuration(260)
            a.setStartValue(self.windowOpacity())
            a.setEndValue(0.0)
            a.finished.connect(self._really_close)
            a.start()
            self._closing_anim = a

    def _really_close(self):
        self._closing_done = True
        self.close()

    # ---------- settings logic ----------
    def spec(self):
        return self.formats[self.fmt_combo.currentIndex()]

    def _on_format(self):
        if not self.formats:
            return
        s = self.spec()
        kind_txt = {"lossy": "Lossy", "lossless": "Lossless", "palette": "256-color palette"}[s["kind"]]
        self.fmt_hint.setText(f"{kind_txt} · saves as {s['ext']}")
        lossless = s["kind"] == "lossless"
        self.quality.setEnabled(not lossless)
        self.q_hint.setText(f"{s['label']} is lossless — every pixel is kept, so quality doesn't apply."
                            if lossless else QUALITY_TIPS.get(s["fmt"], ""))
        self._update_quality()

    def _update_quality(self):
        s = self.spec() if self.formats else None
        q = self.quality.value()
        if s and s["kind"] == "lossless":
            self.q_val.setText("Lossless")
        elif s and s["fmt"] == "GIF":
            self.q_val.setText(f"{q} · {max(2, round(256 * q / 100))} colors")
        else:
            self.q_val.setText(str(q))

    def _update_scale(self):
        sc = self.scale.value()
        self.scale_val.setText(f"{sc}%")
        if self.first_dims:
            w, h = self.first_dims
            self.dims.setText(f"{w:,} × {h:,}  →  {round(w * sc / 100):,} × {round(h * sc / 100):,} px")
        else:
            self.dims.setText("Add a photo to preview the new size")

    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if d:
            self.out_dir = d
            self.out_label.setText(d)

    def reset_out_dir(self):
        self.out_dir = None
        self.out_label.setText("Same folder as each photo")

    # ---------- files ----------
    def pick_files(self):
        pats = " ".join(f"*{e} *{e.upper()}" for e in sorted(INPUT_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose photos", "",
                                                f"Images ({pats});;All files (*)")
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        new = []
        for p in paths:
            P = Path(p)
            if P.is_dir():
                new += [str(x) for x in sorted(P.rglob("*")) if x.suffix.lower() in INPUT_EXTS]
            elif P.suffix.lower() in INPUT_EXTS:
                new.append(str(P))
        seen = set(self.files)
        added = [n for n in new if not (n in seen or seen.add(n))]
        self.files += added
        if not added:
            self.toast.pop("No supported photos found in that drop 🤔")
        self._refresh_files()

    def clear_files(self):
        self.files, self.first_dims = [], None
        self._refresh_files()

    def _refresh_files(self):
        n = len(self.files)
        self.drop.clear_btn.setVisible(n > 0)
        self.count.setText(f"0 / {n}")
        self.open_btn.hide()
        if not n:
            self.drop.preview.setPixmap(QPixmap())
            self.drop.preview.setText("↑")
            self.drop.title.setText("Drop photos here")
            self.drop.sub.setText("or click to browse · folders work too")
            self._update_scale()
            return
        first = self.files[0]
        self.drop.title.setText(f"{n} photo{'s' if n > 1 else ''} ready")
        self.drop.sub.setText(Path(first).name + (f"  +{n - 1} more" if n > 1 else "") + " · click to add more")
        try:
            self.first_dims = probe_size(first)
            dpr = self.devicePixelRatioF()
            img = normalize_mode(load_image(first, preview=True)).convert("RGBA")
            img.thumbnail((int(460 * dpr), int(280 * dpr)), Image.Resampling.LANCZOS)
            qimg = QImage(img.tobytes("raw", "RGBA"), img.width, img.height,
                          img.width * 4, QImage.Format_RGBA8888).copy()
            pix = QPixmap.fromImage(qimg)
            pix.setDevicePixelRatio(dpr)
            self.drop.preview.setText("")
            self.drop.preview.setPixmap(rounded_pixmap(pix))
            self._fade_in(self.drop.preview)
        except Exception as e:
            self.first_dims = None
            self.drop.preview.setPixmap(QPixmap())
            self.drop.preview.setText("?")
            self.toast.pop(f"Couldn't preview {Path(first).name}: {e}")
        self._update_scale()

    # ---------- conversion ----------
    def start(self):
        if not self.formats:
            self.toast.pop("No output formats available — check your Pillow install")
            return
        if not self.files:
            self.toast.pop("Add some photos first ✨")
            return
        self.worker = ConvertWorker(list(self.files), self.spec(), self.scale.value(),
                                    self.quality.value(), self.out_dir)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.convert_btn.setEnabled(False)
        self.convert_btn.setText("Converting…")
        self.open_btn.hide()
        self.bar.value = 0.0
        self.status.setText("Warming up the pixels…")
        self.worker.start()

    def _on_progress(self, done, total, name):
        self.bar.animate_to(done / total)
        self.count.setText(f"{done} / {total}")
        self.status.setText(f"✓ {name}")

    def _on_done(self, ok, errors, last_dir):
        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("Convert")
        self.last_dir = last_dir
        self.open_btn.setVisible(bool(last_dir))
        self.status.setText(f"Done — {ok} converted" + (f", {len(errors)} failed" if errors else ""))
        self.toast.pop(f"✨ {ok} photo{'s' if ok != 1 else ''} converted to {self.spec()['label']}")
        if errors:
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Warning)
            box.setText(f"{len(errors)} file(s) couldn't be converted.")
            box.setDetailedText("\n".join(errors))
            box.exec()


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = FotoRC()
    win.setWindowOpacity(0.0)
    win.show()
    win.intro()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
