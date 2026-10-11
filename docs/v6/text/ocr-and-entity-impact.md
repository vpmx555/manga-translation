# Bổ sung: ảnh hưởng của OCR ngắt từ và nhận nhầm tên

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

### Kết luận từ mã hiện có

Các vấn đề này có thể lan tới nhiều bước sau extract. Normalize hiện chỉ dùng
NFC và gộp khoảng trắng (`normalization/dialogue.py:119`); merge dùng dấu cách
(`:169`). Chưa có dehyphenation theo dòng/nhóm. Vì thế:

```text
cont-ribu-tion         -> cont-ribu-tion
cont-\nribu-\ntion     -> cont- ribu- tion
arase -san             -> arase -san
```

Đây là ví dụ áp dụng phép chuẩn hóa hiện tại, không phải khẳng định các chuỗi đó
đều đã xuất hiện trong probe After School hôm nay.

| Bước | Ảnh hưởng có thể xảy ra |
|---|---|
| Group normalize | Ghép bằng dấu cách vẫn để lại các mảnh của cùng một từ; mất bằng chứng xuống dòng nếu chỉ giữ text đã gộp. |
| GLiNER scan | Từ/tên bị ngắt không còn đúng span hoặc bị đề xuất thành nhiều tên; capitalization/SFX có thể gây false candidate. |
| Alias/name bank | Khóa hiện chủ yếu casefold/strip; `Arase -san`, `Arase-san`, `Arase` chưa có canonical base-name chung. Một tên sai được xác nhận có thể làm sai các scan sau. |
| Analyze/người nói | Mất tín hiệu gọi tên/honorific hoặc dùng tên giả làm identity candidate; ngữ cảnh câu bị vỡ. |
| Glossary/dịch | Literal matching có thể bỏ qua biến thể spacing/hyphen; validator honorific hiện chỉ nhận dạng contiguous ASCII `-san/-kun/-chan`. Model có thể tự sửa nhưng không bảo đảm nhất quán. |
| Dàn chữ | Text sau dịch bị lỗi từ/nghĩa có thể vẫn fit, nên kiểm tra fit không phát hiện lỗi dịch. |

Sửa chuỗi sau extract không tự thay đổi các điểm detect/dialogue/association đã
tính. Giữ raw ID, bbox và association gốc; thay đổi semantic text làm invalid cache
của scan/analyze/dịch và typeset tương ứng.

### Safeguard đã có cho SFX và tên

- `normalization/noise.py:11,39–49` có whole-box rule cho `clench` và `klink`.
  Ignore case/khoảng trắng/dấu câu cuối theo hàm key. `normalization/dialogue.py:55–65`
  áp dụng trước scan/NER. Một câu như `Clench your fists!` vẫn được giữ.
- Rule này không phải classifier SFX tổng quát. Biến thể, các từ chung một box,
  text ghép, override hoặc `keep` exception có thể đi qua.
- Run cũ `20261004T115947Z-569a1a27/03_scan/result.json` của After School có candidate
  `Clench`; `06_export/result.json` trả `mention_type=unknown`, `name_target_id=null`.
  Candidate sai đó không trở thành tên nhân vật đã xác nhận.
- Run diagnostics `translation-normalize-timing-20261005/.../20261005T082031Z-a6e76334/02_normalize/result.json`
  đã loại cả `Klink` và `Clench` với reason `whole_box_sfx`. Không dùng lỗi từ run
  cũ để kết luận safeguard mới chưa hoạt động.
- GLiNER chỉ đề xuất `person name` với threshold 0.85; có lọc pronoun/honorific đơn
  lẻ. Semantic prompt cho phép unknown; binding từ chối unknown/unresolved và cần
  bằng chứng introduction/direct address để gắn tên mới vào identity ổn định.
- Safeguard vẫn có thể thất bại khi model đưa ra quyết định sai nhưng nhất quán
  về cấu trúc. Một tên sai được commit sẽ tạo alias candidate, ảnh hưởng memory
  và đi vào translation, nơi identity export được coi là authoritative.

Nguồn mã: `providers/gliner_ner.py:9,55–72`, `names/stages.py:75,86–97`,
`bank/names.py:121–128`, `analysis/contract.py:42–44`, `analysis/binding.py:103`,
`analysis/pairs.py:31`, `translation/contract.py:59–65`.

---

[← Tổng quan v6](../README.md)
