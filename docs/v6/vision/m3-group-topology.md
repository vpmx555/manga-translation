# M3 — Hợp nhất candidate và giữ nhóm nối xuyên panel

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** đồ thị candidate trên tọa độ toàn trang, xét mask overlap,
containment, text anchors và bằng chứng mép crop. Phân biệt ba trường hợp:

1. Hai lượt predict cùng một vùng: chọn/hợp nhất đại diện, giữ cả provenance.
2. Một candidate toàn nhóm và các candidate chỉ nhìn thấy một phần: lưu quan hệ
   parent/part; không suppression làm mất các phần.
3. Hai bubble độc lập gần nhau: giữ độc lập trừ khi có bằng chứng nối phù hợp.

Ngưỡng gần nhau dùng `gap_ratio = d_min / min(r_left, r_right)` với
`r = sqrt(body_area/pi)`, tính khoảng cách trên contour độ phân giải gốc. Tham số
`reconnect_gap_ratio` được hiệu chỉnh ở M11; khoảng cách chỉ tạo ứng viên xét nối.
Vách viền riêng của hai bubble và cấu trúc ảnh phải được xét trước khi nối thêm.

Cầu nối đã quan sát được trong ảnh/mask tạo edge `observed_bridge`. Khi chia thân,
edge đó vẫn thuộc group. Không dùng contour được nội suy ở M4 làm bằng chứng duy
nhất để gộp hai group. Mỗi group và part có danh sách panel giao nhau; panel index
chính chỉ phục vụ adapter cũ/thứ tự đọc, không cắt group ở biên panel.

**Mô hình:** không có. Xử lý raster mask, connected components, khoảng cách và
đồ thị bằng code.

**Input:** candidates/masks M1, text/tail anchors M2, ảnh gốc và panel snapshot.

**Output:** `group-topology.json` cùng mask đại diện, giữ candidate nguồn.

```json
{
  "page_id": "p001",
  "groups": [{
    "id": "p001:g01", "candidate_ids": ["p001:c01", "p001:c04"],
    "panel_ids": ["p001:panel01", "p001:panel02"],
    "mask_path": "visual-v2/masks/p001/g01-observed.png",
    "connections": [{
      "id": "p001:g01:bridge01", "kind": "observed_bridge",
      "mask_path": "visual-v2/masks/p001/g01-bridge01.png",
      "evidence": ["mask_connectivity", "image_boundary_continuity"]
    }],
    "status": "resolved"
  }]
}
```

**Điều kiện hoàn thành:** crop trùng không nhân đôi text/group; candidate chứa cả
nhóm không nuốt mất part; group xuyên panel giữ đầy đủ mask. Lưu lý do và trạng
thái khi chưa giải quyết được topology.

---

[← Tổng quan v6](../README.md)
