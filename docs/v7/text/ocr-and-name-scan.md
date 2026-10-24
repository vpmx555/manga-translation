# OCR, scene và chuẩn hóa tên

**Contract mặc định đã triển khai:** [scan mới](scan-redesign.md) mô tả:
tên model sửa là OCR hiện hành, replace-all tuần tự và validator dữ liệu tối thiểu.
Phần GLM-OCR bên dưới vẫn áp dụng. Các phần scan từ mục “Scan scene và tên trong
cùng lượt” là mô tả legacy, chỉ dùng khi `scan_policy=null`; không áp dụng các
validator span/coverage/retry đó cho `canonical-scan-v3`.

[← Chỉ mục](../README.md)

## GLM-OCR trong extract

Luồng mới dùng MAGI phát hiện vùng chữ, rồi GLM-OCR nhận dạng trên crop. Config
mặc định `ocr.backend=glm-ocr`, `ocr.batch_size=8`, `max_new_tokens=2048`.
Môi trường/model GLM được chuẩn bị riêng trước inference; không thay dependency
MAGI để chạy OCR. Batch size là số crop được xử lý theo adapter, khác với batch
utterance analyze và khác với concurrency request vLLM.

Giữ crop, raw OCR và source positions để đối chiếu. Lỗi recognition có thể do
text detector bỏ/méo vùng, crop quá sát, dấu nối xuống dòng hoặc model nhận sai;
đổi OCR model không khôi phục chữ nằm ngoài crop nguồn.

Pipeline không có bước LLM tự viết lại toàn bộ OCR chapter cho trôi chảy. Normalize
xử lý cấu trúc/group và các quy tắc noise; sửa tên ở scan là một lớp có phạm vi
hẹp, có evidence và vị trí nguồn. Những từ không phải tên không được tự sửa chỉ
vì suy đoán ngôn ngữ.

## Scan scene và tên trong cùng lượt

`ner.backend=llm-scene` dùng request chia scene để đồng thời đề xuất name spans,
nhóm alias và phép sửa tên. GLiNER không chạy thêm trong đường mặc định này.
LLM nhận text chapter và registry advisory nếu đã có, rồi trả dữ liệu cấu trúc.

Mỗi phép sửa xác định `utterance_id`, `start`, `end`, `original`, `replacement`,
`status`, `evidence` và references. Offsets là **ký tự Unicode, zero-based,
`[start,end)` trên text gốc**, không phải byte UTF-8 hay offset sau patch.

Ví dụ `sasa- mori - chan` có thể được đề xuất thành `sasamori-chan` khi có bằng
chứng tên/alias phù hợp. Validator kiểm tra đúng chuỗi nguồn, vị trí name span,
references hợp lệ và hỗ trợ cho replacement. Patch chồng lấn bị từ chối; code
áp dụng patch có kiểm tra, không dùng response để viết lại JSON nguồn.

Tên ngắn, tên đầy đủ, honorific và dạng có lỗi có thể được gom cùng nhóm nếu có
dấu hiệu cùng người. Tần suất xuất hiện đơn thuần không phải bằng chứng đủ để
sửa chính tả hoặc gộp. Canonical group cần có dạng được hỗ trợ từ nguồn/registry.

## Chưa có registry và quyền của analyze

Registry là tham khảo, có thể sai; không phải yêu cầu bắt buộc để scan chạy.
Khi chưa có registry, code vẫn xét evidence trong chapter để đề xuất nhóm/sửa
tên. Không tạo một person có tên chỉ để hợp thức hóa một patch chưa đủ bằng chứng.

Tên nghi vấn giữ nguyên nguồn và được đánh dấu `suspect`/review. Analyze có quyền
phân giải lại ý nghĩa/name ownership theo evidence của mình; analyze không làm
thêm một lượt sửa text OCR hay đổi offset patch. Nhóm alias ở scan cũng chưa tự
động chứng minh mọi MAGI ID nhắc tới tên đó là một người.

## Giới hạn request scan hiện tại

`llm.scene_num_predict=4096` có trong config, nhưng code `v6/scene.py` hiện dùng
reserve **8192** trong vòng tối đa ba attempt của scene path. Không diễn giải
việc sửa riêng key config này là chắc chắn đổi reserve thực thi. Chính sách
không retry của profile/relationship không áp dụng ngược cho scan.

Nếu scene vượt context, xử lý theo receipt/budget và chính sách code đã freeze.
Không giảm precision evidence, cắt mất utterance hoặc vá prompt trên pod giữa
run. Thay đổi thuật toán packing là source change và phải có batch mới.

## Cách review OCR/scan

Đối chiếu ảnh/crop với raw OCR trước, rồi xem text sau normalize/patch, rejected
patches và suspicions. Phân biệt sai nhận dạng với phép sửa tên sai; chỉ nhìn
text sau scan sẽ không xác định được lỗi đến từ OCR hay normalization.

Để chạy riêng OCR với output cũ, dùng lane diagnostic có sẵn sau khi xem `--help`
của `local_v6.py ocr-prepare` và xác định checkpoint chứa crop/detection cần thiết.
Không chạy cả pipeline rồi gọi đó là phép thử OCR độc lập. Khi yêu cầu test chỉ
OCR, không gọi analyze/translation và không tự thuê Runpod nếu chưa được yêu cầu.

Nguồn mã: `extraction/glm_ocr.py`, `extraction/hayai.py` (validation/backend
compatibility), `v6/scene.py`, `v6/name_normalization.py`.
