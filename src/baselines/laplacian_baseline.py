"""T02 baseline (required by docs/tracks/T02.md §4): ONE global Laplacian-variance threshold.

    python -m src.main --track t02 --pipeline src.baselines.laplacian_baseline \
        --output out/baseline --run-name baseline --offline --seed 42

score = -log(lap_var)  (blurrier = more suspicious). The RECAPTURE threshold is the
`baseline_quantile` quantile of score on the `dev` split only (frozen before eval).
Same pool, budget B and seed as the PoC.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.contact_sheet import render_contact_sheet
from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.review import make_review_queue, write_review_queue
from ..t02_features import compute_features


def run(ctx: RunContext) -> None:
    images = pd.read_csv(ctx.input_dir / "images.csv")
    feats = compute_features(images, ctx.input_dir)
    feats["score"] = -np.log(feats["lap_var"] + 1e-6)

    dev = feats[feats["split"] == ctx.param("dev_split", "dev")]
    thr = float(dev["score"].quantile(float(ctx.param("baseline_quantile", 0.75))))
    feats["pred_defect_type"] = np.where(feats["score"] >= thr, "blur", "none")
    feats["pred_action"] = np.where(feats["score"] >= thr, "RECAPTURE", "KEEP")
    feats["reason"] = "global_laplacian"
    ctx.stats.update(n_items=len(feats), threshold=thr)

    budget = int(ctx.param("budget", 20))
    q = make_review_queue(feats, "image_id", "score", budget, reason_col="reason", action_col="pred_action",
                          slice_col="light", evidence_col="path")
    write_review_queue(q, ctx.out("review_queue.csv"))
    render_contact_sheet(q, ctx.out("contact_sheet.html"), image_root=ctx.input_dir, title="T02 baseline (global Laplacian)")

    out = feats.sort_values("score", ascending=False, kind="mergesort")
    out[["image_id", "score", "pred_defect_type", "pred_action"]].to_csv(ctx.out("ranked_review.csv"), index=False)
    write_decision(Decision(status="HOLD", reason_code="BASELINE_ONLY", reason="Baseline run: not a release decision",
                            owner=ctx.config.get("owner", "baseline"), scope=ctx.track,
                            evidence=[str(ctx.out("ranked_review.csv"))], metrics={"threshold": thr}),
                   ctx.out("decision.json"), ctx.out("decision_log.csv"))
