# M4 — Dựng contour kín, tách thân/đuôi/cầu nối

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp gồm bốn bước:**

1. Trace biên bằng phương pháp cạnh ô pixel hiện có, giữ các component/ring cần
   thiết và provenance. Không chỉ lấy polygon diện tích lớn nhất.
2. Từ distance transform và skeleton của mask, tìm các vùng thân có độ rộng lớn,
   các cổ nối hẹp và nhánh đuôi. Text anchor, tail box và candidate con là bằng
   chứng bổ sung. Chỉ tách thân khi có cổ nối và các vùng thân phù hợp; nhiều OCR
   box trong một oval không tự tạo nhiều part.
3. Hiệu chỉnh những đoạn biên lệch theo nét ảnh trong dải tìm kiếm quanh contour.
   Dùng đường đi có chi phí kết hợp khoảng cách với mask gốc, bằng chứng nét mực
   và độ đổi hướng. Tránh kéo biên vào chữ/nét tranh. Đoạn có nét đứt vẫn giữ
   thông tin nét đứt từ ảnh trước khi khép.
4. Khép mọi ring bằng đoạn được quan sát/được sửa. Khe lớn thiếu bằng chứng dùng
   nội suy có ràng buộc hoặc biên mask gốc; vùng bị cắt tại mép trang khép trong
   giới hạn ảnh. Ghi loại sửa và mức bằng chứng. Nếu polygon tự cắt, sửa và kiểm
   tra; fallback cuối dùng biên kín của mask hợp lệ. Candidate suy biến không có
   vùng hai chiều hợp lệ bị đánh dấu `invalid_candidate`, không tạo contour giả.

“Contour toàn bộ” giữ đuôi/cầu nối; “contour thân” có đường cắt nội bộ đóng kín
part. Đường cắt nội bộ được đánh dấu `partition_cut`, không được classifier đọc
như đường viền thực hay bằng chứng caption. Các part có thể còn liên thông trong
mask toàn nhóm, nhưng vùng dàn chữ của chúng tách rời.

**Mô hình:** không có. Mask morphology, distance transform/skeleton, đồ thị đường
đi và xử lý polygon. Lựa chọn thư viện cụ thể phải tương thích môi trường layout
hiện có; không yêu cầu thay checkpoint segmentation.

**Input:** group topology/mask M3, ảnh gốc, bbox OCR/tail M2.

**Output:** `group-geometry.json`; observed/refined/body/bridge/tail masks riêng.

```json
{
  "schema_version": 2,
  "group_id": "p001:g01",
  "outer_rings": [[[300,400],[600,400],[720,900],[300,900],[300,400]]],
  "contour_status": "closed_reconstructed",
  "segments": [{
    "ring_index": 0, "start_vertex": 1, "end_vertex": 2,
    "kind": "reconstructed", "evidence": "mask_fallback"
  }],
  "parts": [{
    "id": "p001:g01:part01", "source_text_ids": ["p001:t0"],
    "body_rings": [[[320,420],[590,420],[590,610],[320,610],[320,420]]],
    "body_mask_path": "visual-v2/masks/p001/g01-part01-body.png",
    "tail_mask_path": "visual-v2/masks/p001/g01-part01-tail.png",
    "partition_segments": [],
    "panel_ids": ["p001:panel01"], "reading_order": 0
  }],
  "bridge_mask_paths": ["visual-v2/masks/p001/g01-bridge01.png"]
}
```

**Điều kiện hoàn thành:** các ring đầu ra kín và hợp lệ; giữ riêng đoạn suy ra;
part có vùng thân phù hợp text; bridge/đuôi không vào vùng typeset. Contour kín
không đồng nghĩa contour đúng; tỷ lệ biên được sửa đi vào bằng chứng M5 và review.

---

[← Tổng quan v6](../README.md)
