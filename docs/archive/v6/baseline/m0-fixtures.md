# M0 — Khóa baseline và tạo bộ ca kiểm chứng

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** snapshot nguồn/config/model; chọn các ca lỗi từ chapter và tạo
fixture có annotation. Review ảnh để xác nhận nhãn tham chiếu. Không tự coi output
hiện tại hoặc AIP419 là ground truth.

**Mô hình:** không inference mới để tạo annotation. Preflight chỉ kiểm tra asset,
môi trường Python, font và Ollama local. Baseline cùng config 0.45 cần chạy hoặc
lấy từ cache có receipt phù hợp; lọc output cũ 0.25 chỉ là một view phụ.

**Input:** 11 ảnh tại `D:\download\EN-data\After School\EN\Chapter 1`, output probe
hiện tại, kết quả AIP419 và config đã chốt.

**Output đề xuất:**

```json
{
  "schema_version": 1,
  "source_manifest": [{"page_id": "p001", "path": "<absolute-image-path>", "sha256": "<hash>", "width": 1200, "height": 1800}],
  "cases": [{
    "case_id": "joined-cross-panel",
    "page_id": "p001",
    "category": "joined_cross_panel",
    "annotation_status": "confirmed",
    "expected_groups": [{"source_text_ids": ["p001:t0", "p001:t1"], "part_count": 2}],
    "reference_masks": ["fixtures/body-01.png", "fixtures/body-02.png"],
    "visual_labels": {"bubble_style": "unknown"}
  }],
  "receipts": {"baseline_config": "<fingerprint>", "models": "<receipts-path>"}
}
```

**Điều kiện hoàn thành:** source/receipt khớp; ca tham chiếu được review. Nếu
chapter thiếu ca nhiều panel hoặc nối phức tạp, bổ sung fixture hình học tổng hợp
cho kiểm tra cơ chế; ghi riêng kết quả tổng hợp và ảnh thật. Không suy ra chất
lượng thực tế từ fixture tổng hợp.

---

[← Tổng quan v6](../README.md)
