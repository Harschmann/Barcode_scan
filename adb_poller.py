"""
Watches the phone's camera folder over ADB and pulls new photos automatically.

Flow per cycle:
  1. list newest filename in PHONE_CAPTURE_DIR (adb shell ls -t)
  2. if it's different from the last one we saw -> adb pull it to a local
     scratch path (always the SAME local filename, i.e. overwritten - the
     raw pull is not archived, only the final detected crop is)
  3. optionally adb shell rm the source file on the phone (config-gated)
  4. emit new_image so the UI can load it immediately

No device / disconnected phone is a normal, expected state (not an error) -
the operator plugs in, works, unplugs. We report status instead of raising.
"""

import os
import subprocess
import threading
import time

from PyQt5.QtCore import QThread, pyqtSignal


class AdbPoller(QThread):
    new_image = pyqtSignal(str)     # local path to the freshly pulled image
    status_changed = pyqtSignal(str)  # human-readable connection/status text
    capture_started = pyqtSignal()    # UI capture trigger sequence has begun
    capture_finished = pyqtSignal()   # ...and it's done (success or fail - see status)

    def __init__(self, adb_path, phone_dir, scratch_dir, poll_interval_ms,
                 delete_after_pull, camera_open_delay_s=1.5, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path
        self.phone_dir = phone_dir
        self.scratch_dir = scratch_dir
        self.poll_interval = max(poll_interval_ms, 100) / 1000.0
        self.delete_after_pull = delete_after_pull
        self.camera_open_delay = camera_open_delay_s
        self._running = True
        self._last_seen = None
        self._last_status = None
        self._capture_requested = threading.Event()

    def stop(self):
        self._running = False

    def request_capture(self):
        """Thread-safe - call from the UI thread. Tells the poller to trigger
        the phone's shutter on its next loop iteration; the resulting photo
        then flows through the exact same pull/detect/save path as a photo
        the operator took by hand."""
        self._capture_requested.set()

    def run(self):
        os.makedirs(self.scratch_dir, exist_ok=True)
        while self._running:
            try:
                if not self._device_connected():
                    self._emit_status("No phone connected (check USB + adb authorization)")
                    time.sleep(1.0)
                    continue
                self._emit_status("Connected - watching for new photo")

                if self._capture_requested.is_set():
                    self._capture_requested.clear()
                    self.capture_started.emit()
                    try:
                        self._trigger_phone_capture()
                    finally:
                        self.capture_finished.emit()

                newest = self._get_newest_filename()
                if newest and newest != self._last_seen:
                    self._last_seen = newest
                    local_path = self._pull(newest)
                    if local_path:
                        self.new_image.emit(local_path)
                        if self.delete_after_pull:
                            self._delete_on_phone(newest)
            except Exception as e:
                self._emit_status(f"Poll error: {e}")
            time.sleep(self.poll_interval)

    def _emit_status(self, text):
        if text != self._last_status:
            self._last_status = text
            self.status_changed.emit(text)

    def _run(self, args, timeout=4):
        return subprocess.run(
            [self.adb_path, *args],
            capture_output=True, text=True, timeout=timeout
        )

    def _device_connected(self):
        out = self._run(["get-state"], timeout=3)
        return out.returncode == 0 and out.stdout.strip() == "device"

    def _trigger_phone_capture(self):
        # Bring the stock camera app to the foreground in still-photo mode,
        # give it a moment to actually be ready, then send the hardware
        # shutter keypress (KEYCODE_CAMERA). Re-issuing the "open camera"
        # intent every time (rather than only once) means this recovers
        # cleanly even if the app was left on a post-capture review screen.
        self._run(["shell", "am", "start", "-a", "android.media.action.STILL_IMAGE_CAMERA"], timeout=4)
        time.sleep(self.camera_open_delay)
        self._run(["shell", "input", "keyevent", "27"], timeout=3)

    def _get_newest_filename(self):
        out = self._run(["shell", f"ls -t {self.phone_dir}"], timeout=3)
        if out.returncode != 0:
            return None
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        return lines[0] if lines else None

    def _pull(self, filename):
        local_path = os.path.join(self.scratch_dir, "current.jpg")
        remote_path = f"{self.phone_dir}/{filename}"
        tmp_path = local_path + ".part"
        result = self._run(["pull", remote_path, tmp_path], timeout=6)
        if result.returncode != 0:
            return None
        os.replace(tmp_path, local_path)  # atomic-ish swap so UI never reads a half-written file
        return local_path

    def _delete_on_phone(self, filename):
        remote_path = f"{self.phone_dir}/{filename}"
        self._run(["shell", "rm", remote_path], timeout=3)
