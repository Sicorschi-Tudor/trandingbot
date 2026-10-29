"""Attribution of trade R by entry hour / weekday / month / spread-SL ratio / ATR tercile / HTF trend strength.
usage: attrib.py <trades.jsonl> <sig_tf> <htf> [db] """
import sys, json, math, sqlite3, statistics as st
from collections import defaultdict
from datetime import datetime, timezone

path, tf, htf = sys.argv[1], sys.argv[2], sys.argv[3]
db = sys.argv[4] if len(sys.argv) > 4 else "/home/user/trandingbot/data/research.db"
POINT = 0.01
TFS = {"M15": 900, "H1": 3600, "H4": 14400}
trades = [json.loads(l) for l in open(path) if l.strip()]
N = len(trades); R = [t["r"] for t in trades]
mu = sum(R)/N; sd = st.pstdev(R)
print(f"FILE {path}\nN={N} avg_r={mu:+.4f} sd={sd:.3f} CI90=[{mu-1.645*sd/math.sqrt(N):+.4f},{mu+1.645*sd/math.sqrt(N):+.4f}] sum_r={sum(R):+.1f}")

con = sqlite3.connect(db)
def load(tfname):
    rows = con.execute("select t,o,h,l,c from bars_archive where symbol='XAUUSD' and tf=? order by t", (tfname,)).fetchall()
    return rows
def atr_series(rows, n=14):
    """Wilder-free simple ATR like app.indicators (mean of last n TR) -> dict t->atr (ATR of bar closing at t)"""
    out = {}
    trs = []
    prev_c = None
    for t,o,h,l,c in rows:
        tr = (h-l) if prev_c is None else max(h-l, abs(h-prev_c), abs(l-prev_c))
        trs.append(tr); prev_c = c
        if len(trs) >= n:
            out[t] = sum(trs[-n:])/n
    return out
def ema_series(rows, n=50):
    k = 2/(n+1); e=None; out={}
    for t,o,h,l,c in rows:
        e = c if e is None else (c-e)*k+e
        out[t]=e
    return out
import bisect
sig_rows = load(tf); htf_rows = load(htf)
sig_atr = atr_series(sig_rows); 
htf_atr = atr_series(htf_rows); htf_e50 = ema_series(htf_rows, 50)
sig_ts = [r[0] for r in sig_rows]; htf_ts = [r[0] for r in htf_rows]
def last_closed(ts_list, period, t):
    """index of last bar with t_bar + period <= t (closed before entry)"""
    i = bisect.bisect_right(ts_list, t - period) - 1
    return i
feat = []
for tr in trades:
    et = tr["entry_t"]
    d = datetime.fromtimestamp(et, tz=timezone.utc)
    sl_dist = abs(tr["entry"] - tr["sl"])
    spread_usd = tr.get("spread_pts", 0)*POINT
    i = last_closed(sig_ts, TFS[tf], et)
    a = sig_atr.get(sig_ts[i]) if i >= 0 else None
    j = last_closed(htf_ts, TFS[htf], et)
    hs = None
    if j >= 1:
        ha = htf_atr.get(htf_ts[j]); 
        if ha:
            slope = (htf_e50[htf_ts[j]] - htf_e50[htf_ts[j-1]])/ha
            hs = slope if tr["side"] == "BUY" else -slope  # aligned with trade direction
    feat.append({"r": tr["r"], "hour": d.hour, "wd": d.strftime("%a"), "mon": d.month, "year": d.year,
                 "spread_sl": spread_usd/sl_dist if sl_dist else None, "atr": a, "atr_pct": (a/tr["entry"]*100) if a else None,
                 "htf_slope": hs, "score": abs(tr.get("score", 0)), "outcome": tr["outcome"], "side": tr["side"],
                 "sl_floor": sl_dist <= 1.5*POINT*150*1.0001 and (a is None or 1.5*a < 150*POINT)})

def stats(rs):
    n=len(rs); 
    if n==0: return "n=0"
    m=sum(rs)/n; s=st.pstdev(rs) if n>1 else 0; se=s/math.sqrt(n)
    # vs random subsample of the same size (finite population correction)
    se_rand = sd/math.sqrt(n)*math.sqrt(max(0,1-n/N)) if n<N else 1e-9
    z = (m-mu)/se_rand if se_rand>0 else 0
    return f"n={n:5d} avg={m:+.4f} CI90=[{m-1.645*se:+.4f},{m+1.645*se:+.4f}] sum={sum(rs):+7.1f}  z_vs_random={z:+.2f}"
def group(key, order=None, label=""):
    g=defaultdict(list)
    for f in feat: g[key(f)].append(f["r"])
    keys = order if order else sorted(k for k in g if k is not None)
    print(f"\n== by {label} ==")
    for k in keys:
        if k in g: print(f"  {str(k):>12}: {stats(g[k])}")
    if None in g: print(f"  {'NA':>12}: {stats(g[None])}")
def tercile(vals_key):
    vals = sorted(f[vals_key] for f in feat if f[vals_key] is not None)
    q1, q2 = vals[len(vals)//3], vals[2*len(vals)//3]
    def k(f):
        v=f[vals_key]
        if v is None: return None
        return f"T1 low(<{q1:.3g})" if v<q1 else (f"T2 mid" if v<q2 else f"T3 high(>={q2:.3g})")
    return k, [f"T1 low(<{q1:.3g})","T2 mid",f"T3 high(>={q2:.3g})"]

group(lambda f:f["hour"], list(range(24)), "entry hour UTC")
group(lambda f:f["wd"], ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], "weekday")
group(lambda f:f["mon"], list(range(1,13)), "month of year")
group(lambda f:f["year"], None, "year")
def ssb(f):
    v=f["spread_sl"]
    if v is None: return None
    for lo,hi in [(0,0.05),(0.05,0.08),(0.08,0.10),(0.10,0.12),(0.12,0.15),(0.15,0.2),(0.2,9)]:
        if lo<=v<hi: return f"[{lo:.2f},{hi:.2f})"
group(ssb, ["[0.00,0.05)","[0.05,0.08)","[0.08,0.10)","[0.10,0.12)","[0.12,0.15)","[0.15,0.20)","[0.20,9.00)"], "spread/SL ratio")
k,o = tercile("atr_pct"); group(k,o,"ATR14/price % tercile (signal TF)")
k,o = tercile("atr"); group(k,o,"ATR14 USD tercile (signal TF)")
k,o = tercile("htf_slope"); group(k,o,"HTF EMA50 slope/ATR_htf aligned to trade (tercile)")
group(lambda f: "with trend" if f["htf_slope"] is not None and f["htf_slope"]>0.05 else ("flat" if f["htf_slope"] is not None and f["htf_slope"]>-0.05 else ("against" if f["htf_slope"] is not None else None)), ["with trend","flat","against"], "HTF trend vs trade dir (thr 0.05 ATR)")
group(lambda f:f["score"], None, "|score| (net conditions/5)")
group(lambda f:f["outcome"], None, "outcome")
group(lambda f:f["side"], None, "side")
print("\nSL floor (150 pts) binding share:", sum(1 for f in feat if f["sl_floor"])/N)
# session windows commonly considered
def window(f, a, b): return a <= f["hour"] < b
for a,b in [(7,17),(8,16),(12,17),(7,12),(13,17),(7,21),(1,20),(0,24)]:
    rs=[f["r"] for f in feat if window(f,a,b)]
    print(f"  window {a:02d}-{b:02d} UTC: {stats(rs)}")
# top 5% share
srt=sorted(R, reverse=True); k=max(1,int(0.05*N)); top=sum(srt[:k]); gross=sum(x for x in R if x>0)
print(f"\ntop5% ({k} trades) sum={top:+.1f}  share of gross profit={top/gross:.1%}  net={sum(R):+.1f}  avg_r without top5%={(sum(R)-top)/(N-k):+.4f}")
json.dump(feat, open(path.replace(".trades.jsonl", ".feat.json"), "w"))
