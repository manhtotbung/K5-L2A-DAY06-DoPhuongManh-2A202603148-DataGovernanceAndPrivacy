"""Group tests for the T02 PoC: features, scene-relative scoring, review overrides (success + failure)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.ndimage import gaussian_filter

from src.pipeline import DEFAULT_UNITS, apply_review, defect_units, scene_relative_scores
from src.t02_features import blockiness, local_glare


def _texture(seed=0, h=64, w=96):
    return np.random.default_rng(seed).uniform(40, 200, size=(h, w)).astype(np.float32)


def test_local_glare_catches_small_saturated_region():
    g = _texture()
    assert local_glare(g) == 0.0
    g[:16, :12] = 255.0          # one tile fully burnt (e.g. plate glare), <5% of the image
    assert (g > 245).mean() < 0.05
    assert local_glare(g) == pytest.approx(1.0)


def test_blockiness_higher_for_8px_blocks():
    g = _texture()
    blocky = g.reshape(8, 8, 12, 8).mean(axis=(1, 3)).repeat(8, 0).repeat(8, 1)
    assert blockiness(blocky) > 2.0 > blockiness(g)


def test_scene_relative_blur_ignores_soft_camera():
    """A uniformly soft scene (cam_B) must not look blurry; only the blurred variant in a scene should."""
    rows = []
    for scene, base_lap in [("img_00", 1000.0), ("img_01", 100.0)]:   # img_01 = soft optics, all variants
        for v in range(4):
            lap = base_lap / (20 if (scene == "img_00" and v == 3) else 1)
            rows.append(dict(image_id=f"{scene}_{v}", scene_id=scene, lap_var=lap, bright_mean=120.0,
                             local_glare=0.0, blockiness=1.0))
    raw = scene_relative_scores(pd.DataFrame(rows))
    assert raw["blur"].idxmax() == 3
    assert raw.loc[4:7, "blur"].max() == pytest.approx(0.0)   # soft-but-consistent scene: no blur alarm


def test_one_sharp_outlier_does_not_flag_rest_of_scene():
    """cam_B failure mode: one unusually sharp variant must not make the normal variants look blurry."""
    rows = [dict(image_id=f"img_09_{v}", scene_id="img_09", lap_var=lap, bright_mean=115.0, local_glare=0.0,
                 blockiness=1.2) for v, lap in enumerate([187.0, 876.0, 1.7, 196.0])]
    norm = scene_relative_scores(pd.DataFrame(rows))["blur"] / DEFAULT_UNITS["blur"]
    assert norm[2] >= 1.0                       # the truly blurred variant
    assert norm[[0, 1, 3]].max() < 1.0          # normal + sharp variants stay below one defect unit


def test_defect_units_default_without_ctx():
    assert defect_units() == DEFAULT_UNITS


def _scored():
    return pd.DataFrame({"image_id": ["a", "b", "c"], "score": [3.0, 2.0, 0.1],
                         "pred_defect_type": ["blur", "glare", "none"],
                         "pred_action": ["RECAPTURE", "RECAPTURE", "KEEP"]})


def _rev(rows):
    return pd.DataFrame(rows, columns=["item_id", "reviewer", "reviewer_decision", "override_value", "review_note"])


def test_apply_review_overrides_and_keeps_scores():
    out = apply_review(_scored(), _rev([["a", "QA", "reject", "", "false alarm"],
                                        ["b", "QA", "edit", "dark", "night, not glare"],
                                        ["c", "QA", "escalate", "", "check"]]))
    assert out.set_index("image_id").loc["a", ["pred_defect_type", "pred_action"]].tolist() == ["none", "KEEP"]
    assert out.set_index("image_id").loc["b", "pred_defect_type"] == "dark"
    assert out.set_index("image_id").loc["c", "pred_action"] == "RECAPTURE"
    assert out["score"].tolist() == [3.0, 2.0, 0.1]          # ranking never edited by review


def test_poc_end_to_end_reproducible_and_holds_without_review(tmp_path):
    import json

    from src import main as runner
    outs = []
    for name in ("a", "b"):
        out = tmp_path / name
        assert runner.main(["--config", "configs/lab.yaml", "--track", "t02", "--output", str(out),
                            "--offline", "--seed", "42", "--run-name", "poc"]) == 0
        outs.append(out)
    ranked = pd.read_csv(outs[0] / "ranked_review.csv")
    assert list(ranked.columns[:2]) == ["image_id", "score"] and len(ranked) == 96
    assert (outs[0] / "ranked_review.csv").read_text() == (outs[1] / "ranked_review.csv").read_text()
    dec = json.loads((outs[0] / "decision.json").read_text(encoding="utf-8"))
    assert dec["status"] == "HOLD" and dec["reason_code"] == "REVIEW_PENDING"


def test_apply_review_rejects_bad_override_and_unknown_id():
    with pytest.raises(ValueError, match="override_value"):
        apply_review(_scored(), _rev([["a", "QA", "edit", "fuzzy", ""]]))
    with pytest.raises(ValueError, match="unknown image_id"):
        apply_review(_scored(), _rev([["zzz", "QA", "approve", "", ""]]))
