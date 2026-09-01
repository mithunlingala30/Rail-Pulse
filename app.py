"""
RailPulse — Railway Track Condition Monitoring
Vibration Frequency Analysis & Fourier Series Backend

Two workflows:
  1. Upload a CSV vibration dataset for the track between two stations and
     have it analyzed (FFT + defect-band heuristics).
  2. Explore a live simulated demo track if no dataset has been uploaded.

IMPORTANT — this is a demonstration / educational tool, not a certified
safety system. The frequency bands used to represent corrugation,
wheel-flats, joint gaps and rail cracks are illustrative approximations
drawn from general published ranges in railway condition-monitoring
literature. The "closest signature" label assigned to uploaded data is a
heuristic best guess, not a validated diagnosis. Any real deployment needs
calibrated instrumentation and sign-off from a qualified track engineer.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""

import io
import time
import threading
import random
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request, redirect, url_for

app = Flask(__name__)

# --------------------------------------------------------------------------
# Simulation / analysis configuration
# --------------------------------------------------------------------------

FS = 2000.0            # sampling rate (Hz) used for the simulated demo track
DURATION = 0.5          # seconds captured per simulated snapshot
N_SAMPLES = int(FS * DURATION)
BASE_FREQ = 18.0         # Hz, nominal wheel-rail rolling fundamental

DEFECT_BANDS = {
    "corrugation": [(300, 800)],
    "wheel_flat": [(10, 60)],
    "joint_gap": [(1, 8), (300, 1000)],
    "rail_crack": [(150, 260)],
    None: [],
}

DEFECT_LABELS = {
    "corrugation": "Rail Corrugation",
    "wheel_flat": "Wheel Flat Impact",
    "joint_gap": "Joint Gap Impact",
    "rail_crack": "Rail Crack Resonance",
    None: "No Defect Signature",
}

RECOMMENDED_ACTION = {
    "corrugation": "Schedule rail grinding to restore running surface profile.",
    "wheel_flat": "Inspect rolling stock wheelsets for flats; re-profile if confirmed.",
    "joint_gap": "Inspect and re-tighten/weld rail joint; check fishplates.",
    "rail_crack": "Priority ultrasonic inspection — potential internal rail defect.",
    None: "No action required. Continue routine monitoring.",
}

STATUS_THRESHOLDS = {"healthy": 80, "warning": 50}  # health_score >= value

# Stations along the demo corridor (Chennai Central -> Arakkonam), with
# approximate real-world coordinates and chainage in kilometres.
STATIONS = [
    {"code": "MAS", "name": "Chennai Central", "lat": 13.0827, "lng": 80.2707, "chainage_km": 0.0},
    {"code": "PER", "name": "Perambur", "lat": 13.1143, "lng": 80.2331, "chainage_km": 7.0},
    {"code": "VLK", "name": "Villivakkam", "lat": 13.1067, "lng": 80.2000, "chainage_km": 11.0},
    {"code": "ABU", "name": "Ambattur", "lat": 13.1143, "lng": 80.1548, "chainage_km": 15.0},
    {"code": "AVD", "name": "Avadi", "lat": 13.1147, "lng": 80.0970, "chainage_km": 19.0},
    {"code": "TRVR", "name": "Thiruninravur", "lat": 13.1319, "lng": 80.0524, "chainage_km": 23.0},
    {"code": "TRL", "name": "Tiruvallur", "lat": 13.1439, "lng": 79.9089, "chainage_km": 33.0},
    {"code": "AJJ", "name": "Arakkonam", "lat": 13.0833, "lng": 79.6667, "chainage_km": 43.0},
]
TOTAL_KM = STATIONS[-1]["chainage_km"]
N_SIM_SEGMENTS = 14

_lock = threading.Lock()
_segments = []      # simulated demo sensors (always running in the background)
_alerts = []
_tick = 0

app_state = {
    "mode": "simulated",              # 'simulated' | 'uploaded'
    "from_station": STATIONS[0]["code"],
    "to_station": STATIONS[-1]["code"],
    "uploaded": None,                  # dict once a dataset has been uploaded
}


def get_station(code):
    return next((s for s in STATIONS if s["code"] == code), None)


def station_range(from_code, to_code):
    a = get_station(from_code) or STATIONS[0]
    b = get_station(to_code) or STATIONS[-1]
    lo, hi = sorted([a["chainage_km"], b["chainage_km"]])
    return lo, hi, a, b


def maps_embed_url(from_st, to_st):
    """Key-less Google Maps directions embed (no Maps API key required).
    For richer styling (custom colored markers per sensor status), swap
    this for the Maps Embed API with a key: https://developers.google.com/maps/documentation/embed/get-started"""
    return (
        f"https://www.google.com/maps?saddr={from_st['lat']},{from_st['lng']}"
        f"&daddr={to_st['lat']},{to_st['lng']}&output=embed"
    )


def classify_status(score):
    if score >= STATUS_THRESHOLDS["healthy"]:
        return "healthy"
    if score >= STATUS_THRESHOLDS["warning"]:
        return "warning"
    return "critical"


# --------------------------------------------------------------------------
# Simulated demo track
# --------------------------------------------------------------------------

def init_segments():
    rng = random.Random(42)
    defect_pool = ["corrugation", "wheel_flat", "joint_gap", "rail_crack"]
    seeded_defect_indices = rng.sample(range(N_SIM_SEGMENTS), k=4)

    segs = []
    for i in range(N_SIM_SEGMENTS):
        chainage = round((i + 1) * (TOTAL_KM / (N_SIM_SEGMENTS + 1)), 2)
        defect_type = None
        severity = 0.0
        if i in seeded_defect_indices:
            defect_type = defect_pool[seeded_defect_indices.index(i) % len(defect_pool)]
            severity = round(rng.uniform(0.15, 0.4), 3)
        segs.append({
            "id": f"S{i+1:02d}",
            "name": f"Sensor {i+1:02d}",
            "chainage_km": chainage,
            "speed_limit_kmph": rng.choice([80, 100, 110, 130]),
            "defect_type": defect_type,
            "severity": severity,
            "seed": rng.randint(0, 10_000),
            "health_score": 100.0,
            "status": "healthy",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
    return segs


def generate_vibration_signal(defect_type, severity, seed):
    """Synthesize a realistic-looking accelerometer trace for one snapshot."""
    t = np.linspace(0, DURATION, N_SAMPLES, endpoint=False)
    rng = np.random.default_rng(seed + int(time.time() // 5))  # drifts slowly
    base_amp = 1.0

    signal = base_amp * (
        np.sin(2 * np.pi * BASE_FREQ * t)
        + 0.5 * np.sin(2 * np.pi * 2 * BASE_FREQ * t)
        + 0.25 * np.sin(2 * np.pi * 3 * BASE_FREQ * t)
    )
    signal += rng.normal(0, 0.05 * base_amp, size=t.shape)

    if defect_type == "corrugation" and severity > 0:
        fc = 450 + rng.uniform(-60, 60)
        signal += severity * base_amp * 2.2 * np.sin(2 * np.pi * fc * t)

    elif defect_type == "wheel_flat" and severity > 0:
        period_samples = max(int(FS / BASE_FREQ), 1)
        impulses = np.zeros_like(t)
        impulses[::period_samples] = 1.0
        kernel = np.exp(-np.linspace(0, 10, max(int(0.01 * FS), 2)))
        impulse_signal = np.convolve(impulses, kernel, mode="same")
        signal += severity * base_amp * 4.0 * impulse_signal

    elif defect_type == "joint_gap" and severity > 0:
        gap_freq = 3.5
        period_samples = max(int(FS / gap_freq), 1)
        impulses = np.zeros_like(t)
        impulses[::period_samples] = 1.0
        kernel = np.exp(-np.linspace(0, 18, max(int(0.006 * FS), 2)))
        impulse_signal = np.convolve(impulses, kernel, mode="same")
        signal += severity * base_amp * 5.0 * impulse_signal

    elif defect_type == "rail_crack" and severity > 0:
        fr = 190 + rng.uniform(-25, 25)
        signal += severity * base_amp * 1.6 * np.sin(2 * np.pi * fr * t)
        signal += rng.normal(0, 0.12 * severity * base_amp, size=t.shape)

    return t, signal


# --------------------------------------------------------------------------
# Signal analysis (shared by simulated + uploaded data)
# --------------------------------------------------------------------------

def compute_fft(signal, fs):
    n = len(signal)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    complex_spectrum = np.fft.rfft(signal)
    amps = (np.abs(complex_spectrum) / n) * 2.0
    return freqs, amps, complex_spectrum


def dominant_frequency(freqs, amps):
    if len(amps) <= 1:
        return 0.0
    idx = int(np.argmax(amps[1:])) + 1  # skip DC bin
    return round(float(freqs[idx]), 1)


def compute_health(freqs, amps, defect_type):
    """Used for the simulated track, where the injected defect type is known."""
    total_energy = float(np.sum(amps ** 2))
    if total_energy <= 0:
        return 100.0
    defect_energy = 0.0
    for lo, hi in DEFECT_BANDS.get(defect_type, []):
        mask = (freqs >= lo) & (freqs <= hi)
        defect_energy += float(np.sum(amps[mask] ** 2))
    ratio = defect_energy / total_energy
    score = 100.0 - min(ratio * 260.0, 100.0)
    return round(max(score, 0.0), 1)


def compute_health_generic(freqs, amps, signal):
    """Used for uploaded data of unknown origin: scores condition from the
    energy concentrated at high frequency (impact/corrugation-like) and very
    low frequency (joint/impulse-like) bands, then heuristically matches the
    closest known defect signature for labeling purposes only."""
    total_energy = float(np.sum(amps ** 2)) or 1e-9
    hf_ratio = float(np.sum(amps[freqs > 150] ** 2)) / total_energy
    lf_mask = (freqs > 0) & (freqs < 8)
    lf_ratio = float(np.sum(amps[lf_mask] ** 2)) / total_energy

    anomaly = hf_ratio * 150.0 + lf_ratio * 110.0
    score = round(max(0.0, 100.0 - min(anomaly, 100.0)), 1)
    status = classify_status(score)

    best_label, best_ratio = None, 0.0
    for dtype, bands in DEFECT_BANDS.items():
        if dtype is None:
            continue
        e = 0.0
        for lo, hi in bands:
            mask = (freqs >= lo) & (freqs <= hi)
            e += float(np.sum(amps[mask] ** 2))
        ratio = e / total_energy
        if ratio > best_ratio:
            best_ratio, best_label = ratio, dtype

    label = best_label if (status != "healthy" and best_ratio > 0.10) else None
    return score, status, label, dominant_frequency(freqs, amps)


def refresh_segment_health(seg):
    t, signal = generate_vibration_signal(seg["defect_type"], seg["severity"], seg["seed"])
    freqs, amps, _ = compute_fft(signal, FS)
    score = compute_health(freqs, amps, seg["defect_type"])
    seg["health_score"] = score
    seg["status"] = classify_status(score)
    seg["last_updated"] = datetime.now(timezone.utc).isoformat()
    return seg


def push_alert(seg):
    if seg["status"] == "healthy":
        return
    message = (
        f"{DEFECT_LABELS[seg['defect_type']]} detected — health score "
        f"{seg['health_score']}. {RECOMMENDED_ACTION[seg['defect_type']]}"
    )
    alert = {
        "id": f"A{len(_alerts) + 1:05d}-{seg['id']}-{int(time.time()*1000)}",
        "segment_id": seg["id"],
        "segment_name": seg["name"],
        "chainage_km": seg["chainage_km"],
        "status": seg["status"],
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    recent = [a for a in _alerts if a["segment_id"] == seg["id"]]
    if not recent or recent[-1]["status"] != seg["status"]:
        _alerts.append(alert)
        if len(_alerts) > 200:
            del _alerts[0]


def simulation_loop():
    """Background thread: slowly evolves demo-track defect severity to mimic
    real wear, with occasional simulated maintenance resets. Runs regardless
    of which mode is active so the demo track is always live and ready."""
    global _tick
    rng = random.Random()
    while True:
        with _lock:
            _tick += 1
            for seg in _segments:
                if seg["defect_type"] is None:
                    if rng.random() < 0.01:
                        seg["defect_type"] = rng.choice(
                            ["corrugation", "wheel_flat", "joint_gap", "rail_crack"]
                        )
                        seg["severity"] = 0.05
                else:
                    seg["severity"] = min(1.0, seg["severity"] + rng.uniform(0.0, 0.02))
                    if seg["status"] == "critical" and rng.random() < 0.06:
                        seg["defect_type"] = None
                        seg["severity"] = 0.0
                refresh_segment_health(seg)
                if app_state["mode"] == "simulated":
                    push_alert(seg)
        time.sleep(4)


# --------------------------------------------------------------------------
# CSV dataset ingestion
# --------------------------------------------------------------------------

REQUIRED_COLUMNS = {"sensor_id", "chainage_km", "acceleration_g"}


def parse_uploaded_csv(file_stream):
    """Parses a long-format CSV: one row per vibration sample.

    Required columns   : sensor_id, chainage_km, acceleration_g
    Optional columns    : sample_index (defaults to row order per sensor),
                           sampling_rate_hz (defaults to 1000 Hz),
                           speed_limit_kmph, sensor_name
    """
    try:
        df = pd.read_csv(file_stream)
    except Exception as e:
        raise ValueError(f"Could not read the file as CSV ({e}).")

    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            "Missing required column(s): " + ", ".join(sorted(missing)) +
            ". Columns found: " + ", ".join(df.columns)
        )

    if "sample_index" not in df.columns:
        df["sample_index"] = df.groupby("sensor_id").cumcount()
    if "sampling_rate_hz" not in df.columns:
        df["sampling_rate_hz"] = 1000.0

    segments, raw_map, warnings = [], {}, []

    for sensor_id, g in df.groupby("sensor_id", sort=False):
        g = g.sort_values("sample_index")
        signal = pd.to_numeric(g["acceleration_g"], errors="coerce").to_numpy(dtype=float)
        signal = signal[~np.isnan(signal)]
        if len(signal) < 32:
            warnings.append(f"Sensor '{sensor_id}' skipped — only {len(signal)} valid samples (need at least 32).")
            continue

        fs = float(g["sampling_rate_hz"].iloc[0]) if pd.notna(g["sampling_rate_hz"].iloc[0]) else 1000.0
        fs = fs if fs > 0 else 1000.0
        chainage = float(g["chainage_km"].iloc[0])
        speed_limit = (
            int(g["speed_limit_kmph"].iloc[0])
            if "speed_limit_kmph" in g.columns and pd.notna(g["speed_limit_kmph"].iloc[0])
            else 100
        )
        name = (
            str(g["sensor_name"].iloc[0])
            if "sensor_name" in g.columns and pd.notna(g["sensor_name"].iloc[0])
            else f"Sensor {sensor_id}"
        )

        seg_id = str(sensor_id)
        freqs, amps, _ = compute_fft(signal, fs)
        score, status, label, dom_freq = compute_health_generic(freqs, amps, signal)

        segments.append({
            "id": seg_id,
            "name": name,
            "chainage_km": round(chainage, 3),
            "speed_limit_kmph": speed_limit,
            "defect_type": label,
            "severity": round(max(0.0, (100.0 - score) / 100.0), 3),
            "health_score": score,
            "status": status,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        raw_map[seg_id] = {"signal": signal, "fs": fs}

    if not segments:
        raise ValueError("No usable sensor data found (each sensor_id needs at least 32 samples).")

    segments.sort(key=lambda s: s["chainage_km"])
    return segments, raw_map, warnings


# --------------------------------------------------------------------------
# Unified data access (works for both simulated + uploaded modes)
# --------------------------------------------------------------------------

def _find_simulated(seg_id):
    with _lock:
        for s in _segments:
            if s["id"] == seg_id:
                return s
    return None


def find_any_segment(seg_id):
    if app_state["mode"] == "uploaded" and app_state["uploaded"]:
        return next((s for s in app_state["uploaded"]["segments"] if s["id"] == seg_id), None)
    return _find_simulated(seg_id)


def get_signal(seg_id):
    """Returns (t, signal, fs) for a segment id in the currently active mode."""
    if app_state["mode"] == "uploaded" and app_state["uploaded"]:
        raw = app_state["uploaded"]["raw"].get(seg_id)
        if not raw:
            return None
        signal, fs = raw["signal"], raw["fs"]
        t = np.arange(len(signal)) / fs
        return t, signal, fs

    seg = _find_simulated(seg_id)
    if not seg:
        return None
    t, signal = generate_vibration_signal(seg["defect_type"], seg["severity"], seg["seed"])
    return t, signal, FS


def get_active_segments():
    lo, hi, _, _ = station_range(app_state["from_station"], app_state["to_station"])
    if app_state["mode"] == "uploaded" and app_state["uploaded"]:
        segs = list(app_state["uploaded"]["segments"])
    else:
        with _lock:
            segs = list(_segments)
    filtered = [s for s in segs if lo <= s["chainage_km"] <= hi]
    filtered.sort(key=lambda s: s["chainage_km"])
    return filtered


# --------------------------------------------------------------------------
# Page routes
# --------------------------------------------------------------------------

@app.route("/")
def root():
    return redirect(url_for("monitor_page"))


@app.route("/upload")
def upload_page():
    return render_template("upload.html", active_page="upload")


@app.route("/monitor")
def monitor_page():
    return render_template("monitor.html", active_page="monitor")


# --------------------------------------------------------------------------
# API routes
# --------------------------------------------------------------------------

@app.route("/api/stations")
def api_stations():
    return jsonify(STATIONS)


@app.route("/api/state")
def api_state():
    lo, hi, from_st, to_st = station_range(app_state["from_station"], app_state["to_station"])
    uploaded = app_state["uploaded"]
    return jsonify({
        "mode": app_state["mode"],
        "from_station": from_st,
        "to_station": to_st,
        "distance_km": round(hi - lo, 1),
        "maps_embed_url": maps_embed_url(from_st, to_st),
        "filename": uploaded["filename"] if uploaded else None,
        "uploaded_at": uploaded["uploaded_at"] if uploaded else None,
        "warnings": uploaded["warnings"] if uploaded else [],
        "sensor_count": len(uploaded["segments"]) if uploaded else N_SIM_SEGMENTS,
    })


@app.route("/api/select-stations", methods=["POST"])
def api_select_stations():
    data = request.get_json(silent=True) or {}
    from_code, to_code = data.get("from_station"), data.get("to_station")
    if not get_station(from_code) or not get_station(to_code):
        return jsonify({"error": "Invalid station code."}), 400
    with _lock:
        app_state["from_station"] = from_code
        app_state["to_station"] = to_code
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"error": "Please choose a CSV file to upload."}), 400
    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only .csv files are supported."}), 400

    try:
        segments, raw_map, warnings = parse_uploaded_csv(io.BytesIO(file.read()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error while parsing file: {e}"}), 400

    from_code = request.form.get("from_station") or STATIONS[0]["code"]
    to_code = request.form.get("to_station") or STATIONS[-1]["code"]
    if not get_station(from_code) or not get_station(to_code):
        return jsonify({"error": "Invalid station selection."}), 400

    with _lock:
        app_state["mode"] = "uploaded"
        app_state["from_station"] = from_code
        app_state["to_station"] = to_code
        app_state["uploaded"] = {
            "filename": file.filename,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "segments": segments,
            "raw": raw_map,
            "warnings": warnings,
        }
        _alerts.clear()
        for seg in segments:
            push_alert(seg)

    return jsonify({
        "ok": True,
        "filename": file.filename,
        "sensor_count": len(segments),
        "warnings": warnings,
    })


@app.route("/api/use-simulated", methods=["POST"])
def api_use_simulated():
    with _lock:
        app_state["mode"] = "simulated"
        app_state["uploaded"] = None
        _alerts.clear()
    return jsonify({"ok": True})


@app.route("/api/summary")
def api_summary():
    segs = get_active_segments()
    lo, hi, from_st, to_st = station_range(app_state["from_station"], app_state["to_station"])
    scores = [s["health_score"] for s in segs]
    return jsonify({
        "track_name": f"{from_st['name']} \u2013 {to_st['name']}",
        "total_km": round(hi - lo, 1),
        "total_segments": len(segs),
        "avg_health": round(float(np.mean(scores)), 1) if scores else 100.0,
        "healthy_count": sum(1 for s in segs if s["status"] == "healthy"),
        "warning_count": sum(1 for s in segs if s["status"] == "warning"),
        "critical_count": sum(1 for s in segs if s["status"] == "critical"),
        "active_alerts": sum(1 for s in segs if s["status"] != "healthy"),
        "mode": app_state["mode"],
        "tick": _tick,
        "server_time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/segments")
def api_segments():
    segs = get_active_segments()
    out = [{
        "id": s["id"],
        "name": s["name"],
        "chainage_km": s["chainage_km"],
        "speed_limit_kmph": s["speed_limit_kmph"],
        "defect_type": s["defect_type"],
        "defect_label": DEFECT_LABELS[s["defect_type"]],
        "severity": round(s["severity"], 3),
        "health_score": s["health_score"],
        "status": s["status"],
        "last_updated": s["last_updated"],
    } for s in segs]
    return jsonify(out)


@app.route("/api/segments/<seg_id>")
def api_segment_detail(seg_id):
    seg = find_any_segment(seg_id)
    sig = get_signal(seg_id)
    if not seg or sig is None:
        return jsonify({"error": "segment not found"}), 404
    t, signal, fs = sig
    freqs, amps, _ = compute_fft(signal, fs)

    return jsonify({
        "id": seg["id"],
        "name": seg["name"],
        "chainage_km": seg["chainage_km"],
        "speed_limit_kmph": seg["speed_limit_kmph"],
        "defect_type": seg["defect_type"],
        "defect_label": DEFECT_LABELS[seg["defect_type"]],
        "recommended_action": RECOMMENDED_ACTION[seg["defect_type"]],
        "severity": round(seg["severity"], 3),
        "health_score": seg["health_score"],
        "status": seg["status"],
        "dominant_frequency_hz": dominant_frequency(freqs, amps),
        "defect_bands": DEFECT_BANDS.get(seg["defect_type"], []),
        "sampling_rate_hz": fs,
        "time_series": {
            "t": [round(float(x), 5) for x in t[:400]],
            "signal": [round(float(x), 5) for x in signal[:400]],
        },
        "spectrum": {
            "freqs": [round(float(x), 2) for x in freqs],
            "amps": [round(float(x), 5) for x in amps],
        },
    })


@app.route("/api/segments/<seg_id>/fourier")
def api_segment_fourier(seg_id):
    sig = get_signal(seg_id)
    if sig is None:
        return jsonify({"error": "segment not found"}), 404
    t, signal, fs = sig

    n_harmonics = request.args.get("harmonics", default=8, type=int)
    n_harmonics = max(1, min(n_harmonics, 60))

    N = len(signal)
    F = np.fft.rfft(signal)
    idx_sorted = np.argsort(-np.abs(F))
    keep = idx_sorted[:n_harmonics]
    F_trunc = np.zeros_like(F)
    F_trunc[keep] = F[keep]
    reconstructed = np.fft.irfft(F_trunc, n=N)

    freqs = np.fft.rfftfreq(N, d=1.0 / fs)
    kept_freqs = sorted(round(float(freqs[i]), 1) for i in keep)

    mse = float(np.mean((signal - reconstructed) ** 2))
    energy = float(np.mean(signal ** 2)) or 1e-9
    fidelity = round(max(0.0, 100.0 * (1 - mse / energy)), 1)

    step = max(1, N // 400)
    return jsonify({
        "id": seg_id,
        "harmonics_used": n_harmonics,
        "kept_frequencies_hz": kept_freqs,
        "fidelity_pct": fidelity,
        "t": [round(float(x), 5) for x in t[::step]],
        "original": [round(float(x), 5) for x in signal[::step]],
        "reconstructed": [round(float(x), 5) for x in reconstructed[::step]],
    })


@app.route("/api/alerts")
def api_alerts():
    with _lock:
        alerts = list(reversed(_alerts))[:50]
    return jsonify(alerts)


@app.route("/api/health")
def health_check():
    return jsonify({"status": "ok", "tick": _tick})


def _bootstrap():
    global _segments
    _segments = init_segments()
    with _lock:
        for seg in _segments:
            refresh_segment_health(seg)
    t = threading.Thread(target=simulation_loop, daemon=True)
    t.start()


_bootstrap()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
