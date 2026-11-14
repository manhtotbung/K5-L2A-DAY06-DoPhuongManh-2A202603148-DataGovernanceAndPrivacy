"""T02 Raw QC Triage — PoC pipeline (group-owned).

Idea: the 96 images come from 24 source scenes (4 variants each, `img_SS_V`). A *scene-relative*
comparison removes what a global threshold cannot: night scenes are dark by nature, cam_B has softer
optics. Each defect gets a label-free score relative to the other images of the same scene:

    blur         scene median log(lap_var) - log(lap_var)
    dark         scene median log(brightness) - log(brightness)
    glare        local_glare (max saturated fraction over a 4x8 tile grid) - scene median
    compression  blockiness (8-px boundary gradient ratio) - scene median

Each score is divided by a fixed "defect unit" (DEFAULT_UNITS, chosen by looking at dev pixels only,
frozen before eval); final score = max over defects, pred_defect_type = argmax. No ground truth is read.

Steps: 01 load -> 02 process -> 03 rank (top-B=20) -> 04 review (overrides + audit) -> 05 decide.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common.contact_sheet import render_contact_sheet
from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track
from .t02_features import compute_features

DEFECTS = ["blur", "dark", "glare", "compression"]
VALID_TYPES = set(DEFECTS) | {"none"}
VALID_ACTIONS = {"KEEP", "RECAPTURE"}


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")
    images_csv = ctx.input_dir / "images.csv"
    if not images_csv.exists():
        raise FileNotFoundError(f"missing {images_csv}")
    images = pd.read_csv(images_csv)
    need = {"image_id", "path", "camera_id", "light", "split"}
    if need - set(images.columns):
        raise ValueError(f"images.csv missing columns {sorted(need - set(images.columns))}")
    return {"manifest": pd.read_csv(manifest) if manifest.exists() else None, "images": images}


# ---------------------------------------------------------------- 02 process
def scene_relative_scores(feats: pd.DataFrame) -> pd.DataFrame:
    """Raw per-defect scores, each >= 0, relative to the *median* image of the same source scene.

    Median (not max/min) as reference: one unusually sharp/clean variant in a scene (seen on cam_B in dev)
    must not make the normal images of that scene look defective.
    """
    s = feats["scene_id"]
    med = lambda x: x.groupby(s).transform("median")  # noqa: E731
    log_lap = np.log(feats["lap_var"] + 1e-6)
    log_b = np.log(feats["bright_mean"] + 1.0)
    raw = pd.DataFrame(index=feats.index)
    raw["blur"] = med(log_lap) - log_lap
    raw["dark"] = med(log_b) - log_b
    raw["glare"] = feats["local_glare"] - med(feats["local_glare"])
    raw["compression"] = feats["blockiness"] - med(feats["blockiness"])
    return raw.clip(lower=0.0)


# One "unit" of each defect = the deviation that we call a clear defect. Frozen on dev (pixels only, no labels):
#   blur 2.5        lap_var ~12x lower than the scene median
#   dark 1.0        brightness ~e (2.7x) lower than the scene median
#   glare 0.10      +10 % saturated pixels in one tile
#   compression 1.0 8-px boundary/inner gradient ratio +1.0
DEFAULT_UNITS = {"blur": 2.5, "dark": 1.0, "glare": 0.10, "compression": 1.0}


def defect_units(ctx: RunContext | None = None) -> dict:
    over = (ctx.param("defect_units", {}) if ctx else {}) or {}
    return {d: float(over.get(d, DEFAULT_UNITS[d])) for d in DEFECTS}


def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    images = data["images"]
    feats = compute_features(images, ctx.input_dir)
    raw = scene_relative_scores(feats)
    units = defect_units(ctx)
    norm = raw / pd.Series(units)
    ctx.stats["defect_units"] = units

    action_thr = float(ctx.param("recapture_threshold", 1.0))
    out = feats[["image_id", "path", "camera_id", "light", "split", "scene_id"]].copy()
    for d in DEFECTS:
        out[f"s_{d}"] = norm[d].round(4)
    out["score"] = norm.max(axis=1).round(6)
    out["pred_defect_type"] = np.where(out["score"] >= action_thr, norm.idxmax(axis=1), "none")
    out["pred_action"] = np.where(out["score"] >= action_thr, "RECAPTURE", "KEEP")
    out["reason"] = [f"{t} ({s:.2f})" for t, s in zip(norm.idxmax(axis=1), out["score"])]
    return out.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 20))
    q = make_review_queue(scored, id_col="image_id", score_col="score", budget=budget, reason_col="reason",
                          action_col="pred_action", slice_col="light", evidence_col="path")
    write_review_queue(q, ctx.out("review_queue.csv"))
    render_contact_sheet(q, ctx.out("contact_sheet.html"), image_root=ctx.input_dir,
                         title=f"T02 QC review queue (top-{budget})")
    return q


# ---------------------------------------------------------------- 04 review
def apply_review(scored: pd.DataFrame, reviewed: pd.DataFrame, audit=None) -> pd.DataFrame:
    """Reviewer semantics for T02.

    approve  -> keep pipeline prediction, mark reviewed
    reject   -> image is fine: pred_defect_type=none, pred_action=KEEP
    edit     -> override_value is a defect type (blur/dark/glare/compression/none) or an action (KEEP/RECAPTURE)
    escalate -> pred_action=RECAPTURE pending expert (kept conservative)
    skip     -> no change
    Ranking scores are never changed (the eval measures the model, the overrides drive the decision).
    """
    out = scored.copy()
    out["reviewed_by"] = ""
    out["review_decision"] = ""
    ids = set(out["image_id"])
    for r in reviewed.itertuples(index=False):
        if r.item_id not in ids:
            raise ValueError(f"review row for unknown image_id {r.item_id!r}")
        m = out["image_id"] == r.item_id
        before = out.loc[m, ["pred_defect_type", "pred_action"]].iloc[0].to_dict()
        dec, val = r.reviewer_decision, str(r.override_value).strip()
        if dec == "reject":
            out.loc[m, ["pred_defect_type", "pred_action"]] = ["none", "KEEP"]
        elif dec == "edit":
            if val.lower() in VALID_TYPES:
                t = val.lower()
                out.loc[m, ["pred_defect_type", "pred_action"]] = [t, "KEEP" if t == "none" else "RECAPTURE"]
            elif val.upper() in VALID_ACTIONS:
                out.loc[m, "pred_action"] = val.upper()
            else:
                raise ValueError(f"edit on {r.item_id}: override_value {val!r} must be one of "
                                 f"{sorted(VALID_TYPES)} or {sorted(VALID_ACTIONS)}")
        elif dec == "escalate":
            out.loc[m, "pred_action"] = "RECAPTURE"
        out.loc[m, ["reviewed_by", "review_decision"]] = [r.reviewer, dec]
        after = out.loc[m, ["pred_defect_type", "pred_action"]].iloc[0].to_dict()
        if audit:
            audit.log(actor=r.reviewer, action=f"review:{dec}", item_id=r.item_id, before=before, after=after,
                      reason=r.review_note)
    return out


def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        ctx.stats["n_reviewed"] = 0
        ctx.audit.log(actor="pipeline", action="review_pending", item_id=str(reviewed_path),
                      after={"queue_size": len(queue)})
        return scored.assign(reviewed_by="", review_decision="")
    reviewed = load_review_decisions(reviewed_path)
    ctx.stats["n_reviewed"] = len(reviewed)
    return apply_review(scored, reviewed, ctx.audit)


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    """Batch-level policy (frozen before eval):
    - queue not (fully) reviewed                        -> HOLD   REVIEW_PENDING
    - RECAPTURE share > max_recapture_rate (0.30)        -> REWORK RECAPTURE_RATE_HIGH
    - otherwise                                          -> GO     QC_PASSED (RECAPTURE items listed, excluded)
    """
    owner = ctx.config.get("owner", "")
    budget = int(ctx.param("budget", 20))
    n_rev = int(ctx.stats.get("n_reviewed", 0))
    n_rec = int((final["pred_action"] == "RECAPTURE").sum())
    rate = n_rec / max(len(final), 1)
    metrics = {"n_items": len(final), "n_recapture": n_rec, "recapture_rate": round(rate, 4), "n_reviewed": n_rev}
    evidence = [str(ctx.out("ranked_review.csv")), str(ctx.out("review_queue.csv"))]
    if n_rev < min(budget, len(final)):
        return Decision("HOLD", "REVIEW_PENDING", f"Only {n_rev}/{budget} queue items reviewed; human review required",
                        owner, ctx.track, evidence, metrics)
    if rate > float(ctx.param("max_recapture_rate", 0.30)):
        return Decision("REWORK", "RECAPTURE_RATE_HIGH", f"{n_rec}/{len(final)} images need recapture ({rate:.0%})",
                        owner, ctx.track, evidence, metrics)
    return Decision("GO", "QC_PASSED", f"Release KEEP images; {n_rec} RECAPTURE images excluded",
                    owner, ctx.track, evidence, metrics)


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    if spec.track != "t02":
        raise NotImplementedError(f"this group pipeline implements t02 only, not {spec.track} ({spec.title})")
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    final[spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
