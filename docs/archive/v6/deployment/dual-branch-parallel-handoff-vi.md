# Bàn giao: Runpod song song + analyze hai nhánh

Ngày 14/10/2026. Phạm vi: sửa source/config local theo `docs/plans/analyze_dual_branch_design_vi.md`; không truy cập Runpod và không chạy test/model/pipeline.

## Đường chạy đã nối

`normalize → scan_names → analyze → identity → export → translate → typeset` giữ thứ tự stage. Analyze mới dùng `dual-branch-v1`, policy `dual-branch-analyze-v6.1-conservative-shout`. Config V6 mới mặc định chọn engine này; config frozen thiếu engine giữ unified analyze để không đổi hành vi run cũ.

Normalization tách utterance có nhiều neo người nói MAGI hợp lệ, mâu thuẫn nhau thành các đoạn nguồn có thứ tự. Caption được route bằng `caption_hint` của geometry, không bằng nhãn thought/narration hay phong cách chữ. Chỉ essential utterance đi qua analyze; nguồn nonessential được giữ để dịch/render với provenance `source_only`.

Neo dialogue chỉ khóa khi cùng một source text có đủ association text-tail và text-character, index hợp lệ và stable bank ID còn hoạt động. LLM không được đổi neo. Original MAGI speaker và speaker cuối lưu riêng. `narrator`/`others` là speaker kind, không tạo ID nhân vật giả.

Scene planner đọc dialogue theo ngữ nghĩa, không dùng ranh giới trang hay ảnh để phát hiện scene. Filler vẫn giữ thứ tự và được analyze/dịch, nhưng không cập nhật topic. Aside có thread riêng; resume quay lại main thread. Caption nhận scene theo vị trí trong essential order; caption-only được ghi là nhóm khoảng cách, không giả lập physical scene.

Mỗi nhánh chạy forward rồi backward với context overlap, checkpoint policy riêng và target coverage chính xác. Anchor plan không đổi; chỉ kết quả sau hai lượt mới vào merge/identity. Recipient caption là self cho thought, audience cho trường hợp còn lại, ghi `default_rule`; dialogue suy luận recipient riêng, thought dùng self. Giới hạn caption mặc định 7 essential vị trí, bao gồm hai đầu: 1→7 hợp lệ, 1→8 không; chuỗi 1→6→11 hợp lệ theo từng liên kết. Aside không cập nhật mốc main narrator.

## Các lớp song song

| Lớp | Đã triển khai | Giới hạn bảo toàn trạng thái |
|---|---|---|
| Truyện | Executor tối đa `story_workers`, mặc định preset 3 | Chapter trong cùng truyện chạy tuần tự; bank có story lock |
| Analyze | Executor có số future đang chờ hữu hạn, `analyze_branch_workers=2` | Caption/dialogue giữ memory riêng; lượt trong một nhánh vẫn tuần tự |
| Scene | Scene độc lập có thể overlap | Nếu có tên chưa resolve duy nhất trong snapshot, scene chạy theo wave có thứ tự; chỉ alias đã chốt được truyền tiếp |
| GPU | FIFO admission dùng chung giữa process | Một visual owner **hoặc** LLM requests trong `llm_slots`; không load các model visual cùng lúc với LLM |
| CPU | Typeset theo page và thread caps | Font/cache và ghi artifact an toàn; render/validation/publication có thể overlap GPU của truyện khác |

Không chạy hai chapter của cùng truyện cùng lúc. Không chạy translation chunk song song khi chunk sau phụ thuộc history. Không tăng mọi thread lên tối đa vì dễ tăng CPU, tranh bộ nhớ và giảm throughput.

Qwen3.8 27B đã được chọn ở config trước đó, giữ context 16384 và conservative-shout. Khi không override: A40 có 2 slot LLM, 4090/5090 có 1. Branch worker không đồng nghĩa với một slot GPU riêng. Mức này chưa benchmark và không bảo đảm model/context fit mọi cấu hình. Nếu thiếu VRAM, recovery có giới hạn; không biến lỗi hạ tầng thành kết quả semantic hợp lệ.

## Điều khiển khi chuẩn bị batch mới

Cả `deployment/runpod/v6.py prepare` và `deployment/runpod/local_v6.py prepare` có các option:

- `--gpu-preset a40|rtx4090|rtx5090`.
- `--story-workers 1..8`: truyện độc lập.
- `--analyze-workers 1..4`: job nhánh/scene.
- `--llm-slots 1..4`: override số slot server/admission; chỉ tăng sau khi được cho phép kiểm chứng VRAM.
- `--caption-max-distance 1..64`: khoảng cách essential bao gồm hai đầu, mặc định 7.

Option được ghi trước hashing vào config mới. Không sửa config sau freeze, không sửa manifest/checkpoint/output cũ. File `configs/pipeline-v6-runpod.json` hiện bật runtime preset 5090; dùng flag chọn đúng GPU khi chuẩn bị. Việc chuẩn bị không cấp quyền triển khai/chạy remote.

## Provenance và kiểm tra

Analyze lưu scene/thread/essential index, routing reason, neo/original speaker, participant ledger, evidence refs, recipient source, pass checkpoints và analysis plan hash. Export/translation giữ metadata cần thiết; local detailed review hiển thị chúng khi artifact mới có thật, không suy diễn bổ sung cho outputs legacy.

Scene participant ledger dùng character detection gần essential utterance nhất trên cùng trang để scope appearance, cộng với association speaker gốc. Đây là heuristic vị trí có provenance, **không phải** semantic image scene detection; người xuất hiện ở ranh giới cảnh vẫn là ca cần kiểm chứng. Toàn bộ ID candidate trong scene được giữ, không cắt cửa sổ 10 lượt; prompt compact bớt bằng chứng lặp, full ledger nằm trong artifact.

Neo và appearance ledger chỉ đọc observations thuộc extraction scope hiện tại; không dùng detection của chapter trước dù registry giữ observations có cùng page ID/index. Stable bank ID của text cũng phải khớp detection trước khi khóa neo.

Verifier kiểm tra coverage/thứ tự merge, geometry routing, essential index, mỗi target đúng một receipt forward và backward, scene/branch/plan hash, anchor cùng source, recipient thought/caption và phạm vi evidence/khoảng cách caption. Policy/checkpoint mới tách khỏi legacy. Test source được thêm trong `tests/test_v6_dual_branch.py`; **chưa chạy** theo chỉ thị hiện tại.

CPU vẫn cần cho đọc/giải mã ảnh, tokenization, hình học phía host, font layout, render, kiểm tra hash và I/O. Mục tiêu là giảm oversubscription và lấp thời gian GPU chờ bằng truyện độc lập, không chuyển mọi phép tính CPU sang GPU. Visual admission unload LLM có thể tăng thời gian load lại; phải đo throughput, thời gian chờ, VRAM peak, CPU và chất lượng sau khi được cho phép chạy.

## Chưa chứng minh bằng thực nghiệm

Chưa chạy test, benchmark, model pilot, bootstrap hoặc deployment. Không có tuyên bố GPU đạt 100%, tăng tốc bao nhiêu, scene/speaker đúng bao nhiêu hay model 27B fit hai slot A40. Bước thực nghiệm cần yêu cầu và quyền Runpod riêng của người dùng; không tự mở pod hoặc chạy local inference để thay thế.

Kiểm tra tĩnh tại thời điểm bàn giao: AST parse 49 file Python thay đổi/mới trong worktree và `git diff --check` đều không có lỗi. Kiểm tra này không import/chạy pipeline và không thay thế test runtime. Worktree chứa cả thay đổi có sẵn của người dùng, được giữ nguyên.
