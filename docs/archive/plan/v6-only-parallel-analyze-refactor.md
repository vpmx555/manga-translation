# Refactor v6 và analyze bằng batch song song độc lập

Ngày chốt: 2026-10-21. Người dùng đã yêu cầu triển khai kế hoạch.

## Phạm vi và dữ liệu cần giữ

- Chỉ giữ pipeline v6 với analyze mới và OCR-box typesetting. Bỏ pipeline trước v6, unified analyze, alias nối tiếp, forward/backward bắt buộc và chế độ bubble-body.
- Giữ namespace `manga_pipeline.v6`; chia scene, planning, contract, scheduling và merge theo trách nhiệm.
- **Không xóa bất kỳ tài liệu nào trong `docs/`.** Giữ toàn bộ tài liệu lịch sử và `docs/plans`; file kế hoạch này nằm trong `docs/plan` theo yêu cầu riêng của người dùng.
- Giữ dữ liệu, outputs, models/cache và artifact cũ. Không cam kết resume checkpoint cũ; run mới phải có contract mới.
- Tiếp tục dùng được bank/registry v6 tương thích sau khi kiểm tra schema/model/geometry. Không tự migrate legacy.
- Chỉ giữ vLLM/Qwen, Koharu, MAGI, Hayai, GLiNER và local working-set/native Syncthing. Không truy cập, tạo, khởi động hay chạy việc trên Runpod trong task này.

## Scene

- Một request đọc toàn bộ essential dialogue/caption theo thứ tự nguồn, không chia batch và không cắt bớt văn bản.
- Model trả các ID bắt đầu scene và tóm tắt. Code sinh scene ID và các dải utterance liên tiếp, đầy đủ, không trùng hoặc thiếu.
- Scene theo mạch hội thoại; đổi trang, aside hoặc đổi chủ đề nhẹ không tự tạo scene.
- Tóm tắt tiếng Anh, 1–2 câu, tối đa 512 ký tự; chỉ là bối cảnh, không phải bằng chứng khóa speaker/identity.
- Output reserve đầu tiên 4096 token; nếu bị cắt, lần gọi lại được nâng tối đa 8192 khi context cho phép.
- Tối đa một lần gọi lại sau khi biết request trước đã kết thúc. Không gọi lại khi trạng thái completion còn chưa rõ.
- Input không vừa context phải báo lỗi rõ, không tự chia chapter. Không có essential text thì trả scene rỗng, không gọi model.

## Batch độc lập

- Giữ hai nhánh caption/dialogue; mỗi batch thuộc đúng một scene và một nhánh.
- Input chỉ có văn bản batch, tóm tắt scene và snapshot dữ liệu nền cố định. Không truyền toàn bộ văn bản scene hoặc kết quả suy luận của batch khác.
- Mọi batch có thể chạy đồng thời; không có barrier theo scene/nhánh/lượt. Merge theo thứ tự nguồn, không theo thời gian hoàn tất.
- Giữ các quy tắc speaker/mention/caption còn áp dụng trong batch. Không truyền lịch sử suy luận, narrator state hoặc thread state xuyên batch.

### Điểm cắt và ngân sách

- Trần mềm ban đầu: 8 target/batch.
- Dialogue bắt đầu bằng target có anchor chặt: cùng nguồn text–tail–character và stable bank ID còn hiệu lực.
- Khi điểm cắt mềm chưa có anchor tiếp theo, tiếp tục gom các target liên tiếp.
- Ngoại lệ mở đầu không anchor: batch đầu scene và khi giới hạn token cứng buộc phải cắt. Ghi rõ lý do ngoại lệ.
- Dự trù output CPU: `ceil_to_256(1.25 * (128 + 384 * targets + 192 * mentions))`, tối thiểu 512, tối đa 8192 token.
- Các hệ số là mặc định khởi đầu, không phải số đo thực nghiệm. Không thay partition của batch dựa trên kết quả suy luận của batch khác.
- Tokenize chính xác request đã lập; cache theo input/model/template fingerprint và tái sử dụng giữa planner/provider. Chỉ đo lại khi input thật sự thay đổi.
- Nếu phải cắt vì token, ưu tiên điểm cắt có anchor còn vừa; nếu không có mới dùng ngoại lệ. Một target không vừa phải được báo lỗi, không âm thầm cắt câu.

### Pool và contract

- Pool cluster là các cluster thuộc run hiện tại, xuất hiện trên những trang chứa target của batch; không nhầm cluster cùng số khác trang/run.
- Bổ sung person đã biết khớp mention của batch theo quy tắc hiện hành. Không nhận ID tự tạo hoặc ứng viên suy ra từ batch khác.
- `others` là một sentinel duy nhất, không có `others_1`, `others_2` hoặc identity theo số lượng người chưa biết.
- Model chỉ chọn reference đã cung cấp; code ánh xạ person ID và kiểm tra anchor, ownership mention, phạm vi reference và bằng chứng.
- Không tự sửa semantic. Giữ target hợp lệ; target lỗi được lưu unresolved/source-only cùng provenance.

## Identity

- Sau analyze, tổng hợp bằng chứng bằng code; không thêm request model.
- Khác person ID không tự chứng minh khác người; trùng tên không tự chứng minh cùng người hoặc tạo mâu thuẫn.
- Giữ canonical/bank mapping và liên kết đủ bằng chứng gọi tên; giữ nhiều ứng viên nếu chưa đủ bằng chứng.
- Deduplicate bằng nguồn occurrence thật, không đếm retry hoặc bản sao context thành bằng chứng mới.
- Calling-event provenance dùng đúng trang/observation của recipient cluster, không giả định cùng trang với caller.
- Identity không sửa embeddings/crops của bank thị giác gốc. Extraction tiếp tục quản lý bank như luồng v6 hiện hành.

## Continuous batching và adaptive admission

- Một hàng đợi chung cho mọi scene/nhánh, bổ sung request ngay khi có chỗ.
- Admission tối thiểu 1, khởi đầu 2, tối đa 8; trần engine tương ứng 8 sequence.
- Giữ model/revision ghim hiện tại, context 32768 và `max_num_batched_tokens=2048` làm cấu hình đầu tiên. Không hot-reload model/context/profile khi đang chạy.
- Chỉ dùng metrics công việc thực, không thêm request model dò tải trong chapter.
- Poll metrics mỗi 5 giây; xét điều chỉnh mỗi 10 giây khi cửa sổ có ít nhất 2 request hoàn tất hoặc 256 generated tokens.
- Tăng từng bước khi có backlog, KV cache dưới 80%, không có preemption/OOM mới.
- Giảm một nửa (tối thiểu 1) khi KV cache đạt 90%, có preemption mới hoặc OOM; cooldown 30 giây.
- Sau một lần tăng, nếu generation throughput giảm hơn 10% trong hai cửa sổ đủ mẫu liên tiếp thì quay về mức ổn định trước và cooldown.
- Hai poll liên tiếp không có metrics hợp lệ thì về 1; chỉ tăng lại sau hai mẫu hợp lệ.
- Giảm cửa sổ chỉ chặn cấp thêm, không hủy request đang chạy.

### Lỗi và checkpoint

- OOM đã trả lỗi và kết thúc rõ được thử lại tối đa một lần sau khi giảm tải và kiểm tra service còn khỏe.
- Timeout chưa rõ completion phải pause cấp việc; không gửi trùng hoặc restart service để lách guard.
- Ghi receipt trước POST và journal sau response; ngân sách retry tồn tại qua resume.
- Lỗi semantic không tự gọi lại. Scene retry có ngân sách riêng tối đa một lần, không nhân với provider retry.
- Giữ checkpoint batch đã hoàn tất, kể cả một batch khác lỗi. Không báo lỗi hạ tầng là thành công semantic.
- Log riêng planning/tokenize/queue/inference time, tokens, valid/unresolved targets, admission, KV pressure và preemption.

## Refactor và interface

- Block cấu hình `llm` thay `ollama`; chỉ còn real backend vLLM và detector GLiNER. Vẫn hỗ trợ provider/detector mock qua dependency injection.
- Manifest có policy contract mới; từ chối run/config cũ không tương thích thay vì fallback ngầm.
- Giữ các lệnh CLI phục vụ v6; bỏ các lệnh/nhánh chỉ phục vụ pipeline cũ.
- Chuyển các helper prepare/bundle/SSH/bootstrap/verification còn được native workflow dùng trước khi xóa entrypoint cũ.
- Bỏ runtime selectors cũ nhưng giữ font/layout/geometry helper cần cho OCR-box và inpaint.
- Cập nhật concurrency/profile verification từ giả định hai slot sang giới hạn cấu hình. Không bỏ integrity guard hoặc giả qualification receipt.
- Review tự sinh, hiển thị scene summary, batch, cluster pool, anchor, ngoại lệ và unresolved; không thành cổng phê duyệt stage.
- Cập nhật README và chỉ dẫn tài liệu hiện hành mà không xóa tài liệu lịch sử trong `docs/`.

## Kiểm chứng và điều kiện hoàn tất

Chỉ kiểm tra tĩnh và unit test local ngắn với mock/fixture; không tải model, inference/pilot, build image hoặc truy cập Runpod.

- Scene: coverage/order/contiguity, summary contract, empty input, overflow và retry budget.
- Batch: không mất/trùng target, anchor cut/ngoại lệ, hard-limit splitting và singleton overflow.
- Pool: đúng page/run, known-name ownership, singleton `others`, anchor bất biến.
- Isolation/merge: cùng mock response thì completion order/concurrency không đổi semantic result.
- Budget: cache tokenizer được dùng lại; provider không đo lặp; không âm thầm bỏ input.
- Scheduler: continuous refill, bounds 1–8, cooldown, metrics loss, rollback và OOM retry.
- Checkpoint: crash/resume không tăng retry budget hoặc gửi trùng uncertain request.
- Identity: tên nhiều person ID không tự gây gộp/lỗi; source occurrence dedup; recipient khác trang đúng provenance.
- Compatibility: bank/registry v6 hợp lệ còn dùng được; run/config cũ bị từ chối rõ.
- Cleanup: không còn active import/config/runtime dependency vào đường đã bỏ; helper native còn hoạt động.
- Preservation: không xóa docs, dữ liệu hoặc outputs cũ.

Trần 8, hệ số ước lượng và các ngưỡng admission chưa được đo trên GPU thực. Kiểm chứng tốc độ/sức chứa và qualification profile mới thuộc một yêu cầu thực thi GPU riêng.
