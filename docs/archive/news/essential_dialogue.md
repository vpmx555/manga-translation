# Essential-only dialogue analysis

Tài liệu lịch sử cho policy essential-v2 và run pipeline v2. Run mới mặc định dùng essential-v3 với năm bước; xem [hướng dẫn hiện tại](usage.md) và [thiết kế analyze](dialogue_semantics_redesign.md). Các mô tả sáu bước dưới đây vẫn áp dụng khi resume run v2.

Run mới mặc định dùng `dialogue_analysis: essential-v2`. Sáu bước giữ nguyên: extract → normalize → scan → classify → link → export. Run đang dở có `essential-v1` tiếp tục dùng hợp đồng cũ, kể cả prompt/schema và kế hoạch batch đã lưu. Run v2 không có cấu hình này vẫn resume bằng nhánh legacy. Muốn dùng nhãn mới cho chương đã chạy, tạo run `reanalyze` mới; không đổi config trong manifest cũ.

## Dữ liệu vào mô hình

Chỉ OCR có `is_essential_text: true` vào normalization phân tích, NER và LLM. Cờ false hoặc chưa biết được lưu riêng trong `translation_only`, với speaker null và listener rỗng. Export còn ghi `06_export/translation_only.json`. Raw extraction vẫn đầy đủ để giữ provenance và thứ tự cho dịch sau này. Không thêm bước loại UI/comment/SFX: essential có thể sai, nhưng vẫn được phân tích; chưa gán được speaker ID dùng `others`, chưa rõ người nghe dùng `unknown`.

Run mới dùng GLiNER small v2.5 theo mục cấu hình ner; đối chiếu tên/alias đã có trong bank bổ sung ứng viên. Run cũ không có ner vẫn dùng spaCy theo spacy_model. Xem [thử nghiệm nhận diện tên](gliner_names.md).

## Một lượt phân tích tích hợp

Classify xử lý mọi câu essential bằng text với `think:false`, trả về content_type, speaker_id, addressee_ids, addressee_type, mention_type và bằng chứng ngắn. Không gửi hình ảnh, geometry, panel, tail hoặc thông tin nonessential. Model kết luận type và IDs trong cùng response; code kiểm tra tính nhất quán, không âm thầm sửa quyết định hoặc tự chọn ứng viên còn lại. Mỗi câu đã classify thành công có speaker và danh sách người nghe không rỗng:

| Loại | Speaker | Listener |
| --- | --- | --- |
| Dialogue / unknown | Stable ID hoặc `others` | Theo loại người nghe bên dưới |
| Narration | Stable ID người kể nếu biết, nếu không `narrator` | `audience` + `["public_audience"]` |
| Thought | Stable ID hoặc `others` | `self` + `[speaker_id]`, hoặc `["self"]` nếu speaker là `others` |

| Addressee type | IDs | Ý nghĩa |
| --- | --- | --- |
| `single` | Một stable ID hoặc `["unknown"]` | Một người; có thể chưa biết danh tính |
| `group` | Nhiều stable IDs, IDs kèm `"unknown"`, hoặc `["unknown"]` | Nhiều người trong truyện; chỉ giữ thành viên có căn cứ |
| `audience` | `["public_audience"]` | Độc giả/người xem ngoài truyện |
| `self` | `[speaker_id]` hoặc `["self"]` | Chính người nói |
| `unknown` | `["unknown"]` | Chưa rõ loại người nghe |

`group` với duy nhất một ID đã biết cần kèm `unknown` cho phần thành viên chưa xác định; `[3]` riêng lẻ phải là `single` hoặc `self`, tùy người nói. Đám đông trong truyện là `group`, không phải `audience`. Không lấy toàn bộ tập ứng viên làm người nghe. Hai ứng viên `[1, 3]` và speaker `3` không tự chứng minh listener là `1`. `others` không đại diện cho một người cố định. Nhãn `unspecified`, `individual`, `not_applicable` và sentinel viết hoa chỉ còn phục vụ các run cũ.

Các ID đặc biệt không tạo nhân vật trong bank. MAGI là hint có thể sửa; export giữ source_speaker_id để đối chiếu. Tên chỉ được nhắc đến không tự trở thành người nghe. Narration không được xác định chỉ vì các câu tạo thành đoạn liên tục, dùng ngôi thứ nhất hoặc thì quá khứ. Kể lại sự kiện cho người khác trong cuộc đối thoại vẫn là dialogue. Gán public_audience cho narration là quy ước của pipeline.

## Context và bộ nhớ

Mỗi target có tối đa 5 câu essential: 2 trước + target + 2 sau. Ba target trong một lô dùng chung text context đã khử trùng lặp. ID câu/tên gửi model được thay bằng mã ngắn theo lô rồi đổi lại thành ID nguồn trước validate/commit; bằng chứng giới hạn 120 ký tự. Pool stable IDs lấy từ MAGI/context, tên/alias đã mapped trong context, và tối đa 10 câu essential trước. Bộ nhớ thêm speaker/listener đã được LLM trả về ở các lô trước, loại các ID đặc biệt và pending. Cập nhật bộ nhớ giữa các lô; trong lô, các câu có thể dựa vào text chung nhưng không có kết quả đã commit của nhau.

Không có scene. Bộ nhớ chỉ mang ID, tên/alias, nguồn và vị trí lần gặp gần nhất, không chèn toàn bộ 10 câu lịch sử. Tên đã được LLM nối ở các lô trước được đưa vào inferred_aliases trong cửa sổ 10 câu; đây là bằng chứng suy luận trước bước ghi bank, không tự tạo mapping bank. Cache HTTP-model lưu response theo digest; target chỉ commit sau validate đầy đủ. Bộ nhớ hội thoại là cơ chế bổ sung bằng chứng riêng, không đảm bảo thời gian mỗi request giảm.

## Kết luận tên và aliases

LLM có thể nối tên mới của lời gọi trực tiếp với listener ID thuộc pool hợp lệ. Đây là kết luận trong cùng lượt classify, không gọi thêm model. Link tự ghi tên khi ID chưa có tên; nếu đã có tên thì chỉ thêm alias. Tên thủ công, ID, embedding và crop giữ nguyên. Ví dụ Madol Arase đã là ID 3, model kết luận Madol-san cũng là ID 3 thì thêm alias Madol-san. Không tự merge hai người chỉ vì chung họ Arase.

Tên đã mapped sang ID khác hoặc nhiều ID không được tự chuyển. Mọi conclusion được kiểm tra coverage/role/ID trước commit. Bank kiểm tra xung đột lại dưới lock. Tên narration chưa mapped vẫn theo nhánh panel đã có: không ID thì bỏ qua; 1 ID + 1 tên tự gán; nhiều ID dùng VLM. Pending không được gắn tên dưới bất kỳ nhánh nào.

## Resume và review

`04_classify/plans/batch-*.json` lưu input và pool/bộ nhớ bất biến trước inference. Coverage response được kiểm tra theo lô, rồi từng target validate đầy đủ và commit ngay. Nếu một câu sai ID/role, các câu hợp lệ được giữ; chỉ câu lỗi được retry kèm lỗi cụ thể, theo cấu hình retries. Nếu vẫn lỗi, lô sau vẫn được xử lý; resume chỉ gọi lại target chưa hoàn thành với context/pool đã lưu. Kết quả đã hoàn thành và input của các lô sau không đổi theo kết quả retry mới. Điều này giữ resume nhất quán, dù có thể giữ pool ít bằng chứng hơn sau lỗi.

Link có operation journal idempotent. Review classify/export hiển thị mỗi câu, các ứng viên nói/nghe với nguồn, kết quả chọn, addressee type, vai trò tên và bằng chứng; không có confidence speaker/listener. Tổng quan đếm cả người nghe chưa rõ và thành viên nhóm còn thiếu ID. Review không phải nơi chỉnh bank. Confidence NER vẫn xuất hiện ở scan.

```powershell
# Dùng lại MAGI của run cũ; tạo run mới và chạy downstream.
.\venv\Scripts\python.exe run.py reanalyze --run-dir "outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189"

# Tiếp tục chính run mới nếu bị ngắt.
.\venv\Scripts\python.exe run.py resume --run-dir "<new-run-directory>"
```

Reanalyze xác minh artifact extraction, sao chép visualizations và dùng cùng bank. Không nhập checkpoint analysis cũ, không sửa run cũ hoặc chạy MAGI lại. Tên mới được ghi vào bank dùng chung khi nhánh link chạy.

## Kiểm tra triển khai

- 75 test offline kiểm tra hồi quy cũ, essential gate, text-only payload, mã ID ngắn, memory/alias trong cửa sổ 10 câu, bộ nhãn mới, nhóm chưa đủ ID, narrator/người kể có ID, thought/self, tên/alias trong bank, retry riêng câu lỗi, interrupt/resume của cả essential-v1 và essential-v2, reanalyze giữ nguyên run cũ, schema kết hợp và cache sai ngữ nghĩa.
- Smoke lịch sử của essential-v1: 3/3 câu essential quanh Madol-san hoàn thành, khoảng 76.4 giây; type vẫn unspecified. Không dùng kết quả này để đánh giá prompt essential-v2.
- Smoke essential-v2 với Ollama `gemma4:e4b-it-q4_K_M`: 3/3 câu hoàn thành, không có target lỗi, 2 request đều `think:false` và 0 ảnh, khoảng 168.82 giây gồm tải model/NER và cleanup. Cả ba câu có type `single`; speaker/listener là 3→1, 1→3, 1→3. Đây không phải phép đo tốc độ so sánh hoặc độ chính xác trên toàn chương.
- Trong mẫu mới, `Madol-san` vẫn có mention_type `unknown` và name_target_id null dù listener được chọn là ID 3. Không suy ra mapping tên bằng code để thay quyết định của LLM. Báo cáo được lưu ở [outputs/diagnostics/dialogue-labels-v2-smoke.json](../../outputs/diagnostics/dialogue-labels-v2-smoke.json).
- Lệnh smoke dùng bank/checkpoint tạm, không ghi tên vào bank truyện; `--output` lưu báo cáo model/policy, request think/images, kết quả và thời gian. Kiểm thử schema không chứng minh độ đúng của model trên hội thoại nhóm. Chỉ tính độ đúng khi có nhãn tham chiếu đã kiểm tra.

```powershell
.\venv\Scripts\python.exe -B scripts/diagnostics/smoke_dialogue.py --run-dir "outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189" --config configs/pipeline.json --output outputs/diagnostics/dialogue-labels-v2-smoke.json
```

Xem [thiết kế bộ nhãn](dialogue_labels_plan.md) để đối chiếu các quyết định và giới hạn.

Xem [sửa validation/cache và resume 3 target lỗi](dialogue_labels_validation_fix.md). essential-v2 chỉ lưu model cache khi toàn bộ batch hợp lệ về ngữ nghĩa; target tốt trong batch có lỗi vẫn được commit riêng. Schema ràng buộc tổ hợp loại câu, speaker và người nghe; feedback chỉ rõ target lỗi. Đây là kiểm tra nhất quán, không phải bảo đảm độ đúng của mọi kết luận LLM.
