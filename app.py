"""
Carton Barcode Capture

Table setup: barcode stuck on a carton's inner flap -> operator photographs
it on a Samsung phone -> this app auto-pulls the photo over ADB -> operator
(or full-auto mode) finds the barcode ROI -> decodes it -> saves the cropped,
barcode-named image into a growing dataset folder.

Run:  python3 app.py
Edit config.py to change ADB paths, poll rate, and the dataset output folder.
"""

import os
import sys
from datetime import datetime

import cv2
from PyQt5.QtCore import Qt, QRect, QRectF, QPointF, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QPolygonF, QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QGraphicsView, QGraphicsScene,
    QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QRadioButton, QButtonGroup,
    QGroupBox, QGraphicsPolygonItem,
)

import config as cfg
from adb_poller import AdbPoller
from barcode_decode import detect_auto, decode_region, crop_with_padding, sanitize_for_filename


def cv_to_qpixmap(img_bgr):
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())  # copy() so it survives the numpy buffer being reused


DARK_THEME_QSS = """
QMainWindow, QWidget {
    background-color: #14171a;
    color: #e6e8ea;
    font-size: 13px;
}
QGroupBox {
    background-color: #1a1e21;
    border: 1px solid #2a2f33;
    border-radius: 8px;
    margin-top: 16px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: #8a9199;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #22c55e;
}
QPushButton {
    background-color: #22272b;
    color: #dfe3e6;
    border: 1px solid #33393e;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover { background-color: #283430; border: 1px solid #22c55e; }
QPushButton:pressed { background-color: #1b2320; }
QPushButton:disabled { color: #565c62; border-color: #262a2d; background-color: #1a1d1f; }
QPushButton#primaryButton {
    background-color: #22c55e;
    color: #0e1210;
    border: none;
    font-weight: 700;
    padding: 7px 18px;
}
QPushButton#primaryButton:hover { background-color: #2fd66c; }
QPushButton#primaryButton:pressed { background-color: #1ea952; }
QPushButton#primaryButton:disabled { background-color: #2a3430; color: #5c655f; }
QRadioButton { color: #cfd4d8; spacing: 6px; }
QRadioButton::indicator {
    width: 14px; height: 14px; border-radius: 8px;
    border: 2px solid #3a4045; background: #1a1e21;
}
QRadioButton::indicator:checked { border: 2px solid #22c55e; background: #22c55e; }
QStatusBar { background-color: #101315; color: #8a9199; border-top: 1px solid #2a2f33; }
QGraphicsView { background-color: #1a1e21; border: 1px solid #2a2f33; border-radius: 6px; }
QScrollBar:vertical, QScrollBar:horizontal { background: #1a1e21; border: none; }
QScrollBar:vertical { width: 11px; margin: 2px; }
QScrollBar:horizontal { height: 11px; margin: 2px; }
QScrollBar::handle { background: #383f44; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: #22c55e; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""


class ZoomPanView(QGraphicsView):
    """
    Reusable image viewer.
      - mouse wheel  = zoom, anchored under the cursor
      - drag         = pan (default) or draw-a-box (when roi mode is on)
      - quick click (<4px movement) = 'clicked' signal, for future use
      - zoom_in() / zoom_out() / fit() back the required toolbar buttons
    """
    clicked = pyqtSignal(float, float)
    roi_selected = pyqtSignal(float, float, float, float)  # x1, y1, x2, y2 in image coords

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = None
        self._overlay_item = None
        self._roi_mode = False
        self._press_pos = None

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(26, 30, 33))
        self.setMinimumSize(360, 280)

    # ---- content ----
    def set_image(self, img_bgr):
        pixmap = cv_to_qpixmap(img_bgr)
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._overlay_item = None
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fit()

    def clear_image(self):
        self._scene.clear()
        self._pixmap_item = None
        self._overlay_item = None

    def set_overlay_polygon(self, points, color=QColor(0, 220, 0)):
        """Draw (or clear, if points is None) a highlight outline - used to show
        the auto-detected region or a confirmed manual selection."""
        if self._overlay_item is not None:
            self._scene.removeItem(self._overlay_item)
            self._overlay_item = None
        if points:
            poly = QPolygonF([QPointF(p[0], p[1]) for p in points])
            item = QGraphicsPolygonItem(poly)
            pen = QPen(color, 3)
            pen.setCosmetic(True)  # constant on-screen width regardless of zoom
            item.setPen(pen)
            self._scene.addItem(item)
            self._overlay_item = item

    # ---- navigation ----
    def set_roi_mode(self, enabled):
        self._roi_mode = enabled
        self.setDragMode(QGraphicsView.RubberBandDrag if enabled else QGraphicsView.ScrollHandDrag)

    def fit(self):
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

    def zoom_in(self):
        self.scale(1.25, 1.25)

    def zoom_out(self):
        self.scale(0.8, 0.8)

    def wheelEvent(self, event):
        if self._pixmap_item is None:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    # ---- click vs drag ----
    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        press = self._press_pos
        release = event.pos()
        super().mouseReleaseEvent(event)
        if press is not None and self._pixmap_item is not None:
            moved = (release - press).manhattanLength()
            if moved < 4:
                pt = self.mapToScene(release)
                self.clicked.emit(pt.x(), pt.y())
            elif self._roi_mode:
                scene_rect = self.mapToScene(QRect(press, release).normalized()).boundingRect()
                img_rect = scene_rect.intersected(self._pixmap_item.boundingRect())
                if img_rect.width() > 3 and img_rect.height() > 3:
                    self.roi_selected.emit(img_rect.left(), img_rect.top(),
                                            img_rect.right(), img_rect.bottom())
        self._press_pos = None


def _panel(title, view, on_zoom_in, on_zoom_out, on_fit):
    box = QGroupBox(title)
    layout = QVBoxLayout()
    row = QHBoxLayout()
    for text, handler in (("Zoom In", on_zoom_in), ("Zoom Out", on_zoom_out), ("Fit", on_fit)):
        btn = QPushButton(text)
        btn.clicked.connect(handler)
        row.addWidget(btn)
    row.addStretch(1)
    layout.addLayout(row)
    layout.addWidget(view)
    box.setLayout(layout)
    return box


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(cfg.WINDOW_TITLE)
        self.resize(1280, 760)

        self.mode_auto = True
        self.pending_manual_rect = None
        self.current_raw_image = None

        self._build_ui()
        self._start_poller()

    # ---------------------------------------------------------------- UI --
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        # saved banner - big, hidden until a save happens
        self.saved_label = QLabel("")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        self.saved_label.setFont(f)
        self.saved_label.setStyleSheet(
            "color: #22c55e; background-color: rgba(34, 197, 94, 35);"
            "border: 1px solid #22c55e; border-radius: 8px; padding: 8px;"
        )
        self.saved_label.setAlignment(Qt.AlignCenter)
        self.saved_label.setVisible(False)
        outer.addWidget(self.saved_label)

        # controls row
        controls = QHBoxLayout()
        mode_box = QGroupBox("ROI mode")
        mode_layout = QHBoxLayout()
        self.auto_radio = QRadioButton("Auto")
        self.manual_radio = QRadioButton("Manual")
        self.auto_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.auto_radio)
        self.mode_group.addButton(self.manual_radio)
        self.auto_radio.toggled.connect(self._on_mode_toggled)
        mode_layout.addWidget(self.auto_radio)
        mode_layout.addWidget(self.manual_radio)
        mode_box.setLayout(mode_layout)
        controls.addWidget(mode_box)

        self.capture_btn = QPushButton("Capture")
        self.capture_btn.setObjectName("primaryButton")
        self.capture_btn.clicked.connect(self.on_capture_clicked)
        controls.addWidget(self.capture_btn)

        self.detect_btn = QPushButton("Detect")
        self.detect_btn.setObjectName("primaryButton")
        self.detect_btn.clicked.connect(self.run_detect)
        controls.addWidget(self.detect_btn)
        controls.addStretch(1)
        outer.addLayout(controls)

        # two panels
        panels = QHBoxLayout()
        self.raw_view = ZoomPanView()
        self.result_view = ZoomPanView()
        panels.addWidget(_panel("Raw capture", self.raw_view,
                                 self.raw_view.zoom_in, self.raw_view.zoom_out, self.raw_view.fit))
        panels.addWidget(_panel("Detected crop", self.result_view,
                                 self.result_view.zoom_in, self.result_view.zoom_out, self.result_view.fit))
        outer.addLayout(panels, stretch=1)

        self.raw_view.roi_selected.connect(self.on_roi_selected)
        self.raw_view.set_roi_mode(False)  # start in Auto -> pan mode

        self.statusBar().showMessage("Starting...")

    def _start_poller(self):
        self.poller = AdbPoller(
            cfg.ADB_PATH, cfg.PHONE_CAPTURE_DIR, cfg.SCRATCH_DIR,
            cfg.POLL_INTERVAL_MS, cfg.DELETE_FROM_PHONE_AFTER_PULL,
            camera_open_delay_s=cfg.CAMERA_OPEN_DELAY_S,
        )
        self.poller.new_image.connect(self.on_new_image)
        self.poller.status_changed.connect(self.on_status_changed)
        self.poller.capture_finished.connect(lambda: self.capture_btn.setEnabled(True))
        self.poller.start()

    def closeEvent(self, event):
        self.poller.stop()
        self.poller.wait(2000)
        super().closeEvent(event)

    # ----------------------------------------------------------- signals --
    def _on_mode_toggled(self, auto_checked):
        self.mode_auto = auto_checked
        self.raw_view.set_roi_mode(not auto_checked)
        self.raw_view.set_overlay_polygon(None)
        self.pending_manual_rect = None

    def on_status_changed(self, text):
        self.statusBar().showMessage(text)

    def on_capture_clicked(self):
        self.capture_btn.setEnabled(False)  # re-enabled by poller.capture_finished
        self.statusBar().showMessage("Triggering phone capture...")
        self.poller.request_capture()

    def on_new_image(self, path):
        img = cv2.imread(path)
        if img is None:
            return
        self.current_raw_image = img
        self.raw_view.set_image(img)
        self.result_view.clear_image()
        self.pending_manual_rect = None
        if self.mode_auto:
            self.run_detect()

    def on_roi_selected(self, x1, y1, x2, y2):
        self.pending_manual_rect = (x1, y1, x2, y2)
        self.raw_view.set_overlay_polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                                           color=QColor(255, 200, 0))
        self.statusBar().showMessage("ROI selected - press Detect")

    # ----------------------------------------------------------- pipeline --
    def run_detect(self):
        if self.current_raw_image is None:
            self.statusBar().showMessage("No image yet")
            return

        if self.mode_auto:
            det = detect_auto(self.current_raw_image)
        else:
            if self.pending_manual_rect is None:
                self.statusBar().showMessage("Draw a box around the barcode first")
                return
            det = decode_region(self.current_raw_image, self.pending_manual_rect)

        if det is None:
            self.statusBar().showMessage("No barcode found - try Manual mode / redraw the box")
            return

        self.raw_view.set_overlay_polygon(det.polygon, color=QColor(0, 220, 0))
        crop, _box = crop_with_padding(self.current_raw_image, det.polygon, cfg.ROI_PADDING_PX)
        self.result_view.set_image(crop)

        save_path = self._save_crop(crop, det.text)
        self.statusBar().showMessage(f"Decoded: {det.text}  ({det.format})")
        self._show_saved_banner(save_path)

    def _save_crop(self, crop, barcode_text):
        os.makedirs(cfg.OUTPUT_FOLDER, exist_ok=True)
        now = datetime.now()
        base = f"{sanitize_for_filename(barcode_text)}_{now:%Y%m%d}_{now:%H%M}"
        path = os.path.join(cfg.OUTPUT_FOLDER, base + ".jpg")
        n = 1
        while os.path.exists(path):  # same barcode decoded again in the same minute
            path = os.path.join(cfg.OUTPUT_FOLDER, f"{base}_{n}.jpg")
            n += 1
        cv2.imwrite(path, crop)
        return path

    def _show_saved_banner(self, path):
        self.saved_label.setText(f"\u2713 SAVED  --  {os.path.basename(path)}")
        self.saved_label.setVisible(True)
        QTimer.singleShot(cfg.SAVED_BANNER_MS, lambda: self.saved_label.setVisible(False))


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_THEME_QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
