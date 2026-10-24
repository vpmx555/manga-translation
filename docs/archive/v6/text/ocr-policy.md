# N1 — Bỏ bước phục hồi từ OCR theo Q12

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

Người dùng quyết định không triển khai N1 và giả định LLM không bị nhiễu bởi cách
ngắt chữ tiếng Anh. Không có model, code dehyphenation, lexicon sửa từ hoặc target
cache N1. Cách normalize NFC/khoảng trắng hiện tại ở M6 được giữ; việc ghép lời
thoại nhóm vẫn giữ từng source fragment và thứ tự đọc.

**Dạng dữ liệu đi tiếp tới M7/M8:**

```json
{
  "group_id": "p001:g01",
  "source_fragments": [{"text_id": "p001:t0", "raw_text": "cont-ribu-tion", "bbox": [10,20,110,100], "order": 0}],
  "text": "cont-ribu-tion",
  "normalization_profile": "existing_nfc_whitespace_only",
  "ocr_word_repair": "disabled"
}
```

Đây là giả định thiết kế chưa được kiểm chứng trên model/config local, không phải
kết luận đã đo LLM luôn hiểu đúng. Các fixture `cont-ribu-tion`, ngắt từ qua part,
`well-being` và nói lắp dùng để đánh giá analyze/dịch ở M11, không dùng để xây
bước phục hồi từ. Nếu chất lượng không đạt, ghi lỗi và thảo luận lại; không tự
đưa N1 trở lại vì kết quả xấu.

Việc bỏ N1 không làm các phép so khớp chuỗi ở NER/alias/glossary tự trở nên tương
đương. N3/I1 được giữ theo yêu cầu mở rộng tên mới, nhưng chỉ xử lý trường tên và
khóa so khớp. Chuỗi OCR nguồn vẫn không bị viết lại.

---

[← Tổng quan v6](../README.md)
