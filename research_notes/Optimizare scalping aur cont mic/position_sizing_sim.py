import random, math, statistics as st
random.seed(7)
N=5000
def med(x): return st.median(x)
print("=== A. Expectancy / Kelly, win=+1 unit, loss=-2.5 units (b=0.4) ===")
for p in [0.55,0.60,0.65,0.70,2.5/3.5,0.72,0.75,0.80]:
    q=1-p; E=p-2.5*q; f=p-q/0.4
    print(f"p={p:.4f} E(units of win)={E:+.3f}  E(R, loss=1R)={E/2.5:+.3f}R  Kelly f*=p-2.5q={f:+.4f} half={f/2:+.4f}")
print("break-even p = 2.5/3.5 =", round(2.5/3.5,4), "; loss 4 vs win 2 -> 4/6 =", round(4/6,4))

def mc_frac(p,r,T):
    dd30=dd50=ruin=lose=0; finals=[]
    for _ in range(N):
        eq=1.0; peak=1.0; mdd=0.0; mn=1.0
        for t in range(T):
            eq *= (1+0.4*r) if random.random()<p else (1-r)
            if eq>peak: peak=eq
            d=1-eq/peak
            if d>mdd: mdd=d
            if eq<mn: mn=eq
        dd30+=mdd>=.3; dd50+=mdd>=.5; ruin+=mn<=.2; lose+=eq<1; finals.append(eq)
    return dd30/N,dd50/N,ruin/N,lose/N,med(finals)
print("\n=== B. Fixed-fractional MC, 200 trades, risk r of equity per trade (loss=r, win=0.4r), N=5000 ===")
for p in [0.55,0.60,0.65,0.70,0.75,0.80]:
    for r in [0.02,0.05,0.10,0.17]:
        a=mc_frac(p,r,200)
        print(f"p={p:.2f} r={r:.2f}: P(DD>=30%)={a[0]:.3f} P(DD>=50%)={a[1]:.3f} P(eq<=20%)={a[2]:.3f} P(final<start)={a[3]:.3f} medianFinal={a[4]:.2f}")
print("--- 500 trades ---")
for p in [0.60,0.70,0.75,0.80]:
    for r in [0.02,0.05,0.10]:
        a=mc_frac(p,r,500)
        print(f"p={p:.2f} r={r:.2f}: P(DD>=30%)={a[0]:.3f} P(DD>=50%)={a[1]:.3f} P(eq<=20%)={a[2]:.3f} medianFinal={a[4]:.2f}")

print("\n=== C. Closed form (Thorp eq. 7.12/7.13 diffusion): P(ever fall to x*V0)=x^a, a=2M/V ===")
for p in [0.72,0.75,0.80]:
    q=1-p
    for r in [0.02,0.05,0.10,0.17]:
        M=p*math.log(1+0.4*r)+q*math.log(1-r); V=p*q*(math.log(1+0.4*r)-math.log(1-r))**2
        if M<=0: print(f"p={p} r={r}: M={M:+.5f} <=0 -> ruin certain (x^a->1)"); continue
        a=2*M/V
        print(f"p={p} r={r}: M={M:+.5f} V={V:.5f} a={a:.3f} P(ever -30%)={0.7**a:.3f} P(ever -50%)={0.5**a:.3f} P(ever -80%)={0.2**a:.3f}")

def mc_fixed(p,A,lot,win,loss,T,margin_per=20):
    margin=margin_per*lot; ruin=dd50=dd30=0; finals=[]
    for _ in range(N):
        eq=float(A); peak=eq; mdd=0; dead=False
        for t in range(T):
            if not dead:
                eq += win*lot if random.random()<p else -loss*lot
                if eq<=margin: dead=True
                if eq>peak: peak=eq
                d=1-eq/peak
                if d>mdd: mdd=d
        ruin+=dead; dd50+=mdd>=.5; dd30+=mdd>=.3; finals.append(eq)
    return ruin/N,dd50/N,dd30/N,med(finals)
print("\n=== D. Fixed lot MC (real case), 200 trades. per 0.01 lot: win +2 USD, loss -4 or -5 USD. ruin = equity <= margin (20 USD per 0.01 @ 4000, 1:200) ===")
for A,lot in [(100,1),(105,2),(130,3),(130,4)]:
    for loss in [4,5]:
        for p in [0.60,0.65,0.70,0.75,0.80]:
            a=mc_fixed(p,A,lot,2,loss,200)
            print(f"A={A} lot=0.0{lot} loss={loss} p={p:.2f} risk/trade={loss*lot/A*100:.1f}%: P(ruin)={a[0]:.3f} P(DD>=50%)={a[1]:.3f} P(DD>=30%)={a[2]:.3f} medianFinal={a[3]:.0f}")
print("--- 500 trades ---")
for A,lot in [(100,1),(105,2)]:
    for loss in [4,5]:
        for p in [0.70,0.75,0.80]:
            a=mc_fixed(p,A,lot,2,loss,500)
            print(f"A={A} lot=0.0{lot} loss={loss} p={p}: P(ruin)={a[0]:.3f} P(DD>=50%)={a[1]:.3f} medianFinal={a[3]:.0f}")

print("\n=== E. Infinite-horizon gambler's ruin, fixed stake, steps +2/-5 USD per 0.01 lot: root r in (0,1) of p r^7 - r^5 + q = 0; P(ruin from n USD) ~ r^n ===")
def root(p):
    q=1-p; f=lambda r: p*r**7 - r**5 + q
    lo,hi=1e-9,1-1e-9
    # f(0)=q>0, f(1)=0; find interior root where f crosses zero: scan
    xs=[i/10000 for i in range(1,10000)]
    prev=f(xs[0]); 
    for x in xs[1:]:
        cur=f(x)
        if prev>0 and cur<=0:
            a,b=x-1e-4,x
            for _ in range(60):
                m=(a+b)/2
                if f(a)*f(m)<=0: b=m
                else: a=m
            return (a+b)/2
        prev=cur
    return None
for p in [0.72,0.75,0.80,0.85,0.90]:
    r=root(p)
    if r is None: print(f"p={p}: no interior root -> ruin certain"); continue
    print(f"p={p}: r={r:.4f}  P(ruin) 100 USD@0.01 (50 units of 2)~{r**50:.3f}  | 105 USD@0.02 (26 units)~{r**26:.3f} | 130 USD@0.04 (16 units)~{r**16:.3f} | 1000 USD@0.01 (500 units)~{r**500:.2e}")
print("(unit = 2 USD per 0.01 lot so win=+1 unit, loss=-2.5 units -> scaled x2 to integers +2/-5; n = equity/(2 USD*lot/0.01))")

print("\n=== F. Fixed costs ===")
for C in [5,8]:
    for k in [5,10,15]:
        print(f"C={C} USD/day, {k} trades/day -> {C/k:.2f} USD/trade; 22 days = {C*22} USD = {C*22}% of a 100 USD account per month")
print("Required p for zero net after per-trade cost c: p=(L+c)/(W+L)")
for lot in [1,2,4]:
    for W,L in [(2,4),(2,5)]:
        for c in [0.5,0.8,1.6]:
            print(f"lot=0.0{lot} W={W*lot} L={L*lot} c={c}: p_req={(L*lot+c)/((W+L)*lot):.3f}")

print("\n=== G. Margin at 1:200, gold 4000 USD, contract 100 oz ===")
for lot in [0.01,0.02,0.03,0.04]:
    n=lot*100*4000
    print(f"lot={lot}: notional={n:.0f} margin@1:200={n/200:.0f} @1:100(news HMR)={n/100:.0f} @1:500={n/500:.0f}; free margin on 100 USD={100-n/200:.0f}; adverse move to equity 0 on 100 USD = {100/(lot*100):.1f} USD; margin-call(60% of margin) hit after loss of {100-0.6*n/200:.0f} USD")
