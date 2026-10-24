# Báo cáo refactor pipeline v2

Ngày: 2026-10-04. Bản trước refactor đã được push lên `master` của `vpmx555/manga-translation` tại commit `5d82f88`. Thay đổi refactor trong workspace chưa được commit/push.

## Luồng đã triển khai

`extract → normalize → scan → classify → link → export`

Không còn scene segmentation, model sửa thứ tự đọc hoặc các lượt LLM suy luận speaker/addressee cho toàn bộ chapter. Code cũ cho các lượt này và cấu hình reasoning đã được bỏ khỏi luồng chạy. MAGI giữ vai trò phát hiện panel, text, nhân vật, OCR, thứ tự đọc và association speaker nguồn.

| Bước | Hành vi | Artifact chính |
| --- | --- | --- |
| extract | MAGI trong worker riêng; gán stable ID từ bank, ghi transcript bằng ID; worker kết thúc trước khi chạy model tên | `01_extract/result.json`, `transcript.txt` |
| normalize | Chuẩn hóa text, giữ liên kết text/box nguồn, audit phần bị loại, giữ thứ tự đọc | `02_normalize/result.json` |
| scan | spaCy English small phát hiện PERSON; đối chiếu thêm tên đã có trong bank | `03_scan/result.json` |
| classify | Chỉ câu có tên mới: phân loại câu mục tiêu và từng mention bằng text, cùng hai câu trước/hai câu sau và speaker ID nguồn | `04_classify/result.json` |
| link | Ghép tên từ narration vào stable ID có sẵn; chọn bỏ qua, tự gán hoặc VLM theo số ID trong panel | `05_link/result.json` |
| export | Giữ nguồn/thứ tự; thêm tên, narrator, mention và addressee theo bằng chứng có sẵn | `06_export/result.json`, `dialogue.json` |

Phân loại text không nhận ảnh, bounding box, panel hoặc tail evidence. `mention_type` biểu diễn vai trò của tên; `addressee_type` biểu diễn người nghe, hai trường được tách riêng. Tên đã mapped bỏ qua LLM lẫn VLM. Các request Ollama dùng schema JSON và `think: false`, có kiểm tra coverage, ID hợp lệ, response bị cắt, context budget và cache kết quả hợp lệ.

Speaker của dialogue giữ association từ MAGI. Câu được xác định là narration có `voice_type: narrator`, effective `speaker_id: null`; ID MAGI gốc vẫn nằm trong `source_speaker_id`. Nhân vật được nhắc đến không tự trở thành speaker của lời kể. Addressee chỉ được gán khi có direct address rõ hoặc quy tắc vocative hẹp cho tên đã biết; các trường hợp khác giữ unspecified. Không phải mọi câu trong chapter được model phân loại.

## Điều kiện ghép tên

Scan và classify phải hoàn thành trước link. Chỉ mention mới có vai trò narration introduction/reference trong câu nghi narration mới đi vào nhánh này.

| Panel | Kết quả |
| --- | --- |
| Không có stable ID | Không gọi VLM, không gắn tên |
| Một stable ID và đúng một tên mới đủ điều kiện | Tự gán, không gọi VLM |
| Một stable ID và nhiều tên mới | Giữ chưa xác định, không gọi VLM |
| Từ hai stable ID khác nhau và có tên mới đủ điều kiện | VLM xác minh narration và chọn MAGI detection có stable ID |

Nhiều detection cùng ID chỉ tính là một ID. Pending không phải ứng viên có ID ổn định. VLM nhận panel có đánh dấu detection/ID, ưu tiên kiểm tra association speaker MAGI đáng ngờ trong narration, có thể trả unknown. Không tự nối hai nhân vật bằng tên hoặc coi các tên khác nhau là alias. Tên đã mapped được loại trước khi đếm tên mới.

## Cấu trúc

```text
run.py
configs/
src/manga_pipeline/
  cli/
  extraction/
  normalization/
  bank/
  names/
  providers/
  storage/
tests/
  bank/
  cli/
  names/
  normalization/
  providers/
  storage/
docs/
  news/
  archive/
scripts/experiments/
banks/<story-id>/
outputs/<story-id>/<chapter-id>/<run-id>/
```

Entry point là `run.py`; logic điều phối ở `manga_pipeline/pipeline.py`. Các thư mục trong `src` phân chia theo trách nhiệm. Tài liệu triển khai mới nằm trong `docs/news`; tài liệu mô tả pipeline/benchmark cũ được chuyển sang `docs/archive` với ghi chú lịch sử. Script thử nghiệm được tách khỏi `src` và suite test hiện tại. Output và bank được Git ignore.

## Bank và pending

Bank dùng chung giữa các chapter của cùng truyện tại `banks/<story-id>/`. ID, embedding và crop được giữ khi cập nhật tên. Name metadata có nguồn tên, giá trị auto gần nhất, lịch sử và operation receipt. Người dùng sửa trực tiếp `display_name` của ID; lần cập nhật auto sau bảo vệ tên sửa thủ công. Không có bước hỏi duyệt tên.

Pending chỉ match trong ba trang liên tiếp cùng chapter, tính từ trang xuất hiện đầu: trang 1 có thể match trang 2/3, không match trang 4. Pending hết cửa sổ hoặc thuộc chapter khác được đóng khỏi matching. Stable ID không chịu giới hạn này. Không gắn tên, đề xuất tên hay promote pending bằng tên.

Bank cũ ở `data/character_banks` được sao chép riêng khi bank mới chưa tồn tại, sau khi kiểm tra schema/model. Copy được khóa và công bố atomically từ staging directory; bank cũ và metadata backup được giữ. Đây không phải cơ chế nhập checkpoint chapter cũ.

## Resume

Mỗi run có manifest, snapshot config, artifact và hash. Bước đã completed được đọc lại và bỏ qua toàn bộ xử lý/model. Artifact đã commit bị mất hoặc bị sửa sẽ báo lỗi, không âm thầm chạy lại. Config của run giữ cố định; `resume --device` chỉ tác động extraction còn thiếu.

Scan commit từng câu, classify commit từng câu có tên, link commit từng panel. Kết quả unknown hợp lệ vẫn completed. Lỗi từng target được lưu, các target khác tiếp tục; step partial sẽ chặn downstream cho đến khi retry xong. Resume chỉ chạy target lỗi hoặc chưa commit.

Worker receipt phục hồi extraction sau khi worker hoàn thành nhưng manifest chưa ghi. Step receipt phục hồi commit giữa artifact và manifest. Link lưu quyết định trước khi cập nhật bank; journal cập nhật tên có operation key. Vì vậy ngắt sau khi ghi bank nhưng trước target commit không gọi lại VLM hoặc nhân đôi lịch sử. Khóa run ngăn hai tiến trình cùng ghi một run; khóa bank bảo vệ cập nhật dùng chung.

Chỉ resume run của pipeline v2, theo phạm vi đã chốt. Output/checkpoint run cũ không được nhập để tiếp tục chapter.

## Thiết bị và cách chạy

MAGI hỗ trợ `--device auto|cpu|cuda`: auto chọn CUDA khi PyTorch thấy CUDA, nếu không dùng CPU; chọn cuda rõ ràng mà không khả dụng sẽ báo lỗi. spaCy nhỏ chạy CPU; Ollama quản lý thiết bị của server riêng.

```powershell
.\venv\Scripts\python.exe run.py run "D:\Manga\Chapter 1" --story "One Piece" --chapter "chapter-001" --device auto
.\venv\Scripts\python.exe run.py resume --run-dir "outputs/one-piece/chapter-001/<run-id>"
```

Lệnh chạy riêng từng bước và cài dependency/model được ghi tại [usage.md](usage.md).

## Kết quả kiểm tra và giới hạn

- **46 test offline pass**, bao phủ pending, stable ID, bảo toàn nguồn/thứ tự, nhánh điều kiện VLM, tên đã biết, tên thủ công, cache/thinking flag, response bị cắt, lỗi cục bộ, step/target resume, ngắt giữa cập nhật bank và target commit, migration bank bị ngắt và schema/model không tương thích. Ba test bổ sung kiểm tra thanh tiến trình theo thứ tự, reuse/retry và bridge tiến độ MAGI; một test kiểm tra backfill file review không gọi lại pipeline model hoặc đổi artifact/config/bank.
- `compileall`, CLI help và `git diff --check` đã pass. Regression suite cuối chạy trên Python 3.12.0.
- Đã cài spaCy 3.8.16 và `en_core_web_sm` 3.8.0 trong venv. Smoke test nhận được “Roronoa Zoro” và “Monkey D. Luffy”; bỏ sót “Zoro” trong câu gọi “Zoro, help me!”. Tên đã biết được tìm thêm bằng dictionary lookup, nhưng tên mới ngắn hoặc romanized ngoài domain vẫn có thể bị bỏ sót/sai.
- Request Ollama thực tế với `gemma4:e4b-it-q4_K_M` nhận đúng schema, `think: false`, `done: true`, `done_reason: stop`. Câu “This is Roronoa Zoro, a pirate hunter.” trả `content_type: narration`, `mention_type: narration_reference`; 253 prompt tokens, 36 output tokens, khoảng **39,69 giây** tính cho lời gọi infer. Đây là một smoke test, không phải đánh giá độ chính xác hoặc benchmark toàn pipeline.
- Chưa chạy một chapter đầy đủ bằng pipeline mới, chưa kiểm tra inference VLM thực tế hoặc CUDA trên GPU NVIDIA. Máy hiện tại có Intel Iris Xe; không có xác nhận tốc độ CUDA thực tế. MAGI vẫn là bước nặng, và một request Ollama vẫn có thể chậm trên CPU; cải thiện chính là giảm số lượt model và tái dùng kết quả đã hoàn thành.

Không có cam kết mọi câu đều xác định được narrator/addressee, mọi tên riêng đều được phát hiện, hoặc mọi nhân vật đều được ghép tên. Khi thiếu bằng chứng, luồng giữ kết quả chưa xác định thay vì tạo ID hoặc tên trên pending.

## Bổ sung thanh tiến trình

CLI bật thanh tiến trình riêng cho từng bước, giữ các dòng kết quả lần lượt khi chạy toàn bộ. Trong extraction, worker báo tiến độ loading, detection/OCR theo batch và lưu output về parent; log MAGI vẫn được ghi riêng. Các bước còn lại đếm text/utterance/panel thực tế. Resume hiển thị bước/mục tái dùng và lỗi cục bộ. Có `--no-progress`; tiến độ không thay đổi config, checkpoint hoặc cache. Xem [progress.md](progress.md).

## Bổ sung file kiểm tra gọn

Mỗi bước có `review.md`, tổng quan run cũng có `review.md` liên kết các bước, ảnh trang và crop stable ID. Extract/export chỉ hiện câu–người nói/người nghe; normalize chỉ hiện text thay đổi/bị loại; scan/classify/link chỉ hiện tên, câu liên quan và kết luận. Bản đọc không chứa hash, UUID hoặc box arrays. Tạo tự động sau mỗi bước và khi tái dùng bước completed. Lệnh `review --run-dir` tạo lại từ artifact đã lưu, không chạy model và không thay checkpoint. Đã tạo file review cho run After School, We Do và kiểm tra hash artifact, manifest/config/bank không đổi. Xem [compact_reviews.md](compact_reviews.md).
