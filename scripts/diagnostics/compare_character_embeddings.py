"""Compare stored character prototypes without loading models or changing the bank."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def normalized(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or not len(values) or not np.isfinite(values).all():
        raise ValueError("Expected a nonempty finite embedding matrix")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("Zero embedding cannot be normalized")
    return values / norms


def scores(distances):
    nearest = np.sort(distances, axis=1)[:, :min(3, distances.shape[1])]
    return 0.6 * nearest[:, 0] + 0.4 * nearest.mean(axis=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", required=True, type=Path)
    parser.add_argument("--reference", type=int, default=4)
    parser.add_argument("--compare", type=int, nargs="+", default=[10, 13, 15])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    metadata = json.loads((args.bank / "metadata.json").read_text(encoding="utf-8"))
    matrices, indices = {}, {}
    with np.load(args.bank / "embeddings/vectors.npz", allow_pickle=False) as archive:
        for identity in [args.reference, *args.compare]:
            record = metadata["characters"][str(identity)]
            if record.get("disabled"):
                raise ValueError(f"Character {identity} is disabled")
            disabled = set(record.get("disabled_prototype_indices", []))
            raw = archive[f"character_{identity}"]
            indices[identity] = [i for i in range(len(raw)) if i not in disabled]
            matrices[identity] = normalized(raw[indices[identity]])
    reference = matrices[args.reference]
    centroid = normalized(reference.mean(axis=0, keepdims=True))[0]
    report = {"bank": str(args.bank.resolve()), "reference": args.reference,
              "config": metadata["config"], "dimensions": reference.shape[1],
              "note": "Prototype indices are not crop-preview indices. Scores use the final bank, not historical assignment traces.",
              "comparisons": []}
    distances_by_id = {}
    for identity in args.compare:
        other = matrices[identity]
        if other.shape[1] != reference.shape[1]:
            raise ValueError("Embedding dimensions differ")
        distance = np.linalg.norm(reference[:, None, :] - other[None, :, :], axis=2)
        distances_by_id[identity] = distance
        position = np.unravel_index(distance.argmin(), distance.shape)
        score_to_reference = scores(distance.T)
        report["comparisons"].append({
            "id": identity, "reference_indices": indices[args.reference],
            "other_indices": indices[identity], "distance_matrix": distance.tolist(),
            "min_distance": float(distance.min()), "median_distance": float(np.median(distance)),
            "max_distance": float(distance.max()),
            "nearest_pair": [indices[args.reference][position[0]], indices[identity][position[1]]],
            "nearest_cosine": float(1 - distance.min() ** 2 / 2),
            "centroid_distance": float(np.linalg.norm(centroid - normalized(other.mean(axis=0, keepdims=True))[0])),
            "reference_query_scores": scores(distance).tolist(),
            "other_query_scores": score_to_reference.tolist(),
            "other_queries_under_distance_limit": int((score_to_reference <= metadata["config"]["max_distance"]).sum()),
        })
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (args.output / "distances.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["reference_id", "reference_prototype", "other_id", "other_prototype", "distance", "cosine"])
        for item in report["comparisons"]:
            for row, values in enumerate(item["distance_matrix"]):
                for column, value in enumerate(values):
                    writer.writerow([args.reference, item["reference_indices"][row], item["id"],
                                     item["other_indices"][column], value, 1 - value ** 2 / 2])
    figure, axes = plt.subplots(1, len(args.compare), figsize=(5 * len(args.compare), 5), squeeze=False,
                                constrained_layout=True)
    upper = max(float(value.max()) for value in distances_by_id.values())
    for axis, identity in zip(axes[0], args.compare):
        distance = distances_by_id[identity]
        plotted = axis.imshow(distance, vmin=0, vmax=upper, cmap="viridis_r")
        axis.set_title(f"ID {args.reference} vs ID {identity}")
        axis.set_xlabel(f"ID {identity}: stored prototype index")
        axis.set_ylabel(f"ID {args.reference}: stored prototype index")
        axis.set_xticks(range(len(indices[identity])), indices[identity])
        axis.set_yticks(range(len(indices[args.reference])), indices[args.reference])
        for row in range(distance.shape[0]):
            for column in range(distance.shape[1]):
                value = distance[row, column]
                axis.text(column, row, f"{value:.2f}", ha="center", va="center", fontsize=8,
                          color="white" if value > upper * .5 else "black")
    figure.colorbar(plotted, ax=list(axes[0]), shrink=.75, label="Normalized Euclidean distance (lower = closer)")
    figure.suptitle("Bocchi: stored MAGI embeddings; indices do not identify preview crops")
    figure.savefig(args.output / "distances.png", dpi=180)
    plt.close(figure)
    lines = ["# ID 4 so với ID 10, 13, 15 — bank Bocchi hiện tại", "",
             "Tính trên vector đã lưu, không chạy lại model và không thay đổi bank. Mỗi ID có 8 vector 768 chiều trong lần kiểm tra này.", "",
             "Khoảng cách Euclid sau chuẩn hóa L2: càng nhỏ càng gần. Cosine không phải xác suất cùng người.", "",
             "| So với ID 4 | Cặp gần nhất | Khoảng cách nhỏ nhất | Trung vị 64 cặp | Khoảng cách centroid | Cosine cặp gần nhất |",
             "| --- | --- | --- | --- | --- | --- |"]
    for item in report["comparisons"]:
        pair = item["nearest_pair"]
        lines.append(f"| ID {item['id']} | 4[{pair[0]}] / {item['id']}[{pair[1]}] | {item['min_distance']:.4f} | {item['median_distance']:.4f} | {item['centroid_distance']:.4f} | {item['nearest_cosine']:.4f} |")
    lines += ["", "![Ma trận khoảng cách](distances.png)", "",
              "## Điểm ghép vào ID 4 theo công thức pipeline", "",
              "Cho từng prototype làm query, tính `0.6 × khoảng cách gần nhất + 0.4 × trung bình tối đa 3 khoảng cách gần nhất` đến prototype ID 4.", "",
              "| Query ID | Điểm của từng prototype, theo thứ tự chỉ số trong bank | Đạt max_distance 0,65 |",
              "| --- | --- | --- |"]
    for item in report["comparisons"]:
        values = ", ".join(f"{index}: {value:.4f}" for index, value in zip(item["other_indices"], item["other_query_scores"]))
        lines.append(f"| {item['id']} | {values} | {item['other_queries_under_distance_limit']}/{len(item['other_indices'])} |")
    lines += ["", "Đạt ngưỡng khoảng cách chưa đủ để gán ID: còn margin so với ứng viên thứ hai và các ràng buộc panel. Query thực tế khi extract là vector trung bình của cluster, không nhất thiết là một prototype đơn lẻ.", "",
              "## Giới hạn", "",
              "Bank chỉ giữ tối đa 4 crop preview nhưng tới 8 prototype; prototype có thể được bổ sung và chọn lại. Không có ánh xạ bền vững từ chỉ số vector sang crop_paths. Không gắn tên ảnh cho các vector trong ma trận này.", "",
              "Các số trên mô tả bank cuối hiện tại, không tái hiện chính xác mọi quyết định tạo ID trước đó. Không tự động gộp ID từ một cặp vector gần nhau.", "",
              "Dữ liệu chi tiết: [JSON](comparison.json), [CSV từng cặp](distances.csv)."]
    (args.output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Comparison saved: {args.output}")


if __name__ == "__main__":
    main()
