"""
Central configuration for Carton Barcode Capture.
Edit values below - nothing else in the app needs to change.
"""

# --- ADB / phone ---
ADB_PATH = "adb"                                   # full path only if adb isn't on PATH
PHONE_CAPTURE_DIR = "/storage/emulated/0/DCIM/Camera"  # folder on phone where new photos land
POLL_INTERVAL_MS = 400                             # how often to check phone for a new photo
DELETE_FROM_PHONE_AFTER_PULL = True                # cleans phone storage + simplifies new-file detection

# --- Remote capture trigger (the UI "Capture" button) ---
# Opens the phone's camera app, waits CAMERA_OPEN_DELAY_S, then sends the
# shutter keypress. This delay is the one thing that genuinely needs
# tuning on your actual phone - if captures come out blank/blurry, raise
# it; if it feels slow, lower it.
CAMERA_OPEN_DELAY_S = 1.5

# --- Local scratch space (raw pull target, overwritten every capture - not part of the dataset) ---
SCRATCH_DIR = "scratch"

# --- Dataset output (the permanent, growing dataset of detected crops) ---
OUTPUT_FOLDER = "dataset"          # <-- EDIT to point wherever you want the dataset saved
ROI_PADDING_PX = 15                # margin kept around the detected/drawn barcode when cropping

# --- UI ---
WINDOW_TITLE = "Carton Barcode Capture"
SAVED_BANNER_MS = 1200             # how long the big green "SAVED" banner stays on screen
