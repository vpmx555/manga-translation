# Tạm ngừng speaker groups và bảo vệ narrator

Run v3 mới ghi `speaker_policy: individual-v1` trong cấu hình mặc định, `manifest.json` → `config`, `snapshots/config.json`, request của từng batch plan và request journal. Schema, prompt, validation và fallback cùng dùng policy của request đã lưu. Schema vẫn có đúng một nhánh cho mỗi target; code kiểm tra cặp narrator–loại câu.

## Hợp đồng

- Speaker chỉ là stable ID trong tập ứng viên của target, `others` hoặc `narrator`. `groups` không xuất hiện trong enum hoặc hướng dẫn LLM của run mới. Không tự chuyển lời tập thể thành một ID nhân vật.
- `narrator` yêu cầu `content_type: narration`. Dialogue/thought/unknown với narrator ghi `narrator_requires_narration`, mở riêng `speaker_id` cho repair khi các trường khác hợp lệ.
- Narration vẫn được nói bởi ID nhân vật hoặc `others`; không ép narration thành narrator và không ép kiểu người nghe.
- Dùng tối đa một lượt repair hiện có cho phần lỗi. Các target đã hợp lệ và trường độc lập hợp lệ được giữ lại; self được chuẩn hóa theo speaker cuối.
- Nếu repair vẫn trả narrator không hợp lệ, groups, hoặc không có kết quả, speaker fallback thành `others`. Lưu cảnh báo `unresolved_after_repair`, hoàn tất target; resume tái dùng kết quả và journal, không gọi lại model cho phần đã xử lý.

## Run cũ và reanalyze

Run v3 thiếu trường policy giữ nguyên hợp đồng cũ, kể cả các batch đang dở hoặc chưa có plan. Không bổ sung trường vào snapshot, plan, journal hay kết quả đã commit của run cũ. `groups` trong output cũ tiếp tục được export và review hiển thị. Run v2 tiếp tục nhánh legacy.

Dùng `python run.py reanalyze --run-dir <old-run-directory>` để tạo run theo policy mới và tái dùng extraction đã hoàn tất. Config/snapshot của source và hash kết quả đã commit được giữ nguyên.

## Giới hạn và kiểm chứng

Quy tắc không phát hiện trường hợp model đồng thời đánh sai loại câu thành narration và speaker thành narrator. Phân biệt we/us, mở rộng định nghĩa groups và cải thiện bằng chứng chọn speaker được hoãn lại. P03/T01 và P03/T07 chưa được cam kết thành groups.

Regression nằm trong `tests/analysis/test_speaker_policy.py`: enum/prompt/policy lưu trên đĩa; narrator với dialogue/thought/unknown; narration với ba kiểu speaker hợp lệ; repair giữ trường độc lập; fallback có cảnh báo; crash trước commit tái dùng journal; resume v3 cũ đang dở giữ hash; policy không khớp plan/journal bị từ chối. Bộ test legacy và reanalyze tiếp tục kiểm tra luồng v2 và tái dùng extraction.

Chạy toàn bộ test offline bằng `python -B -m unittest discover -s tests -v`. Các test này xác nhận hợp đồng xử lý, không đo lại chất lượng suy luận của model trên chapter thật.

Kiểm chứng ngày 2026-10-05: 107/107 test pass trong môi trường venv của repo. Các regression xác nhận run mới lưu đúng policy; run v3 cũ đang dở giữ hash snapshot, plan, journal và target đã commit; fallback và journal được tái dùng khi resume.
