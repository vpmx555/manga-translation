# Pipeline v4: cài đặt, chạy, đọc kết quả và resume

Sau khi chạy, mở `<run-directory>/review.md`: tổng quan gọn có liên kết tới `review.md` của từng bước, ảnh trang và crop nhân vật. Các file này được tạo tự động sau mỗi bước và khi resume. Xem [cách dùng file review](compact_reviews.md). [Hướng dẫn đọc JSON chi tiết](read_and_check_results.md) dùng khi cần đối chiếu dữ liệu kỹ thuật.

Review tổng có cột `Run-time` cho từng bước và tổng thời gian đã ghi nhận. Đo thời gian thực thi bằng đồng hồ monotonic, cộng qua các lượt partial/retry/lỗi và Ctrl+C, không tính khoảng nghỉ giữa các lần chạy hoặc bước được dùng lại. Extraction được reanalyze tái dùng ghi 0 giây ở run mới. Run cũ thiếu timing hiển thị “—”; không suy ra thời gian từ created_at/finished_at. Khi tiến trình bị kill đột ngột hoặc máy tắt, phần thời gian chưa ghi checkpoint có thể thiếu. Chạy `review --run-dir <run>` để cập nhật bố cục review của run cũ mà không sửa JSON/receipt.

## Chuẩn bị

Chạy từ thư mục repository, dùng Python environment có MAGI/PyTorch và các dependency hiện có. Bước tên cần thêm:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-names.txt
.\venv\Scripts\python.exe -m spacy download en_core_web_sm
```

Run mới mặc định dùng GLiNER small v2.5, nhãn person name, threshold 0.85, CPU. Confidence NER được lưu trong ứng viên tên và hiển thị ở review scan. Lần scan đầu tải model vào `models/ner/`, các lần sau dùng cache. GLiNER được giải phóng sau scan để giảm bộ nhớ trước bước LLM. Lệnh tải spaCy model ở trên chỉ cần cho các run cũ hoặc cấu hình backend spaCy. Tên đã có trong bank còn được tìm bằng đối chiếu chuỗi. Xem [kết quả thử GLiNER](gliner_names.md).

Ollama phải có model cấu hình sẵn; mặc định `gemma4:e4b-it-q4_K_M`. Khởi động Ollama trước bước analyze. Run mới phân tích mọi câu essential, kể cả câu không có tên hoặc tên đã biết. Một lượt LLM cho mỗi batch ba target; text/context tối đa năm câu mỗi target và bộ nhớ tối đa mười lượt trước. Thinking luôn tắt; LLM thoại không nhận ảnh. GPU Ollama được quản lý ở server riêng với PyTorch. Xem [thiết kế đã chốt](dialogue_semantics_redesign.md).

## Chạy từ folder ảnh

```powershell
.\venv\Scripts\python.exe run.py run "D:\Manga\Chapter 1" --story "One Piece" --chapter "chapter-001" --device auto
```

CLI in đường dẫn run ngay sau khi tạo. Output mặc định là `outputs/<story-id>/<chapter-id>/<run-id>/`; bank dùng chung ở `banks/<story-id>/`. Có thể đổi bằng `--output-root`, `--bank-root`, `--config configs/pipeline.json`. Dùng `--no-visualizations` để bỏ ảnh kiểm tra MAGI.

`--device auto|cpu|cuda` áp dụng cho MAGI: auto chọn CUDA nếu PyTorch thấy CUDA, còn lại CPU; chọn cuda rõ ràng sẽ báo lỗi nếu không khả dụng. Không có lựa chọn scene/ordering reasoning. MAGI chạy trong worker riêng để giải phóng model trước bước tên.

## Chạy từng bước

Thanh tiến trình bật mặc định. Run mới hiển thị lần lượt `[1/6] extract` đến `[6/6] translate`; analyze hiển thị batch và phần đang chạy: text, sửa cặp, ghép tên/panel hoặc checkpoint. Bước hoàn tất khi resume hiển thị `reused`. Run v3 đã lưu giữ năm bước; run v2 giữ sáu bước theo luồng cũ. Dùng `--no-progress` để tắt.

```powershell
.\venv\Scripts\python.exe run.py extract "D:\Manga\Chapter 1" --story "One Piece" --chapter "chapter-001"
.\venv\Scripts\python.exe run.py normalize --run-dir "outputs/one-piece/chapter-001/<run-id>"
.\venv\Scripts\python.exe run.py scan --run-dir "outputs/one-piece/chapter-001/<run-id>"
.\venv\Scripts\python.exe run.py analyze --run-dir "outputs/one-piece/chapter-001/<run-id>"
.\venv\Scripts\python.exe run.py export --run-dir "outputs/one-piece/chapter-001/<run-id>"
.\venv\Scripts\python.exe run.py translate --run-dir "outputs/one-piece/chapter-001/<run-id>"
```

Thay `<run-id>` bằng run thực tế. Lệnh bước riêng yêu cầu các bước trước đã hoàn thành. Run mới lưu `01_extract`, `02_normalize`, `03_scan`, `04_analyze`, `05_export`, `06_translate`; mỗi bước pipeline có `result.json` và `review.md`. Dialogue nguồn ở `05_export/dialogue.json`, text ngoài essential ở `05_export/translation_only.json`. Các phiên bản dịch và hai review ở `translations/vi/<revision>/`. Lệnh `translate` riêng ghi vào thư mục dịch, không thay manifest hay bước export nguồn. `classify`/`link` chỉ dành cho run v2 đã lưu. Xem [cấu hình dịch và lọc normalize](vietnamese_translation_usage.md).

Chạy tới một bước bằng `run ... --until scan`, `reanalyze ... --until analyze` hoặc `resume ... --until analyze`.

## Resume và lỗi

Để áp dụng phân tích mới lên chapter đã chạy MAGI xong, tạo run mới:

```powershell
.\venv\Scripts\python.exe run.py reanalyze --run-dir "outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189"
```

Lệnh in đường dẫn run mới và chạy các bước sau extraction. Không chạy lại MAGI; run cũ giữ nguyên. Bank dùng chung nên các tên/alias mới được ghi vào cùng bank. Resume run cũ tiếp tục dùng chính sách cũ; không sửa config để chuyển đổi.

Thêm `--until scan` để chỉ thử bộ nhận diện tên, không chạy LLM hoặc ghi tên vào bank. Muốn tiếp tục từ run scan này, dùng `resume --run-dir <run-mới>`.

```powershell
.\venv\Scripts\python.exe run.py resume --run-dir "outputs/one-piece/chapter-001/<run-id>"
.\venv\Scripts\python.exe run.py resume --run-dir "outputs/one-piece/chapter-001/<run-id>" --until analyze
```

- Tiếp tục run v2/v3/v4 theo phiên bản luồng và config đã lưu. Không chuyển run đang dở sang policy mới bằng cách sửa manifest; dùng reanalyze. Run mới mặc định chạy tới translate; dùng `--until export` để dừng trước dịch.
- Step hoàn thành được xác minh artifact rồi dùng lại; không chạy lại xử lý/model.
- Analyze lưu text theo target, quyết định panel, thao tác bank và batch/bộ nhớ. Resume bỏ qua phần đã hoàn tất; không gọi lại text/VLM vì còn một thao tác ghi dở.
- Chuẩn hóa các cặp cố định bằng code, có cảnh báo. Lỗi còn lại được sửa tối đa một lượt cho đúng target/cặp; không thay các kết luận độc lập đã hợp lệ. Nếu vẫn chưa xác định được thì fallback, ghi cảnh báo và tiếp tục chương. Fallback/unresolved đã hoàn tất không tự retry khi resume.
- Quyết định panel được lưu trước khi ghi bank; operation receipt và mapping được ghi cùng giao dịch. Nếu bị ngắt giữa ghi bank và checkpoint, resume không gọi VLM lại hoặc ghi trùng alias/lịch sử.
- Khi chưa nhận/lưu được kết quả model trước khi bị ngắt, phần đó có thể phải gọi lại. Lỗi I/O/thiết lập vẫn dừng bước để tránh thừa nhận kết quả chưa được ghi.
- Translate giữ checkpoint theo từng câu và từng phong cách. Lỗi model/timeout để partial; lần chạy lại chỉ gọi phần thiếu hoặc lỗi, không thay bằng tiếng Anh để đánh dấu hoàn tất. Câu sửa tay được khóa riêng theo phong cách. Đây là policy retry của dịch; fallback trong analyze vẫn được giữ như trên.
- Ghi atomically và receipt cho step giúp khôi phục khi bị ngắt giữa artifact/manifest. Không tự xem file ghi dở là hoàn thành.
- Không sửa config trong manifest. Đổi model/schema/config cho lượt mới; không âm thầm chạy lại output hoàn thành. Ảnh nguồn thay đổi hoặc artifact bị sửa/hỏng được báo lỗi. `resume --device cpu` chỉ thay thiết bị của extraction chưa xong.

Exit code: 0 hoàn thành đến bước yêu cầu; 2 partial có mục cần retry; 1 lỗi setup/input/step; 130 bị ngắt bằng Ctrl+C. Log MAGI ở `logs/magi.log`, failure từng target nằm trong artifact của step.

## Quy tắc tên và pending

- Pending chỉ match trong ba trang liên tiếp cùng chapter, tính từ trang bắt đầu: trang 1 có thể match trang 3, không match trang 4 khi vẫn pending. Đây không phải chia chapter thành các block cố định ba trang.
- Stable ID vẫn có thể nhận diện ở mọi trang/chapter. Pending không được gắn tên, đề xuất tên hay promote bằng tên.
- Bốn vai trò tên: introduction, direct_address, reference, unknown. Tên đã có mapping không cần VLM; không tự chuyển sang ID khác. Câu chứa tên vẫn được phân tích speaker/listener.
- Không có tên được scan thì không mở panel. Chỉ introduction đã được text LLM xác nhận mới đi vào nhánh panel, kể cả dialogue hoặc câu có speaker. Panel có 0 stable ID: unresolved; 1 ID + 1 tên mới: tự nối; 1 ID + nhiều tên mới: unresolved; từ 2 ID và tên mới: VLM chọn trong ID đã có. Pending luôn không đủ điều kiện.
- Tên gọi trực tiếp chỉ nối với người nghe stable ID mà LLM đã kết luận nhất quán. Khi single đã có người nghe, ID tên gọi phải bằng người nghe. Không tự chọn người còn lại chỉ vì có hai ứng viên.
- Tên giới thiệu đã ghép thành công có ưu tiên cao hơn tên gọi trực tiếp: có thể nâng tên hiển thị và giữ tên cũ thành alias. Hai tên giới thiệu cùng ưu tiên giữ tên hiện tại, thêm alias có bằng chứng cùng ID và cảnh báo. Tên thủ công được bảo vệ. Không gộp theo giống chữ/chung họ.
- Tên vừa ghép được đưa vào batch kế tiếp; không chờ hết chương. Không suy luận lại toàn batch hiện tại để sử dụng tên vừa ghép.
- Narrator và nhân vật được nhắc đến là hai thông tin khác nhau. Export giữ `source_speaker_id`; narration không ép ID nhân vật trong panel thành người nói lời kể.
- Run mới lưu `speaker_policy: individual-v1`: speaker chỉ là ID nhân vật trong tập ứng viên, `others` hoặc `narrator`. `groups` tạm ngừng trong enum và prompt; không tự gán câu tập thể cho một ID nhân vật. Run v3 cũ thiếu policy vẫn giữ hợp đồng cũ, kể cả đang dở; output cũ có `groups` vẫn đọc/hiển thị được. Dùng `reanalyze` để tạo run theo hợp đồng mới và tái dùng extraction, không sửa snapshot/plan/kết quả cũ.
- Narrator yêu cầu `content_type: narration`. Dialogue/thought/unknown với narrator gây lỗi `narrator_requires_narration` và chỉ mở speaker cho repair nếu các trường khác hợp lệ. Tối đa một lượt repair hiện có; nếu speaker vẫn sai hoặc model không trả lời thì dùng `others`, lưu cảnh báo và hoàn tất target. Narration vẫn cho phép ID nhân vật hoặc `others`, không bắt buộc narrator hay public_audience.
- Addressee type vẫn là single, group, audience, self, unknown. Group người nghe chỉ cần giữ thành viên đã biết, kể cả một ID, hoặc `[unknown]`; không ép đủ nhóm. Thought hướng self. Chi tiết phiên bản và giới hạn: [speaker_policy_individual_v1.md](speaker_policy_individual_v1.md).
- Code giữ type LLM đã chọn và chuẩn hóa audience → `[public_audience]`, unknown → `[unknown]`, self → `[speaker_id]` khi biết ID hoặc `[self]`. Thông tin trước/sau nằm trong cảnh báo. Các special token không vào bank và không tạo danh tính liên tục.

## Bank và sửa tên

Sửa `display_name` của đúng ID trong `banks/<story-id>/metadata.json`. Metadata lưu `name_source`, `last_auto_name`, history và operation receipts. Khi tên khác giá trị auto gần nhất hoặc là tên có sẵn không rõ nguồn, cập nhật tự động sẽ bảo vệ tên thủ công. Không đổi ID/embedding/crop bằng cách sửa tên.

Bank ở `data/character_banks/<story-id>` được sao chép khi bank mới chưa tồn tại, có kiểm tra model/schema và giữ metadata backup; directory cũ được bảo toàn. Bản sao được tạo trong thư mục tạm rồi mới công bố bằng rename, tránh bank mới chỉ chứa một phần dữ liệu khi bị ngắt. Đây là migration bank, không phải resume chapter cũ.

Snapshot bank theo từng phase giúp giải thích kết quả run. Nếu sửa tên sau khi export hoàn thành, artifact export cũ vẫn được giữ nguyên; lượt mới sẽ dùng tên đã sửa.

## Kiểm tra

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\venv\Scripts\python.exe run.py --help
```

Suite offline kiểm tra cả policy/resume v2 và luồng analyze v3, kiểm tra cặp, bộ nhớ tên, ưu tiên tên, ngắt giữa text/VLM/ghi bank và các thanh tiến trình. Test cấu trúc không chứng minh độ đúng ngữ nghĩa của model; xem [probe kết hợp](joint_semantic_probe_20261004.md).

## Đọc kết quả nhanh

1. Mở `review.md` ở gốc run để xem trạng thái và đi tới từng bước/ảnh MAGI.
2. `03_scan/review.md`: tên tìm được và confidence NER; đây chưa phải kết luận tên–ID.
3. `04_analyze/review.md`: text, loại câu, speaker, loại/ID người nghe, ứng viên ID/nguồn, tên/vai trò/ID và cảnh báo. Bảng liên kết tên ghi nguồn/lý do unresolved. Chỉ có một cột pool ID để tránh lặp danh sách ứng viên nói/nghe.
4. `05_export/review.md` và `dialogue.json`: kết quả cuối. `mentions[].name_target_id` là kết luận sau ghép; `model_name_target_id` giữ gợi ý text ban đầu, `referenced_id` đối chiếu bank ở thời điểm export.
5. `06_translate/review.md`: liên kết hai bản dịch. Nếu dùng lệnh translate riêng, mở `translations/vi/latest.json` để tìm revision và hai file `review_natural.md`, `review_localized.md`. Speaker/listener giữ từ export; bản dịch không cập nhật hồ sơ hoặc đồ thị nhân vật.

Chi tiết phục vụ resume nằm trong plans/targets/attempts/cache; không cần đọc chúng để review câu thoại. Run v2 vẫn dùng `04_classify`, `05_link`, `06_export` như trước.

Thử toàn chương trên bản sao bank riêng:

```powershell
.\venv\Scripts\python.exe -B scripts/diagnostics/run_isolated_analysis.py --run-dir "outputs/<story>/<chapter>/<run-id>" --output-dir "outputs/diagnostics/<new-test>"
```

Lệnh tái sử dụng MAGI, sao chép bank vào thư mục thử và tạo run v3. Nếu bị ngắt, chạy lại cùng lệnh với `--resume`. Report lưu số lượt gọi, think/images, các case kiểm tra và hash xác nhận source run/bank còn nguyên.
