# Iris Zero chapter 1 — kết quả reanalyze và review local

Batch `iris-zero-ch1-reanalyze-v7-l40s-20261026-r9` nhập artifact đến hết analyze rồi chạy một request reanalyze thật trên L40S Secure. Không chạy thêm test và không suy luận lại OCR/graph/analyze.

| Mục | Kết quả |
|---|---|
| Run | `20261026T130516Z-344ff898` |
| Trạng thái reanalyze | `completed_with_review` |
| Fallback về analyze | `false` |
| Context input | 45.372 token; tổng budget 54.076/55.705 token, context 64K |
| Được nhận | 4 replacement utterance và 2 mô tả event |
| Bị bác | 23 đề xuất: 17 speaker/cluster không khớp, 1 mention ID không thuộc câu, 5 mô tả phụ thuộc correction chưa áp dụng |
| Còn unresolved | 25/260 utterance |
| Automatic retry | 0 |

Đây là kết quả để review, không phải nhãn đúng đã được con người xác nhận. Bản analyze gốc có 7 event lỗi và 14 thành phần tên cần review; lỗi merge làm sập stage trước đó đã được xử lý bằng cách giữ hậu tố sai vào audit thay vì đưa vào chỉ mục tên.

## Mở lại từ backup đã xác minh

Chạy từ gốc repository:

```powershell
venv/Scripts/python.exe -B scripts/materialize_verified_run.py --batch outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9 --run-id 20261026T130516Z-344ff898
Start-Process 'outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/07_reanalyze/review.html'
```

Script yêu cầu `verification.json` có trạng thái `replica_archives_verified`, kiểm tra hash archive và inventory từng file, rồi chỉ giải nén đúng run được chọn. Không sửa file local đã bị thay đổi và không gọi model. Lượt này đã lấy đủ 2.247 file; các ảnh được HTML tham chiếu có trong thư mục đã giải nén.

Đọc `07_reanalyze/changes.json` để xem accepted/rejected và lý do, `result.json` để xem snapshot hiệu lực. `packet.json` là input đầy đủ; `wire-packet.json` là input bảng gọn gửi model và `reference-ledger.json` ánh xạ ID. Giữ cả thư mục run để các ảnh tương đối trong HTML vẫn mở được.

Code: [materialize_verified_run.py](../../../scripts/materialize_verified_run.py). Thiết kế/gate: [stage reanalyze](../stages/07_reanalyze.md).
