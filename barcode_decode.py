"""
Barcode detection + decoding.

Design choice (per spec): the photo is shot at a tilt angle on purpose, so a
detected barcode's four corners form a tilted quadrilateral, not an
axis-aligned rectangle. We deliberately do NOT perspective-warp/dewarp the
crop back to a "flat" top-down view — the saved image must keep the same
tilt it was photographed at. So cropping = axis-aligned bounding box around
the polygon (plus padding), never a homography correction.
"""

import cv2
import numpy as np
import zxingcpp


class Detection:
    """Result of a successful detect/decode."""

    def __init__(self, text, fmt, polygon):
        self.text = text          # decoded string
        self.format = fmt         # e.g. "Code128", "QRCode"
        self.polygon = polygon    # 4 (x, y) points in FULL-IMAGE coordinates,
                                   # in the order the detector reported them
                                   # (may be tilted / not axis-aligned)


def _polygon_from_barcode(barcode, offset=(0, 0)):
    pos = barcode.position
    ox, oy = offset
    return [
        (pos.top_left.x + ox, pos.top_left.y + oy),
        (pos.top_right.x + ox, pos.top_right.y + oy),
        (pos.bottom_right.x + ox, pos.bottom_right.y + oy),
        (pos.bottom_left.x + ox, pos.bottom_left.y + oy),
    ]


def detect_auto(image_bgr):
    """Run detection on the FULL raw frame. Returns a Detection or None.

    Used for auto-ROI mode: whatever barcode zxingcpp finds anywhere in the
    frame, its own polygon IS the ROI - no separate 'find the region' step.
    """
    results = zxingcpp.read_barcodes(image_bgr)
    if not results:
        return None
    b = results[0]
    return Detection(b.text, str(b.format), _polygon_from_barcode(b))


def decode_region(image_bgr, rect):
    """Run detection only inside a user-drawn rect (x1, y1, x2, y2).

    Used for manual-ROI mode. Polygon is translated back into full-image
    coordinates so downstream cropping behaves identically to auto mode.
    """
    x1, y1, x2, y2 = [int(v) for v in rect]
    x1, y1 = max(0, x1), max(0, y1)
    x2 = min(image_bgr.shape[1], x2)
    y2 = min(image_bgr.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return None
    crop = image_bgr[y1:y2, x1:x2]
    results = zxingcpp.read_barcodes(crop)
    if not results:
        return None
    b = results[0]
    return Detection(b.text, str(b.format), _polygon_from_barcode(b, offset=(x1, y1)))


def crop_with_padding(image_bgr, polygon, padding):
    """Axis-aligned bounding-box crop around polygon, expanded by padding.

    Deliberately NOT a perspective warp - tilt is preserved as shot.
    Returns (crop, (x1, y1, x2, y2)) so the caller can also draw the box
    on the raw preview if it wants to.
    """
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    h, w = image_bgr.shape[:2]
    x1 = max(0, int(min(xs)) - padding)
    y1 = max(0, int(min(ys)) - padding)
    x2 = min(w, int(max(xs)) + padding)
    y2 = min(h, int(max(ys)) + padding)
    return image_bgr[y1:y2, x1:x2], (x1, y1, x2, y2)


def sanitize_for_filename(text):
    """Barcode text can contain characters that are invalid in filenames."""
    keep = "-_."
    return "".join(c if c.isalnum() or c in keep else "_" for c in text)
