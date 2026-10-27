# Sửa OCR giữ nguyên cách gọi — scan v4

> Historical documentation before chapter-story-graph-v7. See [current docs](../../../v7/README.md).

Đã triển khai ngày 24/10/2026. Phạm vi là sửa OCR trong scan và hướng dẫn model;
thuật toán gộp MAGI/identity không thay đổi trong bản này.

## Hành vi mới

`canonical_name` là tên đầy đủ của alias group, chỉ dùng làm metadata. `new_name`
chỉ sửa chính tả, khoảng trắng và gạch nối của cách gọi thực sự có trong câu.

| Tên trong câu | Kết quả được phép | Group metadata có thể là |
|---|---|---|
| TORU | Toru | Toru Mizushima |
| KOYUKI | Koyuki | Koyuki Sasamori |
| MIZU- SHIMA | Mizushima | Toru Mizushima |
| sasa- mori - chan | Sasamori-chan | Koyuki Sasamori |
| Koyuk | Koyuki, chỉ khi Koyuk là một từ nguyên vẹn | Koyuki Sasamori |
| Mizushlma Toru | Mizushima Toru, giữ thứ tự tên trong câu | Toru Mizushima |

Prompt và JSON schema mô tả riêng canonical_name, old_name, new_name. Model
được yêu cầu không thêm/bỏ thành phần tên, không đổi thứ tự, không thêm/bỏ/đổi
honorific, không thay surname thành given name và không chép tên đầy đủ của
group vào mọi new_name. Tên không lỗi vẫn có thể trả old_name=new_name để gom alias.

## Guard nhỏ trong code

- Match toàn bộ từ, với ranh giới Unicode; không cắt một ký tự casefold mở rộng.
  Koyuk không khớp tiền tố của Koyuki; yuki-chan không khớp bên trong Koyuki-chan;
  MIZUSHIMA-SA không khớp phần đầu của MIZUSHIMA-SAN.
- Vẫn bỏ qua hoa/thường, khoảng trắng, gạch nối khi tìm tên và replace-all trong
  chapter. Các rule hợp lệ vẫn chạy tuần tự, có cascade theo thứ tự model.
- Giữ các surface dài đã đúng: rule cho Toru không sửa một phần Toru Mizushima
  đã đúng. Rule cho toàn bộ surface vẫn có thể sửa lỗi chính tả của nó.
- Nếu có chèn/bỏ phần tên hoặc đảo thứ tự rõ ràng, giữ tên nguồn, giữ group/name
  candidate và ghi `ocr_name_scope_change_not_applied` với đề xuất nguyên văn để
  review. Không loại cả response hoặc tự gọi model lại.

Ví dụ model vẫn trả `TORU → Toru Mizushima`: OCR giữ TORU; cảnh báo lưu old_name,
proposed_new_name, kept_name và kind=name_expansion. Code không tự đoán rằng
model đã đúng khi đổi cách gọi. Đây là guard phạm vi sửa, không phải từ điển
chính tả hoặc bộ xác thực identity.

Guard nhận diện thay đổi phần tên rõ ràng bằng cấu trúc chuỗi. Nó không chứng
minh một tên mới bất kỳ là đúng về ngữ nghĩa; yêu cầu prompt vẫn cần được đánh
giá bằng inference riêng.

## Version và compatibility

Mặc định mới: `v6.scan_policy=canonical-scan-v4`, trong Python và bốn JSON config
hiện hành. Module triển khai vẫn tên `scan_v3.py` để giữ đường import.

- Artifact v3 vẫn đọc/review/export từ canonical snapshot đã lưu, không chạy
  lại các rule bằng thuật toán mới và không viết lại kết quả lịch sử.
- Muốn chạy scan mới phải dùng config v4 và batch mới. Thử chạy scan v3 bằng
  source hiện hành báo lỗi rõ, yêu cầu dùng frozen source cũ hoặc prepare v4.
- Import kiểm scan contract nên v3 không bị dùng lại như kết quả v4. Khi test
  bản sửa, import tới normalize rồi chạy lại scan và downstream.
- `scan_output_tokens`, concurrency, no-retry và ngưỡng identity giữ nguyên.

## Kiểm tra

**165 test trong 13 module đã qua**, gồm 13 regression mới trong
`tests/test_scan_surface_spelling.py`. Các case mới kiểm tên ngắn/full name,
scope expansion/shortening/reordering, honorific, tiền tố/hậu tố, Unicode,
sequential cascade, prompt/schema và compatibility v3.

Đã replay **response lưu sẵn**, không gọi model, trên 260 câu của run
`20261024T051844Z-f3400353`. Kết quả parser v4:

| Mẫu lỗi trong OCR sau sửa | Parser v3 đã sinh | Parser v4 |
|---|---:|---:|
| Mizushima Mizushima | 1 | 0 |
| Sasamori Sasamori | 2 | 0 |
| Koyukii | 9 | 0 |
| Mizushima-sann | 7 | 0 |

Giữ 5 group, chặn 11 đề xuất sửa vượt phạm vi. Replay mất khoảng0.43 giây trên
máy local trong lần đo; chỉ kiểm parser với response cũ, không đánh giá phản hồi
của model dưới prompt mới. Không chạy Runpod hoặc inference trong lượt sửa này.
Artifact của run cũ và registry không bị sửa.

## Source chính

- `src/manga_pipeline/v6/scan_v3.py`: prompt/schema, whole-word matching, guard
  thay đổi scope, bảo vệ full surface, cache thuần text trong một lần parse.
- `v6/config.py` và `configs/pipeline*.json`: policy v4.
- `v6/semantic.py`: chặn rerun v3 dưới source mới.
- `v6/name_normalization.py`, `independent.py`, `verification.py`, `stages.py`,
  `planning.py`, `review/v6.py`: đọc được v3/v4, dùng đúng canonical OCR.

[Scan contract](../text/scan-redesign.md) · [Validator](../foundation/validators.md) ·
[Tự chạy Runpod](../deployment/self-service-test.md)
