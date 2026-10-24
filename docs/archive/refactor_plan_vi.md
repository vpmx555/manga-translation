# Thiết kế refactor pipeline — bản để xác nhận

## Mục tiêu

Refactor cấu trúc và đồng thời thay luồng xử lý để giảm số request model trên máy i7-1165G7, RAM khoảng 16 GB, Intel Iris Xe. Thiết kế lại CLI/schema. Resume là bắt buộc và chỉ hỗ trợ các lượt chạy của pipeline mới, theo Q23:A. Không tiếp nhận checkpoint/output/cache của lượt chạy pipeline cũ để tiếp tục chapter.

Không chạy phân scene ở bất kỳ chế độ nào. Không dùng model để sửa thứ tự đọc. Không có lượt LLM suy luận speaker/addressee cho toàn bộ utterance.

## Luồng mới

1. **Extract:** chạy MAGI, character bank và gán ID; lưu raw JSON, transcript và thông tin detection/panel/tail. Thứ tự nguồn được giữ xuyên pipeline.
2. **Normalize:** chuẩn hóa text, phân loại nội dung bằng các quy tắc nhẹ, giữ nguồn và audit các text bị loại; không gán scene.
3. **Scan:** spaCy `en_core_web_sm` tìm tên trong OCR tiếng Anh, gồm tên Nhật viết bằng chữ Latinh. Đồng thời tra tên đã có trong bank. Độ bao phủ tên hư cấu/OCR lỗi cần kiểm tra trên sample thực tế.
4. **Classify:** chỉ với tên chưa có mapping, cung cấp câu chứa tên cùng hai câu trước và hai câu sau, kèm speaker ID nguồn từ MAGI. LLM phân loại loại lời của câu mục tiêu và vai trò của từng mention. Không yêu cầu xác định cùng speaker cho cả năm câu.
5. **Link:** chỉ xử lý tên chưa được ghép trong trường hợp nghi narration, sau khi đã chạy scan và classify. Đếm các character ID ổn định khác nhau trong panel: không có ID thì bỏ qua mapping; một ID và đúng một tên mới đủ điều kiện thì tự gán; một ID nhưng nhiều tên mới thì giữ chưa xác định; từ hai ID trở lên mới gọi VLM để xác minh narration và chọn detection/ID phù hợp. Không tạo mô tả hình ảnh cho mọi panel.
6. **Export:** xuất dialogue cuối và báo cáo các mục chưa rõ/lỗi; giữ nguyên thứ tự đọc. Cập nhật các tên đã ghép vào bank theo chính sách dưới đây.

Tên đã có mapping ID: tra bank và **bỏ qua cả classify LLM lẫn link VLM** cho mention đó. Nếu một câu còn tên khác chưa biết, vẫn xử lý tên chưa biết; không suy luận lại mapping tên đã biết.

Việc tra tên đã biết không tự nối lại detection/pending hoặc đổi speaker MAGI. Không gộp nhân vật bằng tên.

### Kiểm tra panel trước khi gọi VLM

- Thực hiện sau các bước tìm tên và phân loại câu/mention; không dùng số ID để bỏ qua các bước trước đó đối với tên chưa có mapping.
- Đếm số **ID ổn định khác nhau**, không đếm số crop hoặc detection. Nhiều detection cùng ID vẫn là một nhân vật.
- Pending không được tính là ứng viên có ID ổn định.
- Panel chỉ có một ID ổn định và đúng một tên mới khác nhau đủ điều kiện: bỏ VLM và tự gán tên cho ID đó; vẫn tuân thủ bảo vệ tên sửa thủ công và không gắn tên cho pending. Các lần nhắc lặp lại cùng một tên không được tính thành nhiều tên mới.
- Panel có từ hai ID ổn định: nhánh narration đủ điều kiện mới gọi VLM, ưu tiên association MAGI đáng ngờ như đã thống nhất.
- Panel không có ID ổn định: bỏ VLM, giữ tên chưa có mapping và không ghi bank. Các bước scan/classify trước đó vẫn chạy đối với tên chưa biết.
- Panel chỉ có một ID nhưng có nhiều tên mới khác nhau đủ điều kiện: giữ chưa xác định, không tự gán tất cả vào một ID và không gọi VLM. Tên đã có mapping được loại khỏi tập tên cần gán trước khi kiểm tra.

## Speaker, narrator và addressee

- Speaker của dialogue dùng kết quả MAGI; không có lượt model sửa speaker toàn chapter.
- Loại lời có thể là dialogue, thought, narration hoặc unknown. Quy tắc nhẹ chỉ cung cấp bằng chứng sơ bộ, không coi mọi text thiếu tail là narration.
- Câu được xác định là narration biểu diễn giọng kể riêng, không coi nhân vật được nhắc đến trong panel là speaker của lời kể. Giữ association/ID nguồn để đối chiếu; narrator chưa rõ không được tạo thành một nhân vật mới trong bank.
- `mention_type` thuộc từng lần xuất hiện của tên: gọi trực tiếp, tự giới thiệu, nhắc người thứ ba, giới thiệu bằng lời kể hoặc chưa rõ.
- `addressee_type` mô tả người nghe, tách khỏi mention. Chỉ gán ID người nghe khi có bằng chứng/quy tắc rõ; các câu khác giữ unspecified/unknown. Narration có addressee not_applicable.
- Không dùng người nói tiếp theo làm quy tắc mặc định xác định người nghe.
- Không phải mọi câu sẽ được phân loại narration hoặc có người nghe; đây là giới hạn đã chọn để giảm inference.

## Character bank và pending

Bank dùng chung theo truyện. ID, embedding và crop của nhân vật đã hoàn chỉnh không bị thay đổi bởi bước gắn tên. Tên không được dùng làm lý do merge/recluster.

**Thay đổi cuối cùng về pending:**

- Cửa sổ pending là **ba trang liên tiếp trong cùng chapter**, tính theo thứ tự ảnh: pending bắt đầu trang 1 có thể match trang 2 hoặc trang 3, không match trang 4 khi vẫn còn pending.
- Nhân vật đã có character ID ổn định vẫn được bank nhận diện ở các trang/chapter khác, theo Q19:A.
- Điều kiện promote bằng các quan sát MAGI/embedding vẫn thuộc Stage 1. Không thêm điều kiện promote bằng tên.
- Bước tên chạy sau khi output MAGI và ID transcript đã được hình thành. Chỉ character ID ổn định của Stage 1 là đối tượng có thể nhận tên.
- **Không gắn tên cho pending dưới bất kỳ hình thức nào:** không tên tạm, không đề xuất tên cho pending, không promote pending bằng tên.
- Pending hết cửa sổ bị đóng khỏi tập matching. Giữ dữ liệu để audit; không tự đưa pending đã đóng trở lại matching. Không xóa dữ liệu bank cũ để triển khai giới hạn này.
- Detection chưa có ID ổn định không đủ điều kiện nhận tên. Nếu không có detection phù hợp đã có ID, bỏ qua/abstain; không ép tên vào ID khác trong panel.

Giới hạn ba trang áp dụng cho matching pending, không phải chia chapter thành các nhóm cố định trang 1–3, 4–6. Trang bắt đầu pending quyết định cửa sổ của nó.

## Ghi tên tự động và sửa thủ công

- Tự ghi mapping ID–tên vào bank khi có kết quả hợp lệ; không hỏi duyệt.
- Ưu tiên kiểm tra ID MAGI có association đáng ngờ ở lời kể; association này là gợi ý, không tự chứng minh tên–ID.
- Chỉ dùng ứng viên detection do MAGI cung cấp trong panel; không tạo ID hoặc suy diễn nhân vật ngoài tập ứng viên để ghi bank.
- Phân biệt tên do model ghi và tên sửa thủ công. Model được phép cập nhật tên tự động nhưng không ghi đè tên đã sửa thủ công.
- Metadata lưu nguồn tên và giá trị tự động gần nhất, để nhận biết khi người dùng chỉnh tên trực tiếp trong bank. Tên hiện có nhưng không rõ nguồn được bảo vệ khi migrate.
- Lưu lịch sử đổi tên/bằng chứng. Không tự coi mọi tên cũ, tên viết tắt hoặc tên giống nhau là alias; không merge hai ID dựa vào tên.

## Model và tài nguyên

- Giữ hỗ trợ chọn thiết bị MAGI đã có trong code hiện tại: `--device auto|cpu|cuda` cho lệnh `run` và `extract`.
- `auto`: dùng CUDA khi PyTorch xác nhận khả dụng, còn lại dùng CPU. `cuda`: kiểm tra khả dụng trước khi chạy, báo rõ nếu môi trường không đáp ứng; không âm thầm chuyển sang CPU khi người dùng đã chọn CUDA. Lưu thiết bị đã chọn vào manifest.
- Resume không chạy lại extraction đã hoàn thành chỉ vì máy hiện tại hoặc thiết bị khả dụng khác; thiết bị áp dụng cho phần xử lý còn thiếu. Nếu output không tương thích vì model/config/schema thực sự thay đổi thì xử lý theo contract resume.
- Model tìm tên: spaCy English small, theo lựa chọn Q14:B.
- spaCy English small mặc định chạy CPU trong phiên bản đầu. `--device` của MAGI không mặc nhiên chuyển spaCy sang GPU.
- LLM/VLM: đề xuất tiếp tục dùng model Ollama đã cài `gemma4:e4b-it-q4_K_M`, cấu hình thay được; thinking tắt, output có schema và ngắn.
- Ollama quản lý GPU ở phía server, độc lập với thiết bị PyTorch của MAGI. Hướng dẫn thiết lập/kiểm tra GPU được ghi riêng; không mô tả `--device cuda` như một công tắc bắt toàn bộ provider sử dụng CUDA. Tham khảo [Ollama hardware support](https://docs.ollama.com/gpu).
- Chạy các model theo giai đoạn, tránh giữ MAGI và model sinh văn bản cùng hoạt động nếu không cần.
- Gom các target cùng panel khi có thể; cache request hợp lệ và deduplicate các target lặp. Không dùng cache của prompt/schema cũ.
- Không cam kết latency/accuracy trước khi có pilot thực tế. Không tự tải thêm một LLM/VLM khác.

## Cấu trúc đề xuất

```text
run.py
src/manga_pipeline/
  cli/
  extraction/
  bank/
  normalization/
  names/
  providers/
  storage/
  pipeline.py
tests/
configs/
banks/<story-id>/
outputs/<story-id>/<chapter-id>/<run-id>/
docs/news/
```

- `extraction/`: MAGI adapter, detection và export nguồn.
- `bank/`: identity, embedding/crop, pending và cập nhật tên.
- `normalization/`: schema/validation nội dung và normalization.
- `names/`: scan, cửa sổ context, mention classification, narration-name linking.
- `providers/`: spaCy/Ollama adapter, giới hạn request và validation response.
- `storage/`: JSON atomic, manifest, cache và resume.
- `cli/` và `pipeline.py`: orchestration; không nhét thuật toán inference vào CLI.

Các module scene/order reasoning, command và benchmark chỉ dành cho luồng bị bỏ được gỡ khỏi code hoạt động. Giữ tài liệu lịch sử và output cũ để đối chiếu. Test được tổ chức lại theo luồng mới; tiện ích/dataset experiment nằm riêng khỏi entry point chính.

Bank cũ được tái sử dụng qua migration có kiểm tra và bản sao dự phòng, giữ ID/crop/embedding. Không silently tạo một bank trống khi người dùng muốn tiếp tục truyện đã có bank.

## CLI và output đề xuất

Entry point ngắn: `python run.py`.

- `run <image-folder> --story ... --chapter ...`: chạy toàn luồng.
- `resume --run-dir ...`: tiếp tục lượt chạy pipeline mới; bỏ qua các step/target đã hoàn thành.
- `extract <image-folder> --story ... --chapter ...`: tạo lượt chạy và extraction.
- `normalize --run-dir ...`
- `scan --run-dir ...`
- `classify --run-dir ...`
- `link --run-dir ...`
- `export --run-dir ...`

Các bước model hỗ trợ retry riêng mục thất bại trong run hiện có; mục đã hoàn thành hợp lệ được giữ nguyên.

Mỗi bước lưu artifact riêng trong run directory: raw/transcript, normalized, name candidates, classified mentions, name links và final dialogue. Manifest ghi config, fingerprint, kết quả từng bước và lỗi. Output có snapshot bank để giải thích kết quả tại thời điểm chạy.

Resume chỉ dùng lại bước/request có input, config và schema phù hợp. Cho phép retry riêng mục thất bại; không xem cache cũ như nhãn đã xác nhận. Phải phân biệt missing evidence với inference failure.

### Resume là một contract bắt buộc

- Run directory là đơn vị tiếp tục công việc. Manifest và artifact đã commit là nguồn trạng thái; không yêu cầu chạy lại từ folder ảnh để khôi phục từng bước đã hoàn thành.
- Một bước chỉ được đánh dấu `completed` sau khi output đã ghi đầy đủ, kiểm tra contract và có record hoàn thành với tham chiếu/hash artifact. Chỉ tồn tại file output hoặc file ghi tạm không chứng minh bước hoàn thành.
- Khi resume, bước `completed` có artifact hợp lệ được đọc lại và **bỏ qua toàn bộ phần xử lý/model của bước đó**. Kết quả ấy được chấp nhận làm đầu vào của bước tiếp theo, không yêu cầu model đánh giá lại.
- Việc chấp nhận kết quả để tiếp tục xử lý không đổi prediction thành nhãn đã được người dùng xác nhận. Không tự sửa nội dung của artifact hoàn thành.
- Bước `running`, `partial` hoặc `failed` tiếp tục từ các mục đã commit bên trong bước. Mỗi target có trạng thái/result/error riêng; request đã hoàn thành hợp lệ không được gọi lại chỉ vì bước tổng chưa hoàn thành.
- Kết quả hợp lệ trả `unknown`, abstain hoặc trường hợp bị bỏ qua theo quy tắc nghiệp vụ được coi là đã xử lý. Không lặp request để ép có một mapping. Failure do HTTP, timeout hoặc response không hợp lệ được lưu riêng và có thể retry.
- Commit request/target xảy ra ngay sau khi có kết quả hợp lệ; không đợi toàn bộ chapter hoàn thành mới lưu. Cache hỗ trợ tái sử dụng request, còn artifact/manifest quyết định mục nào đã được xử lý.
- Output và trạng thái được ghi atomically. Nếu bị ngắt giữa các thao tác ghi, cơ chế khôi phục kiểm tra record commit và artifact tương ứng; không đánh dấu hoàn thành cho dữ liệu bị ghi thiếu.
- Ghi mapping tên vào bank phải idempotent và có journal/operation key. Nếu bank đã được cập nhật nhưng tiến trình bị ngắt trước khi lưu trạng thái target, resume nhận ra operation đã áp dụng; không nhân đôi lịch sử, không cấp lại ID và không ghi đè tên sửa thủ công.
- Bank thay đổi do chính lượt chạy không tự làm mất hiệu lực các bước đã commit. Run giữ snapshot/provenance cần thiết để tiếp tục; không fingerprint toàn bank hiện tại như một input bất biến rồi tự invalidate mọi bước sau mỗi lần ghi tên.
- Nếu artifact hoàn thành mất/hỏng, nguồn ảnh khác hoặc config/schema không tương thích, báo rõ bước không thể tái sử dụng. Không âm thầm coi dữ liệu hỏng là hoàn thành, cũng không âm thầm chạy lại toàn chapter. Chạy lại có chủ đích là một thao tác riêng và chỉ invalidate phần phụ thuộc cần thiết.
- CLI cần lệnh rõ ràng để tiếp tục một run directory, cùng khả năng retry mục thất bại; lệnh toàn luồng khi resume cũng đi theo contract này.

Phạm vi đã chốt: chỉ resume run của pipeline mới. Run cũ có version/schema không tương thích được báo rõ, không được tự nhập vào run mới. Việc bảo toàn/migrate bank dùng chung là công việc riêng, không đồng nghĩa hỗ trợ resume các chapter chạy bằng pipeline cũ. Output cũ được giữ để đối chiếu.

## Lỗi và kiểm chứng

Nhánh classify/link lỗi ở một mục: ghi lỗi, không ghi mapping từ mục đó, tiếp tục mục khác và đánh dấu output chưa hoàn chỉnh nếu còn failure. Extraction thất bại hoặc input không hợp lệ: dừng vì downstream không có nguồn hợp lệ.

Kiểm chứng cần bao phủ: pending trang 1 được xét trang 3 nhưng không trang 4; pending khác chapter không match; stable ID không bị giới hạn pending; không có tên trên pending; tên đã biết không gọi LLM/VLM; tên sửa thủ công không bị ghi đè; narration không gán nhầm referenced character thành speaker; thứ tự và nguồn không đổi; lỗi cục bộ và resume không làm mất/nhân đôi kết quả. Test resume phải mô phỏng ngắt giữa bước/giữa cập nhật bank và xác nhận các step/target đã hoàn thành không gọi lại model, đồng thời phần còn thiếu tiếp tục đúng kết quả.

Test contract chọn thiết bị gồm auto fallback, CPU rõ ràng và lỗi CUDA không khả dụng. Kiểm chứng inference CUDA thật cần máy có GPU/backend tương thích; trên máy hiện tại chỉ phát hiện Intel Iris Xe, chưa có môi trường để kiểm thử CUDA thật.

Tài liệu sử dụng và báo cáo sau triển khai đặt trong `docs/news/`. Bản này là thiết kế trước triển khai; chưa sửa code hay chạy lại model.

## Các phương án đã loại bỏ

Scene segmentation, ordering reasoning, ba lượt speaker/identity/addressee toàn chapter, VLM cho mọi panel, VLM cho mention không phải narration, inference cho tên đã có mapping, bank riêng mỗi run và promote pending bằng tên đều không thuộc luồng mới.

## Xác nhận cuối

Người dùng cần xác nhận bản tổng hợp này trước triển khai theo skill grill-me. Các lựa chọn cụ thể về tên thư mục, command và model Ollama đang là đề xuất để review; các giới hạn về hiệu năng và độ bao phủ đã được nêu rõ.
