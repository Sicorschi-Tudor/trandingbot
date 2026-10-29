"""compare.py <parent.trades.jsonl> <filtered.trades.jsonl> : filtered avg_r vs same-size random subsample of parent"""
import sys, json, math, random, statistics as st
from collections import defaultdict
from datetime import datetime, timezone
random.seed(7)
P = [json.loads(l)["r"] for l in open(sys.argv[1]) if l.strip()]
F = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
R = [t["r"] for t in F]; n=len(R); N=len(P)
mu_f=sum(R)/n; mu_p=sum(P)/N; sd_p=st.pstdev(P); sd_f=st.pstdev(R)
se_rand = sd_p/math.sqrt(n)*math.sqrt(max(0,1-n/N))
boots=[]
for _ in range(4000):
    s=random.sample(P, n); boots.append(sum(s)/n)
boots.sort()
p_emp = sum(1 for b in boots if b>=mu_f)/len(boots)
print(f"parent N={N} avg={mu_p:+.4f} | filtered n={n} avg={mu_f:+.4f} CI90=[{mu_f-1.645*sd_f/math.sqrt(n):+.4f},{mu_f+1.645*sd_f/math.sqrt(n):+.4f}]")
print(f"random same-size subsample expectation: avg={mu_p:+.4f} SE={se_rand:.4f} 90% band=[{boots[int(0.05*len(boots))]:+.4f},{boots[int(0.95*len(boots))]:+.4f}]  z={(mu_f-mu_p)/se_rand:+.2f}  empirical P(random>=filtered)={p_emp:.4f}")
by=defaultdict(list)
for t in F: by[datetime.fromtimestamp(t["entry_t"],tz=timezone.utc).year].append(t["r"])
print("per-year:", {y:(len(v), round(sum(v)/len(v),4)) for y,v in sorted(by.items())})
srt=sorted(R, reverse=True); k=max(1,int(0.05*n)); top=sum(srt[:k]); gross=sum(x for x in R if x>0)
print(f"top5% ({k}) sum={top:+.1f} share of gross={top/gross:.1%} net={sum(R):+.1f} avg without top5%={(sum(R)-top)/(n-k):+.4f}")
oc=defaultdict(list)
for t in F: oc[t["outcome"]].append(t["r"])
print("outcomes:", {k:(len(v), round(sum(v),1)) for k,v in oc.items()})
