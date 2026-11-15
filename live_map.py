#!/usr/bin/env python3
import glob
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from findmy import AppleAccount, RemoteAnisetteProvider
from findmy.keys import KeyPair

ANISETTE_URL = os.environ.get("ANISETTE_URL", "http://localhost:6969")
ACCOUNT_FILE = os.environ.get("ACCOUNT_FILE", "account.json")
PORT = int(os.environ.get("PORT", "8080"))

account_lock = threading.Lock()
account_instance = None
geocode_cache = {}
geofences = {}

devices_data = {}

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def get_address(lat, lon):
    if lat is None or lon is None:
        return "Unknown"
    ckey = (round(lat, 4), round(lon, 4))
    if ckey in geocode_cache:
        return geocode_cache[ckey]

    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=18"
    req = urllib.request.Request(url, headers={"User-Agent": "ESP32-AirTag-Tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addr = data.get("address", {})
            parts = []
            for k in ["road", "neighbourhood", "suburb", "city", "town", "state"]:
                if k in addr:
                    parts.append(addr[k])
            res = ", ".join(parts) if parts else data.get("display_name", f"{lat:.4f}, {lon:.4f}")
            geocode_cache[ckey] = res
            return res
    except Exception:
        return f"{lat:.4f}, {lon:.4f}"

def discover_device_files():
    files = glob.glob("*.json")
    dev_files = {}
    for f in sorted(files):
        if f in ("account.json", "package.json", "tsconfig.json"):
            continue
        dev_name = os.path.splitext(f)[0]
        dev_files[dev_name] = f
    return dev_files

def get_keys_for_file(filepath):
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        obj = data[0] if isinstance(data, list) else data
        keys = [KeyPair.from_b64(obj["privateKey"])]
        for k in obj.get("additionalKeys", []):
            keys.append(KeyPair.from_b64(k))
        return keys
    except Exception as e:
        print(f"Error reading keys from {filepath}: {e}")
        return []

def init_account():
    global account_instance
    if not os.path.exists(ACCOUNT_FILE):
        return False
    try:
        provider = RemoteAnisetteProvider(ANISETTE_URL)
        account_instance = AppleAccount.from_json(ACCOUNT_FILE)
        return True
    except Exception as e:
        print("Failed to load AppleAccount:", e)
        return False

def calculate_trip_metrics(reports):
    if not reports:
        return {"total_distance_m": 0, "stop_count": 0, "duration_s": 0}

    sorted_pts = sorted(reports, key=lambda r: r.timestamp)
    total_dist = 0.0
    stops = 0

    for i in range(1, len(sorted_pts)):
        d = haversine_distance(
            sorted_pts[i - 1].latitude,
            sorted_pts[i - 1].longitude,
            sorted_pts[i].latitude,
            sorted_pts[i].longitude,
        )
        total_dist += d
        if d < 20:
            stops += 1

    first_t = sorted_pts[0].timestamp
    last_t = sorted_pts[-1].timestamp
    duration_s = max(0, int((last_t - first_t).total_seconds()))

    return {
        "total_distance_m": round(total_dist),
        "stop_count": stops,
        "duration_s": duration_s,
    }

def update_device(dev_id, filepath):
    global account_instance, devices_data
    if not account_instance:
        if not init_account():
            return

    keys = get_keys_for_file(filepath)
    if not keys:
        return

    try:
        with account_lock:
            hist = account_instance.fetch_location_history(keys)

        all_reports = []
        for k, reps in hist.items():
            if reps:
                all_reports.extend(reps)

        if not all_reports:
            return

        all_reports.sort(key=lambda r: r.timestamp, reverse=True)
        top = all_reports[0]
        address = get_address(top.latitude, top.longitude)
        metrics = calculate_trip_metrics(all_reports)

        pts = []
        for r in all_reports:
            pts.append({
                "lat": r.latitude,
                "lon": r.longitude,
                "time": str(r.timestamp),
                "confidence": r.confidence,
                "status": r.status,
            })

        gf = geofences.get(dev_id)
        gf_status = "disabled"
        gf_dist = 0
        if gf and gf.get("enabled"):
            gf_dist = round(haversine_distance(top.latitude, top.longitude, gf["lat"], gf["lon"]))
            gf_status = "inside" if gf_dist <= gf["radius"] else "breach"

        devices_data[dev_id] = {
            "id": dev_id,
            "lat": top.latitude,
            "lon": top.longitude,
            "timestamp": str(top.timestamp),
            "confidence": top.confidence,
            "status": top.status,
            "address": address,
            "history": pts,
            "metrics": metrics,
            "geofence": {
                "status": gf_status,
                "distance": gf_dist,
                "radius": gf.get("radius") if gf else 200,
                "lat": gf.get("lat") if gf else top.latitude,
                "lon": gf.get("lon") if gf else top.longitude,
            },
            "last_check": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        print(f"Error updating device {dev_id}: {e}")

def update_all_devices():
    devs = discover_device_files()
    for dev_id, fpath in devs.items():
        update_device(dev_id, fpath)

def poll_loop():
    while True:
        update_all_devices()
        time.sleep(60)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP32 AirTag Live Map</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        :root {
            --bg-glass: rgba(255, 255, 255, 0.92);
            --primary: #0071e3;
            --success: #34c759;
            --danger: #ff3b30;
            --text-dark: #1d1d1f;
            --text-sub: #6e6e73;
        }
        body, html { margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif; }
        #map { width: 100%; height: 100%; }

        .glass-panel {
            background: var(--bg-glass);
            border-radius: 14px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.12);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255,255,255,0.6);
            z-index: 1000;
        }

        .header-panel {
            position: absolute;
            top: 16px;
            left: 16px;
            right: 16px;
            padding: 12px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            max-width: 900px;
            margin: 0 auto;
        }

        .header-title { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 16px; color: var(--text-dark); }
        .pulse { width: 10px; height: 10px; background: var(--success); border-radius: 50%; box-shadow: 0 0 0 rgba(52,199,89, 0.4); animation: pulse 2s infinite; }
        @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(52,199,89, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(52,199,89, 0); } 100% { box-shadow: 0 0 0 0 rgba(52,199,89, 0); } }

        .dev-select {
            padding: 6px 12px;
            border-radius: 8px;
            border: 1px solid #d2d2d7;
            font-size: 14px;
            font-weight: 500;
            background: #fff;
            color: var(--text-dark);
            outline: none;
            cursor: pointer;
        }

        .stats-panel {
            position: absolute;
            top: 76px;
            left: 16px;
            width: 320px;
            padding: 16px;
        }

        .stats-panel h3 { margin: 0 0 10px 0; font-size: 15px; color: var(--text-dark); display: flex; justify-content: space-between; align-items: center; }
        .stat-row { display: flex; justify-content: space-between; font-size: 13px; margin: 6px 0; color: var(--text-sub); }
        .stat-val { font-weight: 600; color: var(--text-dark); }

        .address-box {
            background: rgba(0,0,0,0.04);
            border-radius: 8px;
            padding: 8px 10px;
            font-size: 12px;
            color: var(--text-dark);
            margin: 8px 0;
            line-height: 1.4;
        }

        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-inside { background: #d4edda; color: #155724; }
        .badge-breach { background: #f8d7da; color: #721c24; animation: alertBlink 1s infinite; }
        @keyframes alertBlink { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

        .playback-panel {
            position: absolute;
            bottom: 20px;
            left: 16px;
            right: 16px;
            max-width: 680px;
            margin: 0 auto;
            padding: 12px 18px;
        }

        .controls { display: flex; align-items: center; gap: 12px; }
        .btn {
            background: var(--primary);
            color: #fff;
            border: none;
            border-radius: 8px;
            padding: 7px 14px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.15s;
        }
        .btn:hover { background: #0077ed; }
        .btn-secondary { background: #e8e8ed; color: var(--text-dark); }
        .btn-secondary:hover { background: #dcdce2; }

        .slider { flex: 1; accent-color: var(--primary); cursor: pointer; }
        .time-label { font-size: 12px; font-weight: 500; color: var(--text-dark); min-width: 130px; text-align: right; }
    </style>
</head>
<body>
    <div class="glass-panel header-panel">
        <div class="header-title">
            <span class="pulse"></span>
            ESP32 AirTag Tracker
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
            <select id="deviceSelector" class="dev-select" onchange="onDeviceChange()"></select>
            <button class="btn btn-secondary" onclick="refreshCurrentDevice()">↻ Update</button>
            <a id="gmapsLink" class="btn" style="background:var(--success); text-decoration:none;" target="_blank">Google Maps</a>
        </div>
    </div>

    <div class="glass-panel stats-panel">
        <h3>
            <span id="cardDeviceName">Device</span>
            <span id="gfBadge" class="badge badge-inside">Inside Safe Zone</span>
        </h3>
        <div class="address-box" id="cardAddress">Fetching address...</div>
        <div class="stat-row"><span>Last Report:</span><span class="stat-val" id="cardTimestamp">--</span></div>
        <div class="stat-row"><span>Accuracy:</span><span class="stat-val" id="cardAccuracy">--</span></div>
        <div class="stat-row"><span>Total Traveled:</span><span class="stat-val" id="cardDistance">--</span></div>
        <div class="stat-row"><span>Detected Stops:</span><span class="stat-val" id="cardStops">--</span></div>
        <div class="stat-row"><span>Dwell Time:</span><span class="stat-val" id="cardDuration">--</span></div>
        <div style="margin-top: 10px; border-top: 1px solid #e5e5ea; padding-top: 8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:12px; color:var(--text-sub);">Safe Zone Radius:</span>
                <span style="font-size:12px; font-weight:600;" id="gfRadiusLabel">200m</span>
            </div>
            <input type="range" class="slider" id="gfSlider" min="50" max="1000" step="50" value="200" style="width:100%; margin-top:4px;" oninput="updateGeofenceRadius(this.value)">
            <button class="btn btn-secondary" style="width:100%; margin-top:6px; font-size:12px;" onclick="setGeofenceHere()">Set Safe Zone at Current Point</button>
        </div>
    </div>

    <div class="glass-panel playback-panel">
        <div class="controls">
            <button id="playBtn" class="btn" onclick="togglePlayback()">▶ Play Trail</button>
            <input type="range" id="timeScrubber" class="slider" min="0" max="0" value="0" oninput="onScrub(this.value)">
            <select id="speedSelect" class="dev-select" style="padding:4px 8px; font-size:12px;" onchange="playbackSpeed = parseFloat(this.value)">
                <option value="1">1x</option>
                <option value="3" selected>3x</option>
                <option value="8">8x</option>
            </select>
            <span id="scrubTime" class="time-label">--</span>
        </div>
    </div>

    <div id="map"></div>

    <script>
        var map = L.map('map', { zoomControl: false }).setView([28.985, 77.090], 16);
        L.control.zoom({ position: 'bottomright' }).addTo(map);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }).addTo(map);

        var currentDevice = "";
        var activeHistory = [];
        var marker = null;
        var playheadMarker = null;
        var trailPolyline = null;
        var geofenceCircle = null;

        var isPlaying = false;
        var playIdx = 0;
        var playbackSpeed = 3;
        var playInterval = null;

        if ("Notification" in window && Notification.permission !== "granted" && Notification.permission !== "denied") {
            Notification.requestPermission();
        }

        function loadDevices() {
            fetch('/api/devices').then(r => r.json()).then(devs => {
                var sel = document.getElementById('deviceSelector');
                sel.innerHTML = '';
                devs.forEach(d => {
                    var opt = document.createElement('option');
                    opt.value = d;
                    opt.innerText = d;
                    sel.appendChild(opt);
                });
                if (devs.length > 0) {
                    currentDevice = devs[0];
                    fetchDeviceData(currentDevice, false);
                }
            });
        }

        function onDeviceChange() {
            currentDevice = document.getElementById('deviceSelector').value;
            fetchDeviceData(currentDevice, false);
        }

        function refreshCurrentDevice() {
            if (currentDevice) {
                fetchDeviceData(currentDevice, true);
            }
        }

        function formatDuration(sec) {
            var h = Math.floor(sec / 3600);
            var m = Math.floor((sec % 3600) / 60);
            return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
        }

        function fetchDeviceData(devId, force) {
            var url = '/api/location?device=' + encodeURIComponent(devId) + (force ? '&refresh=1' : '');
            fetch(url).then(r => r.json()).then(data => {
                if (!data || !data.lat) return;

                document.getElementById('cardDeviceName').innerText = data.id;
                document.getElementById('cardAddress').innerText = data.address || (data.lat.toFixed(5) + ', ' + data.lon.toFixed(5));
                document.getElementById('cardTimestamp').innerText = data.timestamp;
                document.getElementById('cardAccuracy').innerText = (data.confidence || 0) + ' m';
                document.getElementById('gmapsLink').href = 'https://www.google.com/maps?q=' + data.lat + ',' + data.lon;

                if (data.metrics) {
                    var distKm = (data.metrics.total_distance_m / 1000).toFixed(2);
                    document.getElementById('cardDistance').innerText = distKm + ' km';
                    document.getElementById('cardStops').innerText = data.metrics.stop_count;
                    document.getElementById('cardDuration').innerText = formatDuration(data.metrics.duration_s);
                }

                activeHistory = (data.history || []).slice().reverse();
                var scrubber = document.getElementById('timeScrubber');
                scrubber.max = Math.max(0, activeHistory.length - 1);
                scrubber.value = scrubber.max;
                if (activeHistory.length > 0) {
                    document.getElementById('scrubTime').innerText = activeHistory[activeHistory.length - 1].time;
                }

                var pos = [data.lat, data.lon];
                if (!marker) {
                    marker = L.circleMarker(pos, { radius: 9, fillColor: '#0071e3', color: '#fff', weight: 3, opacity: 1, fillOpacity: 0.9 }).addTo(map);
                    map.setView(pos, 17);
                } else {
                    marker.setLatLng(pos);
                }

                if (activeHistory.length > 1) {
                    var lineCoords = activeHistory.map(p => [p.lat, p.lon]);
                    if (trailPolyline) map.removeLayer(trailPolyline);
                    trailPolyline = L.polyline(lineCoords, { color: '#0071e3', weight: 4, opacity: 0.7, dashArray: '6, 8' }).addTo(map);
                }

                var gf = data.geofence;
                var gfBadge = document.getElementById('gfBadge');
                if (gf && gf.status !== "disabled") {
                    var gfCenter = [gf.lat, gf.lon];
                    if (!geofenceCircle) {
                        geofenceCircle = L.circle(gfCenter, { radius: gf.radius, color: '#34c759', fillColor: '#34c759', fillOpacity: 0.12 }).addTo(map);
                    } else {
                        geofenceCircle.setLatLng(gfCenter);
                        geofenceCircle.setRadius(gf.radius);
                    }

                    if (gf.status === "breach") {
                        gfBadge.className = "badge badge-breach";
                        gfBadge.innerText = "OUTSIDE SAFE ZONE (" + gf.distance + "m)";
                        geofenceCircle.setStyle({ color: '#ff3b30', fillColor: '#ff3b30' });
                        if (Notification.permission === "granted") {
                            new Notification("🚨 Safe Zone Alert", { body: data.id + " moved outside safe zone! Distance: " + gf.distance + "m" });
                        }
                    } else {
                        gfBadge.className = "badge badge-inside";
                        gfBadge.innerText = "Inside Safe Zone";
                        geofenceCircle.setStyle({ color: '#34c759', fillColor: '#34c759' });
                    }
                } else {
                    gfBadge.style.display = "none";
                }
            });
        }

        function setGeofenceHere() {
            var r = parseInt(document.getElementById('gfSlider').value);
            fetch('/api/geofence', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ device: currentDevice, action: 'set_current', radius: r })
            }).then(() => fetchDeviceData(currentDevice, false));
        }

        function updateGeofenceRadius(val) {
            document.getElementById('gfRadiusLabel').innerText = val + 'm';
            if (geofenceCircle) {
                geofenceCircle.setRadius(parseInt(val));
            }
        }

        function onScrub(val) {
            playIdx = parseInt(val);
            updatePlayhead(playIdx);
        }

        function updatePlayhead(idx) {
            if (!activeHistory || idx >= activeHistory.length) return;
            var pt = activeHistory[idx];
            var latlng = [pt.lat, pt.lon];
            document.getElementById('scrubTime').innerText = pt.time;

            if (!playheadMarker) {
                playheadMarker = L.circleMarker(latlng, { radius: 8, fillColor: '#ff9500', color: '#fff', weight: 2, fillOpacity: 1 }).addTo(map);
            } else {
                playheadMarker.setLatLng(latlng);
            }
        }

        function togglePlayback() {
            var btn = document.getElementById('playBtn');
            if (isPlaying) {
                clearInterval(playInterval);
                isPlaying = false;
                btn.innerText = '▶ Play Trail';
            } else {
                if (playIdx >= activeHistory.length - 1) playIdx = 0;
                isPlaying = true;
                btn.innerText = '⏸ Pause';
                playInterval = setInterval(stepPlayhead, 1000 / playbackSpeed);
            }
        }

        function stepPlayhead() {
            if (playIdx >= activeHistory.length - 1) {
                togglePlayback();
                return;
            }
            playIdx++;
            document.getElementById('timeScrubber').value = playIdx;
            updatePlayhead(playIdx);
        }

        loadDevices();
        setInterval(() => { if (!isPlaying && currentDevice) fetchDeviceData(currentDevice, false); }, 30000);
    </script>
</body>
</html>
"""

class RequestHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        urlp = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(urlp.query)

        if urlp.path == "/api/devices":
            devs = list(discover_device_files().keys())
            self.send_json(devs)
        elif urlp.path == "/api/location":
            dev_id = qs.get("device", ["mytag"])[0]
            force = qs.get("refresh", ["0"])[0] == "1"

            if force or dev_id not in devices_data:
                devs = discover_device_files()
                if dev_id in devs:
                    update_device(dev_id, devs[dev_id])

            data = devices_data.get(dev_id, {})
            self.send_json(data)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

    def do_POST(self):
        urlp = urllib.parse.urlparse(self.path)
        if urlp.path == "/api/geofence":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            dev_id = body.get("device", "mytag")
            radius = body.get("radius", 200)

            cur = devices_data.get(dev_id)
            if cur and "lat" in cur:
                geofences[dev_id] = {
                    "enabled": True,
                    "lat": cur["lat"],
                    "lon": cur["lon"],
                    "radius": radius,
                }
                update_device(dev_id, discover_device_files().get(dev_id, f"{dev_id}.json"))

            self.send_json({"status": "ok", "geofence": geofences.get(dev_id)})
        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))

    def log_message(self, format, *args):
        pass

def main():
    print("Starting Enhanced Live Map Dashboard...")
    init_account()
    update_all_devices()

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", PORT), RequestHandler)
    print(f"Enhanced Live Map running on port {PORT}")
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
