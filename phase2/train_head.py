"""Train the routing head on frozen Laya and pick the cascade threshold.

laya_mlx exposes answers, not embeddings, so the features are Laya's answer probabilities on a
fixed set of questions about the task (Laya itself is never updated). A multinomial logistic
regression maps them to the Phase 1 label (cheapest tier that solved the task).
Evaluation is k-fold by task, so no task is ever in its own training fold.

  python -m phase2.train_head --tasks tasks_text.json --labels labels.json --out head.json

tasks_text.json: {task_id: instruction text}; labels.json from `python -m phase2.replay --labels-out`.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from .router import TIER_QUESTION

FEATURE_QUESTIONS = {
    **TIER_QUESTION,
    "scope": {"type": "score", "instructions": "How much of the codebase must change to finish `task`?",
              "criteria": ["one line or one value", "one function or one file", "several files or modules",
                           "a whole subsystem"]},
    "uncertainty": {"type": "score", "instructions": "How clear is what `task` asks for?",
                    "criteria": ["fully specified", "mostly clear", "ambiguous, needs investigation"]},
    "debugging": {"type": "noul", "instructions": "Does `task` require finding the cause of a failure?"},
}


def laya_features(texts: list[str]) -> np.ndarray:
    import laya_mlx

    agent = laya_mlx.load()
    rows = []
    for t in texts:
        ans = agent.predict({"task": t}, FEATURE_QUESTIONS)["answers"]
        row = []
        for qid, q in FEATURE_QUESTIONS.items():
            a = ans[qid]
            if q["type"] == "noul":
                row.append(float(a["noul"]))
            else:
                probs = a["probabilities"]  # {"0": p0, "1": p1, ...}
                row += [float(probs[str(i)]) for i in range(len(q["criteria"]))] + [float(a["confidence"])]
        rows.append(row)
    return np.array(rows)


def fit_softmax(X, y, k, l2=1e-2, steps=2000, lr=0.5):
    """Plain multinomial logistic regression (numpy, full-batch gradient descent)."""
    mu, sd = X.mean(0), X.std(0) + 1e-8
    Z = np.c_[(X - mu) / sd, np.ones(len(X))]
    W = np.zeros((Z.shape[1], k))
    Y = np.eye(k)[y]
    for _ in range(steps):
        P = np.exp(Z @ W - (Z @ W).max(1, keepdims=True))
        P /= P.sum(1, keepdims=True)
        W -= lr * (Z.T @ (P - Y) / len(Z) + l2 * W)
    return {"W": W.tolist(), "mu": mu.tolist(), "sd": sd.tolist()}


def predict_proba(model, X):
    W, mu, sd = (np.array(model[k]) for k in ("W", "mu", "sd"))
    Z = np.c_[(X - mu) / sd, np.ones(len(X))]
    P = np.exp(Z @ W - (Z @ W).max(1, keepdims=True))
    return P / P.sum(1, keepdims=True)


def kfold_eval(X, y, k_classes, folds=6, seed=570):
    """Out-of-fold predictions; folds split by task (one row per task)."""
    idx = np.random.default_rng(seed).permutation(len(y))
    oof = np.zeros((len(y), k_classes))
    for f in range(folds):
        test = idx[f::folds]
        train = np.setdiff1d(idx, test)
        if len(np.unique(y[train])) < 2:
            oof[test] = np.bincount(y[train], minlength=k_classes) / len(train)
            continue
        oof[test] = predict_proba(fit_softmax(X[train], y[train], k_classes), X[test])
    pred = oof.argmax(1)
    return {"accuracy": float((pred == y).mean()), "within_one": float((abs(pred - y) <= 1).mean()),
            "oof_proba": oof.tolist()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", default="head.json")
    ap.add_argument("--folds", type=int, default=6)
    args = ap.parse_args(argv)
    texts = json.loads(Path(args.tasks).read_text())
    lab = json.loads(Path(args.labels).read_text())
    ids = sorted(set(texts) & set(lab))
    X, y = laya_features([texts[i] for i in ids]), np.array([lab[i] for i in ids])
    k = int(y.max()) + 1
    cv = kfold_eval(X, y, k, args.folds)
    model = fit_softmax(X, y, k)
    Path(args.out).write_text(json.dumps({"tasks": ids, "cv": {kk: v for kk, v in cv.items() if kk != "oof_proba"},
                                          "model": model, "questions": list(FEATURE_QUESTIONS)}, indent=2))
    print(json.dumps({"n": len(ids), "accuracy": cv["accuracy"], "within_one": cv["within_one"]}))


if __name__ == "__main__":
    main()
