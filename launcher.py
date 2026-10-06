#!/usr/bin/env python3
"""foto RC launcher: shows a glass splash instantly, then loads the heavy stuff."""
import sys

# Only light Qt imports here. Everything heavy is loaded after the splash is visible.
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (QPainter, QPixmap, QColor, QFont, QPen, QPainterPath,
                           QLinearGradient, QRadialGradient, QConicalGradient)
from PySide6.QtWidgets import QApplication, QSplashScreen

W, H = 460, 280
C1, C2, C3 = QColor(255, 214, 10), QColor(198, 244, 50), QColor(46, 232, 111)  # yellow → green


def make_splash(dpr, progress, message):
    pix = QPixmap(int(W * dpr), int(H * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(0, 0, W, H).adjusted(1, 1, -1, -1)
    card = QPainterPath()
    card.addRoundedRect(r, 28, 28)

    # Dark base + glowing orbs (clipped to the rounded card)
    p.setClipPath(card)
    base = QLinearGradient(r.topLeft(), r.bottomRight())
    base.setColorAt(0, QColor(12, 10, 32))
    base.setColorAt(1, QColor(16, 30, 30))
    p.fillRect(r, base)
    for color, cx, cy, rad in ((C1, 0.15, 0.10, 260), (C3, 0.90, 0.95, 280), (C2, 0.70, 0.20, 180)):
        g = QRadialGradient(QPointF(W * cx, H * cy), rad)
        for stop, a in ((0, 130), (0.5, 35), (1, 0)):
            c = QColor(color)
            c.setAlpha(a)
            g.setColorAt(stop, c)
        p.fillRect(r, g)
    sheen = QLinearGradient(0, 0, 0, H * 0.55)  # glass highlight
    sheen.setColorAt(0, QColor(255, 255, 255, 40))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(r, sheen)
    p.setClipping(False)

    # Glass border
    border = QLinearGradient(r.topLeft(), r.bottomRight())
    border.setColorAt(0, QColor(255, 255, 255, 190))
    border.setColorAt(0.5, QColor(255, 255, 255, 40))
    border.setColorAt(1, QColor(C3.red(), C3.green(), C3.blue(), 160))
    p.setPen(QPen(border, 1.4))
    p.drawPath(card)

    # Lens logo
    lr = QRectF(36, 40, 44, 44)
    ring = QConicalGradient(lr.center(), progress * 360)
    for s, c in ((0, C1), (0.5, C3), (1, C1)):
        ring.setColorAt(s, c)
    p.setPen(QPen(ring, 5))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(lr)

    # Gradient title
    f = QFont()
    f.setPointSize(34)
    f.setWeight(QFont.Weight.Black)
    title = QPainterPath()
    title.addText(96, 78, f, "foto RC")
    tg = QLinearGradient(96, 0, 300, 0)
    tg.setColorAt(0, C1)
    tg.setColorAt(0.5, C2)
    tg.setColorAt(1, C3)
    p.fillPath(title, tg)

    small = QFont()
    small.setPointSize(12)
    p.setFont(small)
    p.setPen(QColor(255, 255, 255, 150))
    p.drawText(QRectF(98, 86, 300, 20), Qt.AlignLeft, "glass-smooth photo converter")

    # Status text + progress bar
    p.setPen(QColor(255, 255, 255, 200))
    p.drawText(QRectF(36, H - 82, W - 72, 20), Qt.AlignLeft | Qt.AlignVCenter, message)
    track = QRectF(36, H - 52, W - 72, 8)
    p.setPen(QPen(QColor(255, 255, 255, 60), 1))
    p.setBrush(QColor(255, 255, 255, 25))
    p.drawRoundedRect(track, 4, 4)
    if progress > 0:
        fill = QRectF(track.left(), track.top(), max(8, track.width() * progress), track.height())
        fg = QLinearGradient(fill.topLeft(), fill.topRight())
        fg.setColorAt(0, C1)
        fg.setColorAt(1, C3)
        p.setPen(Qt.NoPen)
        p.setBrush(fg)
        p.drawRoundedRect(fill, 4, 4)
    p.end()
    return pix


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("foto RC")
    dpr = app.primaryScreen().devicePixelRatio()

    splash = QSplashScreen(make_splash(dpr, 0.05, "Starting up…"), Qt.WindowStaysOnTopHint)
    splash.setAttribute(Qt.WA_TranslucentBackground)
    splash.show()
    app.processEvents()

    def step(progress, msg):
        splash.setPixmap(make_splash(dpr, progress, msg))
        app.processEvents()

    step(0.30, "Loading imaging engine…")
    import foto_rc # the heavy part: Pillow, HEIF, the full UI code

    step(0.75, "Polishing the glass…")
    win = foto_rc.FotoRC()

    step(1.0, "Ready")
    win.setWindowOpacity(0.0)
    win.show()
    splash.finish(win)  # splash closes once the main window appears
    win.intro()         # your existing fade-in animation
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
