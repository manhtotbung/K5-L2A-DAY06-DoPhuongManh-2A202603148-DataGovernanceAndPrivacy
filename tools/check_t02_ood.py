"""Run the frozen T02 PoC on ten synthetic holdout images from T03."""

from pathlib import Path
from shutil import copyfile

import pandas as pd

from src.common.manifest import verify_manifest
from src.main import main


if __name__ == "__main__":
    source = Path("fixtures/t03_synthetic")
    if verify_manifest(source / "manifest.csv"):
        raise SystemExit("T03 manifest verification failed")

    images = pd.read_csv(source / "holdout_transformed.csv").sort_values("image_id")
    counts = images.groupby("parent_image").size()
    parents = counts[counts >= 5].index[:2]
    selected = images[images["parent_image"].isin(parents)].groupby("parent_image").head(5)
    if len(selected) != 10:
        raise SystemExit("Expected two parent scenes with five images each")

    root = Path("out/t02_ood")
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    manifest = pd.read_csv(source / "manifest.csv").set_index("path")
    metadata = []
    provenance = []
    for index, row in enumerate(selected.itertuples(index=False)):
        image_id = f"{row.parent_image}_{index}"
        path = f"{image_id}.jpg"
        copyfile(source / row.path, input_dir / path)
        metadata.append(dict(image_id=image_id, path=path, camera_id="unknown", light="unknown", split="ood"))
        provenance.append(dict(manifest.loc[row.path], path=path, file_id=image_id, group_id=row.parent_image, split="ood"))

    pd.DataFrame(metadata).to_csv(input_dir / "images.csv", index=False)
    pd.DataFrame(provenance).to_csv(input_dir / "manifest.csv", index=False)
    output = root / "poc"
    status = main([
        "--config", "configs/lab.yaml", "--input", str(input_dir), "--output", str(output),
        "--run-name", "t02_ood", "--offline", "--seed", "42",
    ])
    if status:
        raise SystemExit(status)

    ranked = pd.read_csv(output / "ranked_review.csv")
    sources = pd.DataFrame({"image_id": [r["image_id"] for r in metadata], "source_path": selected["path"].tolist()})
    columns = ["image_id", "source_path", "score", "pred_defect_type", "pred_action"]
    ranked.merge(sources, on="image_id", validate="one_to_one")[columns].to_csv(root / "results.csv", index=False)
