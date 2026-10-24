# Scan tên và chia scene trong cùng lượt LLM

Cấu hình mặc định `ner.backend = "llm-scene"` dùng LLM hiện tại của pipeline để
chia scene và tìm tên trong cùng phản hồi tại bước `scan`. Không tải hoặc chạy
GLiNER trong luồng này. Các trường model/revision/threshold/device cũ trong `ner`
chỉ dành cho chế độ tương thích `gliner`; không điều khiển LLM scan.

Scene chỉ dùng các câu essential; tìm tên dùng toàn bộ utterance đã normalize.
LLM trả tên dưới dạng nguyên văn OCR và utterance ID. Code tìm mọi lần xuất hiện,
kiểm tra biên từ và tạo offset từ nguồn. Không chấp nhận tên trích xuất không có
trong câu. Đối chiếu tên/alias đã có trong person index vẫn được giữ lại.
Không tự gán confidence cho kết quả LLM.

## Chuẩn hóa tên bằng patch và nhóm alias

Cùng phản hồi scene/name còn có `name_normalization`: `patches`, `groups`,
`suspicions`. LLM không trả lại câu hay document đã viết lại. Patch gồm
`utterance_id`, `start`, `end`, `original`, `replacement`, `status`, `evidence`,
`evidence_refs`; offset tính theo ký tự Unicode Python, zero-based `[start,end)`
trên text normalize gốc trước mọi patch. Code kiểm tra vị trí chọn đúng một tên,
chuỗi nguồn, honorific, bằng chứng, và xung đột; áp dụng các patch hợp lệ từ phải
sang trái. Những đoạn ngoài tên không đổi.

Ví dụ `HI SASA- MORI - CHAN!` thành `HI SASAMORI-CHAN!`. Không đổi tên ngắn
thành tên đầy đủ. Giữ kiểu chữ toàn hoa/toàn thường và honorific. Sửa lỗi chữ
cần cách viết được hỗ trợ từ một lần xuất hiện rõ trong chapter hoặc registry;
chỉ nhiều lần lặp lại chưa đủ bằng chứng. Patch nghi vấn/không hợp lệ giữ nguyên
nguồn; các patch chồng lấn bị loại toàn bộ, các patch độc lập vẫn tiếp tục.

Kết quả scan giữ nhóm alias **dự kiến**, tên chuẩn, các cách gọi và bằng chứng.
Nhóm trùng thành viên mâu thuẫn được đánh dấu nghi vấn. Nhóm không ép gộp person
hay MAGI bank ID; canonical name phải có cách viết từ nguồn/registry hỗ trợ.
Tên registry là thông tin tham khảo; registry chưa có tên không chặn xử lý.
Khung registry vẫn được bootstrap từ bank trước scan, đề xuất tên mới nằm trong
artifact của run cho đến khi analyze xác nhận.

Analyze nhận câu đã sửa, tên gốc, offset trước/sau sửa và nhóm alias tham khảo.
Analyze xác nhận/bác tên người và suy luận chủ sở hữu, không trả patch hay sửa
nhóm. Chỉ từng mention đã được analyze xác nhận và nối với person ID mới được
ghi vào registry. Không ghi cả nhóm theo một mention đã xác nhận.

Export dùng câu đã sửa cho translation/typesetting, giữ `original_text` và
`name_corrections` trên câu có sửa; extract/normalize gốc không bị ghi đè.
Các mention giữ `raw_surface`, `original_start/end`, và `start/end` sau sửa.
`review.md` của scan/analyze/export ghi patch, bằng chứng, nhóm alias, nghi vấn,
đề xuất bị từ chối và quyết định xác nhận/chủ sở hữu từ analyze.

Kết quả scan lưu scenes và hash document. Analyze kiểm tra hash/coverage rồi
tái sử dụng scenes, không gọi chia scene lần nữa. Receipt request giữ cơ chế
resume và tối đa hai lần thử khi phản hồi sai hoặc bị cắt như trước.

Run mới dùng mặc định này. Manifest cũ dùng `gliner` vẫn giữ hành vi cũ; muốn đổi
phải tạo run mới hoặc vô hiệu hóa scan và các bước phụ thuộc bằng cơ chế chuẩn,
không chép scan cũ sang manifest mới.

Kiểm tra offline: `tests/test_v6_scene_names.py` kiểm tra lượt gọi chung, offset,
tên bịa/sửa OCR, câu không essential, chương rỗng, resume và tái sử dụng scene.
Chưa đánh giá chất lượng tìm tên bằng model thật trong thay đổi này.

`tests/test_v6_name_normalization.py` kiểm tra patch Unicode, nhiều vị trí có độ
dài thay đổi, bằng chứng chapter/registry, patch sai/chồng lấn, nhóm alias dự kiến,
registry chỉ lưu tên đã xác nhận, review và scan → analyze → export.

Xác minh sau triển khai chuẩn hóa tên: 39 test qua (9 normalization, 5 scene/name,
19 independent batches, 6 semantic contract); `git diff --check` qua. Test đều
offline, không tải/chạy model và không dùng Runpod. Trong môi trường Windows hiện
tại dùng `venv/Scripts/python.exe` với `PYTHONPATH=src` cho bộ kiểm tra có review
vì `.venv-hayai-local` không cài SciPy. Artifact scan theo schema cũ không có patch
sẽ không được tự bổ sung: tạo run mới để chạy schema/prompt mới.

Kết quả: 5 test luồng chung và 19 test independent batches đều qua. Bộ independent
edges qua 19/20; test `test_known_name_owner_and_anchor_cannot_be_changed` còn kỳ
vọng speaker anchor bất biến, trái cơ chế inference-first hiện có. Không thay
quy tắc merge/speaker trong thay đổi scan tên này.
