# Carton Barcode Capture

Operator photographs the barcode on a carton's inner flap using a Samsung
phone -> this app auto-pulls the photo over ADB -> detects + decodes the
barcode -> saves the cropped, barcode-named image into a growing dataset.

## Setup

1. `pip install -r requirements.txt --break-system-packages` (or in a venv)
2. Install platform-tools `adb` and make sure it's on PATH (or set the full
   path in `config.py -> ADB_PATH`)
3. Connect the phone by USB, enable USB debugging, accept the "Allow USB
   debugging" prompt on the phone once
4. Edit `config.py`:
   - `OUTPUT_FOLDER` - where the permanent dataset gets saved
   - `PHONE_CAPTURE_DIR` - defaults to the stock Camera folder; change if
     your phone saves photos somewhere else
5. `python3 app.py`

## How it behaves

- **Auto mode (default):** operator just taps the phone's shutter. The app
  detects the new photo (~0.3-0.5s poll), pulls it, shows it, and
  immediately tries to decode the barcode across the whole frame - if found,
  it crops + saves + flashes the green "SAVED" banner with no button
  presses needed.
- **Manual mode:** for when auto-detect can't find it (bad angle, cluttered
  background). Drag on the raw panel to box the barcode, then press
  **Detect**. In this mode drag draws the box instead of panning - use the
  mouse wheel to zoom in/out first if you need a closer look before drawing.
- **Tilt is preserved on purpose:** the photo is shot at an angle, not
  top-down. The crop is a plain axis-aligned bounding box around whatever
  was detected (with a small padding margin) - never a perspective
  warp/dewarp. The saved image looks exactly as tilted as it was shot.
- Every panel: mouse wheel = zoom (anchored under the cursor), Zoom
  In/Out/Fit buttons, and (in Auto mode) drag = pan.
- The raw pull is a single scratch file that gets overwritten every
  capture - it's not archived. Only the final barcode-named crop is kept
  permanently, in `OUTPUT_FOLDER`. Say the word if you want raw captures
  archived too (e.g. for audit) and I'll add a config flag for it.
- After every successful pull, the source photo is deleted from the phone
  (`DELETE_FROM_PHONE_AFTER_PULL` in config) to keep phone storage clean and
  keep "is this a new photo" detection simple.

## Deferred

Network-port access to the dataset (so it's reachable remotely) was
intentionally left out of this pass - next step, as discussed.

## Not testable in this sandbox

No real Samsung phone or display was available here, so `adb_poller.py`
was verified against a real `adb` binary for its no-device / graceful-
disconnect handling, and `app.py` was run end-to-end offscreen with a
synthetic tilted-barcode image standing in for a real pull (detect -> crop
-> save -> filename -> banner all confirmed working). The one thing that
still needs a real run on your machine: pointing a phone's actual DCIM path
and confirming poll-to-display latency feels right - `POLL_INTERVAL_MS` in
config.py is the knob if it needs to be faster/slower.
