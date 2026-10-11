# Mô hình, phiên bản và phạm vi sử dụng

> Luồng v6 đã được triển khai; xem [trạng thái kiểm chứng](../delivery/implementation-status.md). Chạy local; pilot After School chapter 1.

| Mốc | Mô hình/phương pháp | Vai trò |
|---|---|---|
| M1 | `mayocream/koharu-layout-rfdetr-seg-2xl-1152` | Detect panel/bubble và mask; bổ sung bbox/score lớp onomatopoeia làm bằng chứng SFX. |
| M2 | `ragavsachdeva/magiv2` | Detect text/nhân vật, candidate association, tail box/association. |
| M2 | `JustANormalTinkerer/hayai-ocr-v2.5-nova` | OCR các vùng text; môi trường riêng theo code hiện có. |
| M3–M6 | Xử lý mask, đồ thị, hình học và ảnh bằng code | Gộp candidate, giữ topology, tạo contour/thân và đơn vị lời thoại. M5b gợi ý loại thoại chỉ từ ảnh/mask, không nhận text hoặc gọi model riêng; M5a mapping tách riêng. |
| N2 | Bằng chứng Koharu onomatopoeia, code và Ollama local khi cần phân giải | Phân biệt SFX, nhãn hành động/trạng thái, lời thoại, narration và unknown. |
| N3/I1 | Code tạo khóa tên/honorific chữ thường; xác nhận tên vẫn ở M7 | So khớp biến thể và thành phần tên; giữ OCR gốc, không thêm gate tên độc lập. |
| I2 | Code tổng hợp calling events M7; MAGI embedding/crops đọc lại | Caller-first giữ person qua nhiều appearance; ảnh chỉ hỗ trợ, không thêm call so ảnh khi embedding xa. |
| I3 | Code reconcile identity/redirect, event log và validator | Ánh xạ nhiều cluster về identity chuẩn, bảo toàn lịch sử và kiểm tra mâu thuẫn. |
| M7 | GLiNER hiện có cho scan; Ollama local `gemma4:e4b-it-q4_K_M` cho analyze | Kết hợp text/ngữ cảnh với visual hints để chốt loại thoại, tên và speaker cấp nhóm trong cùng lượt analyze; dùng ảnh khi analyze đã cần, không thêm call style. |
| M8, M10 | Ollama local cùng model của M7 | Dịch đầy đủ và dịch lại ngắn hơn khi cần. |
| M9 | Raster mask, font metrics FreeType/Pillow, tối ưu ngắt dòng | Dàn chữ. Không có model học. |
| M11, khi inpaint được bật | Cơ chế nền trắng/AOT hiện có | Làm sạch vùng chữ gốc trước khi render. Không xóa toàn vùng contour mới. |

Revision đã có trong mã:

```text
Koharu: aed55fdb8ca953c6bec33cf6ed6dd52a9b72bfa2
MAGIv2: fbc890fec52977142e8ee00bfe26e9458b65517c
Hayai:  2803d0dc005fa4396ab1e7c5a472d2c450a247f7
Hayai processor: b53b807d3a2d5e2b3911292f2d69e5341cdc064c
AOT:    cffe2346ac2b5ebe1f2d61335d602d12cc144c6f
GLiNER model: gliner-community/gliner_small-v2.5
```

Revision GLiNER, model digest Ollama, font file/hash và config được ghi từ môi
trường thực tế vào receipt. Tag Ollama có trong config chưa chứng minh model đã
được cài hoặc chạy thành công; M0 kiểm tra khả năng sử dụng local.

Các ngưỡng đã chốt được giữ cho run mới:

```json
{
  "text_detection_threshold": 0.45,
  "text_classification_threshold": -0.5,
  "text_character_matching_threshold": 0.25,
  "character_detection_threshold": 0.35,
  "character_character_matching_threshold": 0.75,
  "layout": {"bubble_threshold": 0.45, "panel_threshold": 0.5, "crop_padding": 24},
  "segmentation_mask_cutoff": 0.5
}
```

Ngưỡng dialogue là logit, association là affinity sigmoid; hai ngưỡng được áp
dụng độc lập. Điểm association theo từng text là bằng chứng cho M7, không phải
quyết định người nói của cả nhóm.

---

[← Tổng quan v6](../README.md)
