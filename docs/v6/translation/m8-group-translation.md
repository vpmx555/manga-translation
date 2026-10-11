# M8 — Dịch toàn bộ lời thoại nhóm

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

**Phương pháp:** một target translation cho mỗi group utterance. Model nhận câu
đầy đủ, speaker/style đã analyze, context, glossary và ngôn ngữ đích. Output một
chuỗi chung; không yêu cầu dịch độc lập theo part. Cache key gồm group membership,
text nguồn, analysis, glossary và model/config fingerprint.

**Mô hình:** Ollama local, model của M7. Không thay provider trong thiết kế này.

**Input:** analyzed utterance M7 và cấu hình dịch hiện có.

**Output:**

```json
{
  "utterance_id": "p001:u01", "group_id": "p001:g01",
  "source_text": "I thought you would come.",
  "translation": "Tôi cứ nghĩ bạn sẽ đến.",
  "target_language": "vi", "speaker_id": null,
  "variant": "full", "status": "translated", "attempt": 0
}
```

**Điều kiện hoàn thành:** đủ nội dung nguồn; cùng nhóm có đúng một bản dịch đầu
vào cho typeset. Không chia bản dịch dựa trên tỷ lệ số ký tự OCR gốc trước khi đo
sức chứa thật của các vùng.

---

[← Tổng quan v6](../README.md)
