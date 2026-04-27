from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path
import subprocess
import sys
import html
import json
import csv
import time
import traceback

ROOT = Path(r"C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS")
STRATS = ROOT / "_strategies"
REGISTRY = ROOT / "_registry" / "data_index.json"
RUNS = ROOT / "_ui_runs"
REPORTS = ROOT / "_reports"
PY = sys.executable

RUNS.mkdir(exist_ok=True)

class Server(ThreadingHTTPServer):
    allow_reuse_address = True

def read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def load_registry():
    return read_json(REGISTRY, [])

def strategy_files():
    STRATS.mkdir(exist_ok=True)
    out = []
    for p in sorted(STRATS.glob("*.py"), key=lambda x: x.name.lower()):
        n = p.name.lower()
        if n.startswith("_"):
            continue
        if n == "__init__.py":
            continue
        if n.startswith("common__"):
            continue
        if n in {"contract.py", "indicators.py", "panel.py"}:
            continue
        out.append(p)
    return out

def unique(rows, key):
    return sorted(set(str(x.get(key, "")).strip() for x in rows if str(x.get(key, "")).strip()))

def selected_rows(rows, exchange, symbol, timeframes, max_files):
    tfs = [x.strip().lower() for x in str(timeframes).split(",") if x.strip()]
    out = []
    for x in rows:
        if exchange != "all" and str(x.get("exchange","")).lower() != str(exchange).lower():
            continue
        if symbol != "all" and str(x.get("symbol","")).upper() != str(symbol).upper():
            continue
        if "all" not in tfs and str(x.get("timeframe","")).lower() not in tfs:
            continue
        if str(x.get("timeframe","")).lower() == "funding":
            continue
        out.append(x)
    if int(max_files) > 0:
        out = out[:int(max_files)]
    return out

def latest_state_file():
    files = sorted(RUNS.glob("run_*.state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None

def latest_log_file():
    files = sorted(RUNS.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None

def tail(path, n=50000):
    try:
        if not path:
            return ""
        p = Path(path)
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")[-n:]
    except Exception as e:
        return "LOG_READ_ERROR: " + repr(e)

def leaderboard_html(strategy):
    if not strategy:
        return "<p>No strategy yet.</p>"

    path = REPORTS / (strategy + "_leaderboard.csv")
    if not path.exists():
        return "<p>No leaderboard yet.</p>"

    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            for i, r in enumerate(csv.DictReader(f)):
                if i >= 50:
                    break
                rows.append(r)
    except Exception as e:
        return "<pre>" + html.escape(repr(e)) + "</pre>"

    if not rows:
        return "<p>Empty leaderboard.</p>"

    cols = ["exchange", "symbol", "timeframe", "trades", "win_rate", "net_pnl_pct", "profit_factor", "max_drawdown_pct", "verdict"]
    s = "<table><tr>" + "".join("<th>"+html.escape(c)+"</th>" for c in cols) + "</tr>"
    for r in rows:
        verdict = str(r.get("verdict", ""))
        cls = "goodrow" if verdict == "PROMOTE" else "watchrow" if verdict == "WATCHLIST" else "badrow"
        s += '<tr class="' + cls + '">' + "".join("<td>"+html.escape(str(r.get(c, "")))+"</td>" for c in cols) + "</tr>"
    s += "</table>"
    return s

def page(title, body, refresh=False):
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ''
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
""" + meta + """
<title>""" + html.escape(title) + """</title>
<style>
body{margin:0;background:#070b18;color:#eef2ff;font-family:Arial,sans-serif}
.wrap{max-width:1500px;margin:20px auto;padding:0 18px}
.card{background:#111a33;border:1px solid #263254;border-radius:18px;padding:22px}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
label{display:block;font-weight:900;margin:14px 0 6px}
select,input{width:100%;box-sizing:border-box;background:#070b18;color:white;border:1px solid #33405f;border-radius:10px;padding:12px}
button,a.btn{display:inline-block;text-decoration:none;margin-top:18px;margin-right:8px;padding:14px 18px;border:0;border-radius:12px;background:#7183ff;color:white;font-weight:900;cursor:pointer}
.panel{background:#080d1d;border:1px solid #25304f;border-radius:14px;padding:14px;margin-top:14px}
.big{font-size:24px;font-weight:900}
.kpi{font-size:28px;font-weight:900}
.badge{display:inline-block;border-radius:999px;padding:7px 12px;margin:4px;background:#24345e;color:#dbe4ff;font-weight:900}
.running{background:#164d2c;color:#9dffbf}
.finished{background:#164d2c;color:#9dffbf}
.crashed{background:#5a1e28;color:#ffb0b0}
.warn{background:#5a4a1e;color:#ffe58a}
.progress{height:22px;background:#071020;border:1px solid #33405f;border-radius:999px;overflow:hidden;margin:10px 0}
.bar{height:100%;background:#7183ff}
pre{background:#050814;border:1px solid #202846;border-radius:12px;padding:14px;overflow:auto;max-height:520px;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px}
th,td{border-bottom:1px solid #263254;padding:8px;text-align:left}
th{color:#9fb0ff}
.goodrow td{color:#9dffbf}
.watchrow td{color:#ffe58a}
.badrow td{color:#ff9b9b}
.err{background:#37111a;border:1px solid #9f3348;color:#ffd3da}
</style>
</head>
<body><div class="wrap"><div class="card">
""" + body + """
</div></div></body></html>"""

class App(BaseHTTPRequestHandler):
    def send_html(self, title, body, refresh=False, code=200):
        data = page(title, body, refresh).encode("utf-8", errors="replace")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def safe(self, fn):
        try:
            fn()
        except Exception:
            err = traceback.format_exc()
            self.send_html(
                "UI Error",
                '<h1>UI Error</h1><div class="panel err"><b>Real error shown below:</b><pre>' + html.escape(err) + '</pre></div><a class="btn" href="/">Back Home</a>',
                False,
                500,
            )

    def do_GET(self):
        self.safe(self._get)

    def do_POST(self):
        self.safe(self._post)

    def _get(self):
        path = urlparse(self.path).path

        if path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if path == "/run":
            self.redirect("/")
            return

        if path == "/status":
            state_path = latest_state_file()
            state = read_json(state_path, {}) if state_path else {}

            if not state:
                log = latest_log_file()
                self.send_html(
                    "Live Status",
                    '<h1>Live Status</h1><div class="panel warn">No active run state yet.</div><a class="btn" href="/">Back Home</a><h2>Latest log</h2><pre>' + html.escape(tail(log)) + '</pre>',
                    True,
                )
                return

            status = str(state.get("status", "UNKNOWN"))
            refresh = status in {"STARTING", "LOADING_STRATEGY", "RUNNING"}
            cls = "running" if refresh else "finished" if status == "FINISHED" else "crashed"
            pct = float(state.get("percent", 0) or 0)

            body = """
<h1>Live Backtest Status</h1>
<a class="btn" href="/">Back Home</a>
<a class="btn" href="/status">Refresh</a>

<div class="panel">
<div class="big"><span class="badge """ + cls + """">""" + html.escape(status) + """</span></div>
<div class="progress"><div class="bar" style="width:""" + html.escape(str(pct)) + """%"></div></div>
<div class="kpi">""" + html.escape(str(state.get("current_index", 0))) + """ / """ + html.escape(str(state.get("total_files", 0))) + """ files — """ + html.escape(str(pct)) + """%</div>
<p><b>Current:</b> """ + html.escape(str(state.get("current_exchange", ""))) + """ / """ + html.escape(str(state.get("current_symbol", ""))) + """ / """ + html.escape(str(state.get("current_timeframe", ""))) + """</p>
<p><b>Current file:</b> """ + html.escape(str(state.get("current_file", ""))) + """</p>
<p><b>Updated:</b> """ + html.escape(str(state.get("updated_at", ""))) + """</p>
</div>

<div class="grid">
<div class="panel"><b>Strategy</b><br>""" + html.escape(str(state.get("strategy_name", ""))) + """</div>
<div class="panel"><b>Selected files</b><br>""" + html.escape(str(state.get("selected_files", ""))) + """</div>
<div class="panel"><b>Selected size</b><br>""" + html.escape(str(state.get("selected_size_mb", ""))) + """ MB / """ + html.escape(str(state.get("selected_size_gb", ""))) + """ GB</div>
</div>

<div class="grid">
<div class="panel"><b>Exchange filter</b><br>""" + html.escape(str(state.get("exchange_filter", ""))) + """</div>
<div class="panel"><b>Symbol filter</b><br>""" + html.escape(str(state.get("symbol_filter", ""))) + """</div>
<div class="panel"><b>Timeframes</b><br>""" + html.escape(str(state.get("timeframe_filter", ""))) + """</div>
</div>

<div class="grid">
<div class="panel"><b>Tested</b><br>""" + html.escape(str(state.get("tested", 0))) + """</div>
<div class="panel"><b>Failures</b><br>""" + html.escape(str(state.get("failures", 0))) + """</div>
<div class="panel"><b>Seconds</b><br>""" + html.escape(str(state.get("seconds", ""))) + """</div>
</div>

<div class="panel"><b>Actual exchanges</b><br>""" + html.escape(", ".join(state.get("exchanges", []))) + """</div>
<div class="panel"><b>Symbol sample</b><br>""" + html.escape(", ".join(state.get("symbols_sample", []))) + """</div>
"""

            if state.get("error"):
                body += '<div class="panel err"><b>Error</b><pre>' + html.escape(str(state.get("error", ""))) + '</pre></div>'

            body += """
<h2>Live Log</h2>
<pre>""" + html.escape(tail(state.get("log"))) + """</pre>
<h2>Leaderboard</h2>
""" + leaderboard_html(str(state.get("strategy_name", "")))

            self.send_html("Live Status", body, refresh)
            return

        rows = load_registry()
        strats = strategy_files()

        total_mb = round(sum(float(x.get("size_mb", 0)) for x in rows if str(x.get("timeframe", "")).lower() != "funding"), 2)
        total_gb = round(total_mb / 1024, 3)
        total_files = len([x for x in rows if str(x.get("timeframe", "")).lower() != "funding"])

        strat_opts = "".join('<option value="' + html.escape(str(p)) + '">' + html.escape(p.stem) + '</option>' for p in strats)
        ex_opts = '<option value="all">all</option>' + "".join('<option value="' + html.escape(x) + '">' + html.escape(x) + '</option>' for x in unique(rows, "exchange"))
        sym_opts = '<option value="all">all</option>' + "".join('<option value="' + html.escape(x) + '">' + html.escape(x) + '</option>' for x in unique(rows, "symbol"))
        tf_opts = """
<option value="1m,5m,15m,30m,1h,4h,1d" selected>All core: 1m,5m,15m,30m,1h,4h,1d</option>
<option value="1m,5m,15m">Scalp: 1m,5m,15m</option>
<option value="5m,15m,1h">Intraday: 5m,15m,1h</option>
<option value="1h,4h,1d">Swing: 1h,4h,1d</option>
<option value="1m">1m only</option>
<option value="5m">5m only</option>
<option value="15m">15m only</option>
<option value="1h">1h only</option>
<option value="4h">4h only</option>
<option value="1d">1d only</option>
"""

        body = """
<h1>Backtest Cockpit</h1>

<div class="panel">
<div class="big">Active data loaded</div>
<span class="badge">Files: """ + html.escape(str(total_files)) + """</span>
<span class="badge">Size: """ + html.escape(str(total_mb)) + """ MB / """ + html.escape(str(total_gb)) + """ GB</span>
<span class="badge">Real strategies: """ + html.escape(str(len(strats))) + """</span>
</div>

<form method="post" action="/run">

<div class="grid">
<div><label>Python Strategy File</label><select name="strategy" required>""" + strat_opts + """</select></div>
<div><label>Exchange</label><select name="exchange">""" + ex_opts + """</select></div>
<div><label>Symbol</label><select name="symbols">""" + sym_opts + """</select></div>
</div>

<div class="grid">
<div><label>Timeframes</label><select name="timeframes">""" + tf_opts + """</select></div>
<div><label>Max Files</label><select name="max_files"><option value="25">25 quick</option><option value="100">100 medium</option><option value="999999" selected>All selected</option></select></div>
<div><label>Max Bars</label><select name="max_bars"><option value="10000">10k fast</option><option value="30000">30k balanced</option><option value="50000" selected>50k deep</option><option value="0">All bars slow</option></select></div>
</div>

<div class="grid">
<div><label>Starting Capital</label><select name="starting_capital"><option value="500">500</option><option value="1000" selected>1000</option><option value="2500">2500</option><option value="5000">5000</option></select></div>
<div><label>Maker Fee</label><select name="maker_fee"><option value="0">0</option><option value="0.0005">0.05%</option><option value="0.001" selected>0.1%</option><option value="0.0015">0.15%</option></select></div>
<div><label>Taker Fee</label><select name="taker_fee"><option value="0">0</option><option value="0.0005">0.05%</option><option value="0.001" selected>0.1%</option><option value="0.0015">0.15%</option></select></div>
</div>

<div class="grid">
<div><label>Slippage</label><select name="slippage"><option value="0">0</option><option value="0.0002">0.02%</option><option value="0.0005" selected>0.05%</option><option value="0.001">0.1%</option></select></div>
<div><label>Max Hold Bars</label><select name="max_hold_bars"><option value="24">24</option><option value="48">48</option><option value="72">72</option><option value="96" selected>96</option><option value="168">168</option></select></div>
<div><label>Signal Step</label><select name="signal_step"><option value="1" selected>Every candle</option><option value="3">Every 3 candles</option><option value="5">Every 5 candles</option><option value="10">Every 10 candles</option></select></div>
</div>

<div class="grid">
<div><label>Default TP</label><select name="default_tp"><option value="0.015">1.5%</option><option value="0.022">2.2%</option><option value="0.03" selected>3%</option><option value="0.04">4%</option><option value="0.05">5%</option></select></div>
<div><label>Default SL</label><select name="default_sl"><option value="0.01">1%</option><option value="0.012">1.2%</option><option value="0.015" selected>1.5%</option><option value="0.018">1.8%</option><option value="0.02">2%</option></select></div>
<div><label>Strategy Window Bars</label><select name="strategy_window_bars"><option value="300">300</option><option value="500" selected>500</option><option value="700">700</option><option value="800">800</option><option value="1200">1200</option></select></div>
</div>

<div class="panel">
<b>What will happen after Run:</b><br>
1. It creates a live state file.<br>
2. It redirects to status page.<br>
3. Status page shows RUNNING / FINISHED / CRASHED, current exchange, symbol, timeframe, GB, progress, log and leaderboard.
</div>

<button type="submit">RUN SELECTED PY STRATEGY</button>
<a class="btn" href="/status">Live Status</a>
</form>
"""
        self.send_html("Backtest Cockpit", body)

    def _post(self):
        path = urlparse(self.path).path
        if path != "/run":
            self.redirect("/")
            return

        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", errors="replace")
        data = parse_qs(raw)

        def v(k, d):
            return data.get(k, [d])[0]

        strategy = v("strategy", "")
        if not strategy:
            self.send_html("Missing Strategy", '<h1>Missing strategy</h1><a class="btn" href="/">Back</a>', False, 400)
            return

        exchange = v("exchange", "all")
        symbols = v("symbols", "all")
        timeframes = v("timeframes", "1m,5m,15m,30m,1h,4h,1d")
        max_files = int(v("max_files", "999999"))

        rows = selected_rows(load_registry(), exchange, symbols, timeframes, max_files)
        mb = round(sum(float(x.get("size_mb", 0)) for x in rows), 2)
        strategy_name = Path(strategy).stem

        run_id = "run_" + time.strftime("%Y%m%d_%H%M%S") + "__" + strategy_name
        log = RUNS / (run_id + ".log")
        state = RUNS / (run_id + ".state.json")

        initial = {
            "status": "STARTING",
            "run_id": run_id,
            "strategy": strategy,
            "strategy_name": strategy_name,
            "exchange_filter": exchange,
            "symbol_filter": symbols,
            "timeframe_filter": timeframes,
            "selected_files": len(rows),
            "total_files": len(rows),
            "selected_size_mb": mb,
            "selected_size_gb": round(mb / 1024, 3),
            "exchanges": sorted(set(x.get("exchange", "") for x in rows)),
            "symbols_sample": sorted(set(x.get("symbol", "") for x in rows))[:50],
            "timeframes": sorted(set(x.get("timeframe", "") for x in rows)),
            "current_index": 0,
            "percent": 0,
            "log": str(log),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        state.write_text(json.dumps(initial, indent=2), encoding="utf-8")

        cmd = [
            PY, "-u", str(ROOT / "run_backtest.py"),
            "--strategy", strategy,
            "--exchange", exchange,
            "--symbols", symbols,
            "--timeframes", timeframes,
            "--starting-capital", v("starting_capital", "1000"),
            "--maker-fee", v("maker_fee", "0.001"),
            "--taker-fee", v("taker_fee", "0.001"),
            "--slippage", v("slippage", "0.0005"),
            "--max-hold-bars", v("max_hold_bars", "96"),
            "--default-tp", v("default_tp", "0.03"),
            "--default-sl", v("default_sl", "0.015"),
            "--max-bars", v("max_bars", "50000"),
            "--max-files", v("max_files", "999999"),
            "--strategy-window-bars", v("strategy_window_bars", "500"),
            "--signal-step", v("signal_step", "1"),
            "--run-state", str(state),
        ]

        with open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write("$ " + " ".join(cmd) + "\n\n")
            f.write("UI_SELECTED_FILES=" + str(len(rows)) + "\n")
            f.write("UI_SELECTED_SIZE_MB=" + str(mb) + "\n")
            f.write("UI_SELECTED_SIZE_GB=" + str(round(mb / 1024, 3)) + "\n\n")
            f.flush()
            subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)

        self.redirect("/status")

if __name__ == "__main__":
    print("UI running: http://127.0.0.1:8787", flush=True)
    Server(("127.0.0.1", 8787), App).serve_forever()
