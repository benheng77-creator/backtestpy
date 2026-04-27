from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path
import subprocess, sys, html, json, csv, time, traceback

ROOT = Path(r"C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS")
STRATS = ROOT / "_strategies"
REGISTRY = ROOT / "_registry" / "data_index.json"
RUNS = ROOT / "_ui_runs"
REPORTS = ROOT / "_reports"
PY = sys.executable
RUNS.mkdir(exist_ok=True)

class Server(ThreadingHTTPServer):
    allow_reuse_address = True

def jload(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def registry():
    return jload(REGISTRY, [])

def strategies():
    out = []
    for p in sorted(STRATS.glob("*.py"), key=lambda x: x.name.lower()):
        n = p.name.lower()
        if n.startswith("_") or n == "__init__.py" or n.startswith("common__"):
            continue
        if n in {"contract.py", "indicators.py", "panel.py"}:
            continue
        out.append(p)
    return out

def uniq(rows, key):
    return sorted(set(str(x.get(key, "")).strip() for x in rows if str(x.get(key, "")).strip()))

def select_rows(rows, ex, sym, tfs, max_files):
    tfset = [x.strip().lower() for x in str(tfs).split(",") if x.strip()]
    out = []
    for x in rows:
        if ex != "all" and str(x.get("exchange","")).lower() != ex.lower():
            continue
        if sym != "all" and str(x.get("symbol","")).upper() != sym.upper():
            continue
        if "all" not in tfset and str(x.get("timeframe","")).lower() not in tfset:
            continue
        if str(x.get("timeframe","")).lower() == "funding":
            continue
        out.append(x)
    if int(max_files) > 0:
        out = out[:int(max_files)]
    return out

def latest_state():
    files = sorted(RUNS.glob("run_*.state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
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
        return repr(e)

def leaderboard(strategy):
    if not strategy:
        return "<p>No leaderboard yet.</p>"
    p = REPORTS / f"{strategy}_leaderboard.csv"
    if not p.exists():
        return "<p>No leaderboard yet.</p>"
    rows = []
    try:
        with open(p, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            for i, r in enumerate(csv.DictReader(f)):
                if i >= 50:
                    break
                rows.append(r)
    except Exception as e:
        return "<pre>" + html.escape(repr(e)) + "</pre>"
    if not rows:
        return "<p>Empty leaderboard.</p>"
    cols = ["exchange","symbol","timeframe","trades","win_rate","net_pnl_pct","profit_factor","max_drawdown_pct","verdict"]
    s = "<table><tr>" + "".join("<th>"+html.escape(c)+"</th>" for c in cols) + "</tr>"
    for r in rows:
        s += "<tr>" + "".join("<td>"+html.escape(str(r.get(c,"")))+"</td>" for c in cols) + "</tr>"
    return s + "</table>"

def page(title, body, refresh=False):
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">{meta}
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#070b18;color:#eef2ff;font-family:Arial,sans-serif}}
.wrap{{max-width:1500px;margin:20px auto;padding:0 18px}}
.card{{background:#111a33;border:1px solid #263254;border-radius:18px;padding:22px}}
.grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
label{{display:block;font-weight:900;margin:14px 0 6px}}
select,input{{width:100%;box-sizing:border-box;background:#070b18;color:white;border:1px solid #33405f;border-radius:10px;padding:12px}}
button,a.btn{{display:inline-block;text-decoration:none;margin-top:18px;margin-right:8px;padding:14px 18px;border:0;border-radius:12px;background:#7183ff;color:white;font-weight:900}}
.panel{{background:#080d1d;border:1px solid #25304f;border-radius:14px;padding:14px;margin-top:14px}}
.badge{{display:inline-block;border-radius:999px;padding:7px 12px;margin:4px;background:#24345e;color:#dbe4ff;font-weight:900}}
.run{{background:#164d2c;color:#9dffbf}} .bad{{background:#5a1e28;color:#ffb0b0}}
.progress{{height:22px;background:#071020;border:1px solid #33405f;border-radius:999px;overflow:hidden;margin:10px 0}}
.bar{{height:100%;background:#7183ff}}
pre{{background:#050814;border:1px solid #202846;border-radius:12px;padding:14px;overflow:auto;max-height:520px;white-space:pre-wrap}}
table{{width:100%;border-collapse:collapse;margin-top:16px;font-size:13px}}
th,td{{border-bottom:1px solid #263254;padding:8px;text-align:left}}
th{{color:#9fb0ff}}
</style></head><body><div class="wrap"><div class="card">{body}</div></div></body></html>"""

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

    def do_GET(self):
        try:
            self._get()
        except Exception:
            self.send_html("UI ERROR", "<h1>UI ERROR</h1><pre>"+html.escape(traceback.format_exc())+"</pre>", False, 500)

    def do_POST(self):
        try:
            self._post()
        except Exception:
            self.send_html("RUN ERROR", "<h1>RUN ERROR</h1><pre>"+html.escape(traceback.format_exc())+"</pre><a class='btn' href='/'>Back</a>", False, 500)

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
            sp = latest_state()
            st = jload(sp, {}) if sp else {}
            if not st:
                self.send_html("Status", "<h1>Status</h1><div class='panel'>No run started yet.</div><a class='btn' href='/'>Back</a>", True)
                return

            status = str(st.get("status","UNKNOWN"))
            live = status in {"STARTING","LOADING_STRATEGY","RUNNING"}
            pct = float(st.get("percent", 0) or 0)
            cls = "run" if status == "FINISHED" or live else "bad"

            body = f"""
<h1>Live Backtest Status</h1>
<a class="btn" href="/">Back</a>
<a class="btn" href="/status">Refresh</a>

<div class="panel">
<span class="badge {cls}">{html.escape(status)}</span>
<div class="progress"><div class="bar" style="width:{pct}%"></div></div>
<h2>{html.escape(str(st.get("current_index",0)))} / {html.escape(str(st.get("total_files",0)))} files — {html.escape(str(pct))}%</h2>
<p><b>Current:</b> {html.escape(str(st.get("current_exchange","")))} / {html.escape(str(st.get("current_symbol","")))} / {html.escape(str(st.get("current_timeframe","")))}</p>
<p><b>Current file:</b> {html.escape(str(st.get("current_file","")))}</p>
</div>

<div class="grid">
<div class="panel"><b>Strategy</b><br>{html.escape(str(st.get("strategy_name","")))}</div>
<div class="panel"><b>Selected files</b><br>{html.escape(str(st.get("selected_files","")))}</div>
<div class="panel"><b>Selected size</b><br>{html.escape(str(st.get("selected_size_mb","")))} MB / {html.escape(str(st.get("selected_size_gb","")))} GB</div>
</div>

<div class="grid">
<div class="panel"><b>Exchange filter</b><br>{html.escape(str(st.get("exchange_filter","")))}</div>
<div class="panel"><b>Symbol filter</b><br>{html.escape(str(st.get("symbol_filter","")))}</div>
<div class="panel"><b>Timeframes</b><br>{html.escape(str(st.get("timeframe_filter","")))}</div>
</div>

<div class="panel"><b>Actual exchanges:</b><br>{html.escape(", ".join(st.get("exchanges",[])))}</div>
<div class="panel"><b>Symbol sample:</b><br>{html.escape(", ".join(st.get("symbols_sample",[])))}</div>
"""
            if st.get("error"):
                body += "<div class='panel bad'><b>Error</b><pre>"+html.escape(str(st.get("error","")))+"</pre></div>"

            body += "<h2>Live Log</h2><pre>"+html.escape(tail(st.get("log")))+"</pre>"
            body += "<h2>Leaderboard</h2>" + leaderboard(str(st.get("strategy_name","")))
            self.send_html("Status", body, live)
            return

        rows = registry()
        sfiles = strategies()
        active = [x for x in rows if str(x.get("timeframe","")).lower() != "funding"]
        total_mb = round(sum(float(x.get("size_mb",0)) for x in active), 2)
        total_gb = round(total_mb / 1024, 3)

        strat_opts = "".join(f'<option value="{html.escape(str(p))}">{html.escape(p.stem)}</option>' for p in sfiles)
        ex_opts = '<option value="all">all</option>' + "".join(f'<option value="{html.escape(x)}">{html.escape(x)}</option>' for x in uniq(rows,"exchange"))
        sym_opts = '<option value="all">all</option>' + "".join(f'<option value="{html.escape(x)}">{html.escape(x)}</option>' for x in uniq(rows,"symbol"))

        body = f"""
<h1>Backtest Cockpit</h1>

<div class="panel">
<span class="badge">Active files: {len(active)}</span>
<span class="badge">Active size: {total_mb} MB / {total_gb} GB</span>
<span class="badge">Real strategies: {len(sfiles)}</span>
</div>

<form method="post" action="/run">
<div class="grid">
<div><label>Python Strategy File</label><select name="strategy" required>{strat_opts}</select></div>
<div><label>Exchange</label><select name="exchange">{ex_opts}</select></div>
<div><label>Symbol</label><select name="symbols">{sym_opts}</select></div>
</div>

<div class="grid">
<div><label>Timeframes</label><select name="timeframes">
<option value="1m,5m,15m,30m,1h,4h,1d" selected>All core</option>
<option value="1m,5m,15m">Scalp</option>
<option value="5m,15m,1h">Intraday</option>
<option value="1h,4h,1d">Swing</option>
<option value="1m">1m only</option>
<option value="5m">5m only</option>
<option value="15m">15m only</option>
<option value="1h">1h only</option>
<option value="4h">4h only</option>
<option value="1d">1d only</option>
</select></div>
<div><label>Max Files</label><select name="max_files"><option value="25">25 quick</option><option value="100">100 medium</option><option value="999999" selected>All selected</option></select></div>
<div><label>Max Bars</label><select name="max_bars"><option value="10000">10k fast</option><option value="30000">30k balanced</option><option value="50000" selected>50k deep</option><option value="0">All bars slow</option></select></div>
</div>

<div class="grid">
<div><label>Capital</label><select name="starting_capital"><option value="1000" selected>1000</option><option value="500">500</option><option value="5000">5000</option></select></div>
<div><label>Maker Fee</label><select name="maker_fee"><option value="0.001" selected>0.1%</option><option value="0">0</option><option value="0.0005">0.05%</option></select></div>
<div><label>Taker Fee</label><select name="taker_fee"><option value="0.001" selected>0.1%</option><option value="0">0</option><option value="0.0005">0.05%</option></select></div>
</div>

<div class="grid">
<div><label>Slippage</label><select name="slippage"><option value="0.0005" selected>0.05%</option><option value="0">0</option><option value="0.001">0.1%</option></select></div>
<div><label>Max Hold Bars</label><select name="max_hold_bars"><option value="96" selected>96</option><option value="48">48</option><option value="168">168</option></select></div>
<div><label>Signal Step</label><select name="signal_step"><option value="1" selected>Every candle</option><option value="5">Every 5 candles</option><option value="10">Every 10 candles</option></select></div>
</div>

<input type="hidden" name="default_tp" value="0.03">
<input type="hidden" name="default_sl" value="0.015">
<input type="hidden" name="strategy_window_bars" value="500">

<button type="submit">RUN SELECTED PY STRATEGY</button>
<a class="btn" href="/status">Live Status</a>
</form>
"""
        self.send_html("Backtest Cockpit", body)

    def _post(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length",0))).decode("utf-8", errors="replace")
        data = parse_qs(raw)
        def v(k,d): return data.get(k,[d])[0]

        strategy = v("strategy","")
        ex = v("exchange","all")
        sym = v("symbols","all")
        tfs = v("timeframes","1m,5m,15m,30m,1h,4h,1d")
        max_files = int(v("max_files","999999"))

        rows = select_rows(registry(), ex, sym, tfs, max_files)
        mb = round(sum(float(x.get("size_mb",0)) for x in rows), 2)
        name = Path(strategy).stem

        run_id = "run_" + time.strftime("%Y%m%d_%H%M%S") + "__" + name
        log = RUNS / (run_id + ".log")
        state = RUNS / (run_id + ".state.json")

        state.write_text(json.dumps({
            "status":"STARTING",
            "run_id":run_id,
            "strategy":strategy,
            "strategy_name":name,
            "exchange_filter":ex,
            "symbol_filter":sym,
            "timeframe_filter":tfs,
            "selected_files":len(rows),
            "total_files":len(rows),
            "selected_size_mb":mb,
            "selected_size_gb":round(mb/1024,3),
            "exchanges":sorted(set(x.get("exchange","") for x in rows)),
            "symbols_sample":sorted(set(x.get("symbol","") for x in rows))[:50],
            "timeframes":sorted(set(x.get("timeframe","") for x in rows)),
            "current_index":0,
            "percent":0,
            "log":str(log),
            "updated_at":time.strftime("%Y-%m-%d %H:%M:%S")
        }, indent=2), encoding="utf-8")

        cmd = [
            PY, "-u", str(ROOT / "run_backtest.py"),
            "--strategy", strategy,
            "--exchange", ex,
            "--symbols", sym,
            "--timeframes", tfs,
            "--starting-capital", v("starting_capital","1000"),
            "--maker-fee", v("maker_fee","0.001"),
            "--taker-fee", v("taker_fee","0.001"),
            "--slippage", v("slippage","0.0005"),
            "--max-hold-bars", v("max_hold_bars","96"),
            "--default-tp", v("default_tp","0.03"),
            "--default-sl", v("default_sl","0.015"),
            "--max-bars", v("max_bars","50000"),
            "--max-files", v("max_files","999999"),
            "--strategy-window-bars", v("strategy_window_bars","500"),
            "--signal-step", v("signal_step","1"),
            "--run-state", str(state),
        ]

        f = open(log, "w", encoding="utf-8", newline="\n")
        f.write("$ " + " ".join(cmd) + "\n\n")
        f.write("UI_SELECTED_FILES=" + str(len(rows)) + "\n")
        f.write("UI_SELECTED_SIZE_MB=" + str(mb) + "\n")
        f.write("UI_SELECTED_SIZE_GB=" + str(round(mb/1024,3)) + "\n\n")
        f.flush()
        subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
        f.close()

        self.redirect("/status")

if __name__ == "__main__":
    print("========================================", flush=True)
    print("BACKTEST COCKPIT SERVER RUNNING", flush=True)
    print("OPEN: http://127.0.0.1:8787/", flush=True)
    print("KEEP THIS BLACK WINDOW OPEN", flush=True)
    print("========================================", flush=True)
    Server(("127.0.0.1", 8787), App).serve_forever()
