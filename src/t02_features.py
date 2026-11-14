"""T02 image-quality features (label-free). Shared by the baseline and the PoC.

Every feature is computed from pixels only — no ground truth is read here.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .common.image_ops import brightness_stats, laplacian_variance, load_rgb, to_gray

BLOCK = 8          # JPEG block size
GLARE_GRID = (4, 8)  # rows x cols of tiles for local-glare detection


def blockiness(gray: np.ndarray, block: int = BLOCK) -> float:
    """Ratio of mean |gradient| on 8-px block boundaries vs. inside blocks (1.0 = no blocking)."""
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    bx = (np.arange(dx.shape[1]) % block) == block - 1
    by = (np.arange(dy.shape[0]) % block) == block - 1
    on = np.concatenate([dx[:, bx].ravel(), dy[by, :].ravel()]).mean()
    off = np.concatenate([dx[:, ~bx].ravel(), dy[~by, :].ravel()]).mean()
    return float(on / max(off, 1e-6))


def local_glare(gray: np.ndarray, grid: tuple[int, int] = GLARE_GRID, thr: float = 245.0) -> float:
    """Max fraction of saturated pixels over a tile grid (catches glare on a small region, e.g. a plate)."""
    H, W = gray.shape
    rs = np.linspace(0, H, grid[0] + 1).astype(int)
    cs = np.linspace(0, W, grid[1] + 1).astype(int)
    best = 0.0
    for i in range(grid[0]):
        for j in range(grid[1]):
            t = gray[rs[i]:rs[i + 1], cs[j]:cs[j + 1]]
            if t.size:
                best = max(best, float((t > thr).mean()))
    return best


def image_features(path: str | Path) -> dict:
    img = load_rgb(path)
    g = to_gray(img)
    b = brightness_stats(img)
    return {
        "lap_var": laplacian_variance(img),
        "bright_mean": b["mean"],
        "bright_p95": b["p95"],
        "frac_dark": b["frac_dark"],
        "frac_saturated": b["frac_saturated"],
        "local_glare": local_glare(g),
        "blockiness": blockiness(g),
    }


def compute_features(images: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Add feature columns + `scene_id` (image_id `img_SS_V` -> `img_SS`) to images.csv rows."""
    feats = pd.DataFrame([image_features(root / p) for p in images["path"]], index=images.index)
    out = pd.concat([images, feats], axis=1)
    out["scene_id"] = out["image_id"].str.rsplit("_", n=1).str[0]
    return out
