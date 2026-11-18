"""A5 offline router, step 3: the fully observed sub-question, with no counterfactual estimate.
cascade vs stop-at-k: both outcomes are observed for every cascade unit (stop = 0 solves, GLM cost of first k
commands). Also: single-feature AUC for predicting the cascade's solve and the per-task solve pattern."""
import collections, json, os
import numpy as np
from r2_router_eval import units, FEATS, fit_lr, lr_fit, lr_pred, X

y = np.array([u["reward"] for u in units]); tasks = [u["task"] for u in units]
print("cascade solves per task:", {t: f"{int(sum(y[i] for i in range(len(y)) if tasks[i]==t))}/{tasks.count(t)}" for t in sorted(set(tasks))})


def auc(s, y):
    pos, neg = s[y == 1], s[y == 0]
    return float(np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg]))


for k in (3, 5, 10):
    Xm = X(units, k)
    a = sorted(((auc(Xm[:, j], y), f) for j, f in enumerate(FEATS)), key=lambda t: -abs(t[0] - 0.5))
    print(f"\nk={k} single-feature AUC for cascade solve (0.5 = none; in-sample, descriptive):",
          ", ".join(f"{f} {v:.2f}" for v, f in a[:6]))
    # task-mean of a feature vs task solve rate: does the feature separate tasks or runs?
    # LOTO stop rule: stop if feature on the bad side of a threshold fit on other tasks
    for lam in (0.25, 0.5, 1.0):
        best_cv = None
        choice = {}
        rules = []
        for t in sorted(set(tasks)):
            tr = [i for i in range(len(units)) if tasks[i] != t]
            best = (sum(y[i] - lam * units[i]["usd"] for i in tr), None, None, None)  # always cascade
            for j, f in enumerate(FEATS):
                v = np.unique(Xm[tr, j])
                for th in (v[:-1] + v[1:]) / 2:
                    for side in ("le", "gt"):
                        s = 0.0
                        for i in tr:
                            stop = Xm[i, j] <= th if side == "le" else Xm[i, j] > th
                            s += -lam * units[i]["cost_at_k"][str(k)] if stop else y[i] - lam * units[i]["usd"]
                        if s > best[0] + 1e-9:
                            best = (s, f, th, side)
            rules.append(best[1:])
            for i in range(len(units)):
                if tasks[i] == t:
                    _, f, th, side = best
                    if f is None:
                        choice[i] = "cascade"
                    else:
                        v = units[i]["feats"][str(k)][f]; choice[i] = "stop" if (v <= th if side == "le" else v > th) else "cascade"
        S = sum(y[i] for i in choice if choice[i] == "cascade")
        C = sum(units[i]["usd"] if choice[i] == "cascade" else units[i]["cost_at_k"][str(k)] for i in choice)
        lost = sum(1 for i in choice if choice[i] == "stop" and y[i] == 1)
        print(f"  lam={lam}: LOTO cascade-or-stop stump {S:.0f} solves ${C:.2f} (stopped {sum(1 for c in choice.values() if c=='stop')}, "
              f"of which solved by cascade {lost})  rules={collections.Counter((r[0], r[2]) for r in rules)}")
    # LOTO logistic: stop if P(cascade solve) < p*
    for pstar in (0.2, 0.3, 0.4):
        choice = {}
        for t in sorted(set(tasks)):
            tr = [i for i in range(len(units)) if tasks[i] != t]
            m = lr_fit(Xm[tr], y[tr], np.ones(len(tr)))
            for i in range(len(units)):
                if tasks[i] == t:
                    choice[i] = "stop" if lr_pred(m, Xm[i:i+1])[0] < pstar else "cascade"
        S = sum(y[i] for i in choice if choice[i] == "cascade")
        C = sum(units[i]["usd"] if choice[i] == "cascade" else units[i]["cost_at_k"][str(k)] for i in choice)
        print(f"  LOTO logreg stop if p<{pstar}: {S:.0f} solves ${C:.2f} (stopped {sum(1 for c in choice.values() if c=='stop')})")
