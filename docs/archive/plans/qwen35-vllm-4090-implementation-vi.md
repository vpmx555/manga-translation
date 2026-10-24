# Qwen 3.5 27B trên vLLM — thiết kế đã chốt và phần đã triển khai

> **GPU hiện hành: RTX 5090 theo yêu cầu 16/10/2026.** Lượt 4090 dừng ở preflight:
> weights 18,8 GiB, KV khả dụng 1,08 GiB so với 2,15 GiB cần cho 32768 token;
> chưa chạy analyze. Config hiện ghim `rtx5090` / `RTX 5090`, giữ context 32K,
> một slot và không offload. Các lựa chọn 4090 bên dưới là thiết kế lịch sử.

Ngày: 2026-10-15. Chỉ triển khai mã/config và tài liệu trong workspace. Chưa cài dependencies, tải model, build/push image, chạy test/model hoặc truy cập Runpod trong phiên này.

## 1. Quyết định cuối

Chỉ thị mới nhất của người dùng thay lựa chọn Ollama bằng **vLLM**. Các quyết định còn lại được giữ; những phần chưa chốt được tác giả triển khai quyết định theo quyền người dùng đã cấp.

| Hạng mục | Giá trị |
| --- | --- |
| Engine | vLLM 0.31.0, môi trường Python riêng |
| Model | `cyankiwi/Qwen3.5-27B-AWQ-4bit` |
| Revision | `5cba9b5f1bca69fab9b75c88ae7817ae22d7d898` |
| Định dạng quantization | AWQ 4-bit đóng gói `compressed-tensors`, không phải GGUF Q4_K_M |
| Dtype tính toán | BF16 |
| GPU | Đúng một RTX 4090; không chuyển GPU |
| Context | 32.768 token cố định; không tự hạ khi thiếu VRAM |
| Concurrency | `max_num_seqs=1`, runtime LLM slots 1 |
| Lịch | Một chapter hoạt động; luân phiên truyện sau mỗi chapter; giữ thứ tự chapter của từng truyện |
| Analyze nhánh | Tối đa 3 targets, giữ lượt xuôi và lượt ngược |
| Scene plan | Tối đa 3 topics không phải filler, giới hạn riêng với batch nhánh |
| Dịch | Tối đa 3 targets, giữ hai phong cách đầu ra |
| Đầu ra | 2.048 token/request cho scene, analyze và dịch; không phải từng target |
| Ngưỡng đóng gói | Input chat đã tính schema + dự phòng output + 512 ≤ 85% context |
| Sampling | Temperature 0, top_k 20, top_p 0,95, presence_penalty 0 |
| Thinking | Tắt bằng `chat_template_kwargs.enable_thinking=false` |
| Structured output | OpenAI-compatible `response_format=json_schema` |
| Generation timeout | HTTP client 600 giây; tách với thời gian nạp và chờ hàng đợi |
| Queue timeout | 600 giây |
| Startup timeout | 600 giây, chỉ dừng process group do chính controller vừa tạo nếu chưa sẵn sàng |
| Retry timeout | Tối đa một lần cho cùng request sau khi có bằng chứng server cũ đã ngừng; không thử lại ngay |
| Retry nội dung provider | 0; caller chịu trách nhiệm repair theo target |
| Recovery chia nhóm | Tối đa 2 lượt cho nhóm đang xử lý; batch cap 3 nên có thể giảm tới 1 |
| GPU memory utilization | 0,94 — cấu hình ứng viên, chưa phải số đo |
| CPU offload | `--cpu-offload-gb 0`, không fallback |
| CUDA graphs | Tắt bằng `--enforce-eager` để giới hạn chi phí khởi động/bộ nhớ ứng viên |
| Prefix caching | Tắt ở cấu hình đầu, tránh thêm biến với kiến trúc hybrid |
| Prefill chunk | Tối đa 2.048 token/lượt scheduler, chunked prefill bật |

**Prefill chunk 2.048 token khác batch analyze 3 targets.** Một bên là cách engine xử lý prompt trên GPU, bên kia là cách pipeline nhóm các đơn vị ngữ nghĩa.

Temperature/presence penalty được đặt rõ để không kế thừa sampling từ model một cách vô tình. Không khẳng định đây là sampling tối ưu cho chất lượng dịch; cần đo riêng nếu thay đổi sau này.

## 2. Cấu hình và chọn provider

[`configs/pipeline-v6-runpod.json`](../../configs/pipeline-v6-runpod.json) chọn `ollama.backend = "vllm"`. Tên section `ollama` được giữ như khóa manifest cũ cho các tùy chọn inference chung; **nó không có nghĩa pipeline dùng Ollama** khi backend là vLLM. Section `vllm` chứa phiên bản, revision và các tham số server riêng.

[`providers/factory.py`](../../src/manga_pipeline/providers/factory.py) chọn provider theo backend, không tự fallback. [`providers/vllm.py`](../../src/manga_pipeline/providers/vllm.py) dùng `/v1/chat/completions`, `/v1/models` và `/tokenize`; không gọi API Ollama. Factory cũng áp dụng cho đường dịch độc lập có đủ snapshot cấu hình engine.

Các config local/visual và model OCR/NER không được chuyển sang Qwen. Phạm vi “toàn bộ” là toàn bộ tác vụ LLM đã dùng provider chung, gồm analyze, dịch và sửa/rút gọn bản dịch.

## 3. Ngân sách context và rút thông tin

[`runtime/llm_budget.py`](../../src/manga_pipeline/runtime/llm_budget.py) giữ hợp đồng chung cho dự phòng đầu ra, suffix hướng dẫn và giảm ngữ cảnh. Với vLLM, các đường analyze và dịch lấy số token chat từ `/tokenize` của chính model, với cùng messages, schema trong prompt và công tắc thinking như request generation. Schema cũng được truyền làm ràng buộc JSON; phần schema trong prompt giúp model hiểu trường phải sinh và được tính vào input.

Heuristic UTF-8 vẫn tồn tại cho provider cũ và lúc chuẩn bị dữ liệu synthetic. Nó không được gọi là phép đếm token chính xác. Generation còn kiểm tra `usage.prompt_tokens` sau phản hồi để phát hiện lệch dự phòng.

Thứ tự khi không vừa:

1. Giảm số targets trong batch, giữ ranh giới thread/caption.
2. Khi chỉ còn một target, giảm lịch sử cũ, tóm tắt, context lân cận rồi các phần hỗ trợ trực tiếp được phép.
3. Có thể bỏ forward support, anchor hỗ trợ ngoài target, aliases và bằng chứng participant ngoài target theo thứ tự đã ghi trong code.
4. Giữ nguyên lời thoại target, khóa/ID, target anchor và candidate mention ownership. Không cắt source text để làm request vừa.
5. Bộ tối thiểu vẫn không vừa thì báo lỗi; không đổi context hoặc giả lập `unknown` hợp lệ.

Tham chiếu bằng chứng được giới hạn lại theo request thực đã gửi. Kết quả có giảm ngữ cảnh được đánh dấu review và lưu metadata `semantic_context_reduction`/`context_reduction`. Checkpoint request/schema/prompt và telemetry giữ dấu vết để kiểm tra sau.

Các nhánh scene/dialogue/caption chính hiện dùng text có cấu trúc; không tự thêm ảnh. Provider hỗ trợ message ảnh cho các đường gọi thực có ảnh, nhưng thiết kế không tự bỏ ảnh đầu vào của các đường đó để tránh mất crop mục tiêu không được định danh rõ. Nếu bộ ảnh tối thiểu không vừa thì báo lỗi thay vì âm thầm bỏ bằng chứng. Việc mở rộng chính sách giảm ảnh cụ thể cần biết contract của đường gọi đó.

## 4. Vòng đời GPU khi thay engine

vLLM thường giữ ngân sách GPU sau khi nạp. Vì GPU cũng phục vụ visual, cơ chế API unload của Ollama không dùng được cho vLLM.

Thiết kế giữ nguyên **quyền sở hữu GPU theo stage**: visual và LLM không đồng thời giữ lượt. Cách thực hiện cho backend mới:

- Chỉ khởi động vLLM khi một tác vụ LLM/tokenization cần dùng model.
- Tái sử dụng service cùng cấu hình trong giai đoạn LLM.
- Khi visual nhận lượt, dừng process group vLLM do controller quản lý và xác nhận không còn process sống trong nhóm.
- Nạp lại khi tới tác vụ LLM tiếp theo; dừng service khi batch kết thúc bình thường.
- Không tự dừng/restart service sau một generation timeout chưa rõ trạng thái.

Không dùng sleep level 1 vì nó chuyển trọng số sang RAM. Không chọn sleep level 2 ở lần triển khai đầu để tránh thêm các endpoint quản trị và luồng reload đặc thù. Đổi lại, cold start mỗi lần chuyển stage có thể đáng kể và được ghi bằng `llm_service_start/stop` để đo.

Đây là thay đổi bắt buộc ở adapter vòng đời khi đổi engine; không giữ một service vLLM chiếm GPU rồi cho visual tranh phần bộ nhớ còn lại.

## 5. Runtime, dependency và model

[`vllm_runtime.py`](../../deployment/runpod/vllm_runtime.py) quản lý service loopback `127.0.0.1:8000`. Nó chỉ dùng môi trường/model đã chuẩn bị; không tải model khi inference.

- [`requirements-vllm.in`](../../deployment/runpod/requirements-vllm.in) pin phiên bản. Setup biên dịch lock có hashes và sync vào env riêng để không thay dependency MAGI/Koharu/Hayai.
- Fast Dockerfile chuẩn bị env vLLM trên CPU; không bake trọng số vào image bằng mặc định mới.
- Prefetch tải checkpoint theo revision, kiểm tra metadata 4-bit và đủ tập shard, ghi marker.
- Controller native đóng gói thêm lock vLLM được resolve trên CPU; bootstrap chỉ được sync lock đã có, không resolve lại. Nếu env chưa nằm trong image, việc sync vẫn có chi phí cài đặt; ưu tiên image đã chuẩn bị hoặc staging env trước khi thuê GPU. Prefetch không tự cài env bị thiếu.
- Server chạy từ snapshot local, offline mode; chỉ một sequence, context cố định và không offload.
- Khi startup không đạt, không đổi model, context, GPU hoặc quantization.

Không có dependency/model/image nào đã được cài, tải hoặc build trong phiên sửa mã này. CUDA/driver của env vLLM phải được xác minh trên máy được cho phép chạy, không suy từ env MAGI đã có CUDA.

CLI quản lý `doctor` và `prefetch` đã nhận `--config PATH` để chọn đúng snapshot; bỏ option này vẫn giữ mặc định cũ cho các workflow legacy. Không dùng lệnh mặc định legacy rồi coi nó là bằng chứng preflight của vLLM.

## 6. Timeout và phục hồi

[`inference_state.py`](../../src/manga_pipeline/runtime/inference_state.py) ghi pause receipt toàn runtime và ledger theo request. Mất kết nối generation không chứng minh inference đã kết thúc. Queue giữ paused qua cả process exit hoặc chuẩn bị plan khác.

Chỉ được phục hồi khi có bằng chứng process/server cũ cùng process group đã ngừng. Controller có subcommand `recover` cho thao tác operator rõ ràng; nó **không giết công việc chưa rõ trạng thái**, không tự restart để chữa lỗi, và chỉ tạo service mới sau khi xác minh nhóm cũ đã hết.

Sau đó chạy lại preflight phù hợp và resume. Ledger cho phép tối đa một retry nguyên request; retry bị gián đoạn cũng không được gửi trùng vô hạn. Không dùng `/v1/models`, heartbeat hay PID còn sống làm bằng chứng request đã hoàn thành.

600 giây là giới hạn client chờ HTTP, không phải cam kết chapter hoàn thành trong 10 phút. Queue, startup và inference có số đo riêng. Một timeout cộng một retry có thể chiếm khoảng 20 phút xử lý/chờ HTTP ngoài các chi phí khác.

## 7. Preflight, resume và revision

Preflight vLLM thực hiện một request analyze synthetic có schema và prompt dài, không dùng cache của lần preflight trước. Nó ràng buộc kết quả với model revision, engine signature, effective config, source bundle và môi trường đã chuẩn bị.

Đạt preflight chứng minh được đường chạy ứng viên có thể nạp và xử lý mẫu đó với CPU offload tắt; **chưa chứng minh chất lượng hay thời gian trên chapter thật**. GPU đúng loại cũng được kiểm tra trước khi chạy batch/start service.

Driver v6 từ chối resume config khác. Option mới `prepare --reanalyze-existing` ghi quyền fork reanalysis vào plan bất biến; khi được chọn, driver dùng lệnh reanalyze có sẵn và giữ run cũ. Nó không sửa snapshot/checkpoint cũ để giả lập đã dùng model mới. Nếu đổi cả extraction không tương thích, controller reanalyze vẫn có quyền từ chối.

Chỉ chuẩn bị plan không cấp quyền chạy Runpod. Không chạy CLI chuẩn bị hoặc thực thi bằng placeholder khi chưa có input/chapter cụ thể.

## 8. Đóng các phát hiện review

| Review | Thay đổi | Phần còn phải kiểm chứng |
| --- | --- | --- |
| R1 — GPU/32K | GPU constraint, engine args offload 0, startup và structured probe | Đủ VRAM/driver và độ ổn định trên 4090 thật |
| R2 — Token budget | Shared policy + `/tokenize` với prompt/schema thực | So token thực tế/usage, đặc biệt đường đa phương thức |
| R3 — Chapter priority | Một story worker và lịch chapter round robin | Thời gian tới chapter đầu, cold start giữa stage |
| R4 — Timeout | Pause bền, request ledger, proof phục hồi và giới hạn retry | Tình huống mất kết nối/worker thật trên môi trường được duyệt |
| R5 — Output/repair | 2K/request, scene cap 3, từ chối JSON bị cắt, recovery hữu hạn | Tỷ lệ cắt output và số repair trên dữ liệu thật |
| R6 — Evidence | Protected targets, thu hẹp evidence theo request, lưu reduction | Chất lượng speaker/addressee sau giảm context |
| R7 — Reproducibility | Engine/revision/sampling pin, cache key mới, revision rõ | Lock/env/image và audit của lần chạy thực |
| R8 — Acceptance | Kế hoạch kiểm chứng riêng bên dưới | Chưa có benchmark; 30 phút/chapter chỉ là mục tiêu |

## 9. Kiểm tra trong workspace

Được phép: rà soát tĩnh, parse AST Python, parse JSON, kiểm tra liên kết local và whitespace diff. Có file regression model-free được chuẩn bị nhưng **chưa chạy**, theo lựa chọn lập kế hoạch kiểm chứng riêng của người dùng.

Không báo test pass hoặc đã đạt VRAM/latency khi chỉ có các kiểm tra tĩnh. Kết quả cuối của lượt sửa mã được ghi trong [kế hoạch kiểm chứng](qwen35-vllm-4090-validation-vi.md).

## 10. Nguồn kỹ thuật

- [Checkpoint và revision đã pin](https://huggingface.co/cyankiwi/Qwen3.5-27B-AWQ-4bit/tree/5cba9b5f1bca69fab9b75c88ae7817ae22d7d898): metadata 4-bit compressed-tensors; artifact khoảng 20 GB, không phải số đo VRAM đỉnh.
- [vLLM 0.31.0 trên PyPI](https://pypi.org/project/vllm/0.31.0/): phiên bản package đã chọn.
- [Recipe Qwen 3.5 của vLLM](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html): kiến trúc hybrid và các lưu ý phục vụ.
- [Structured outputs](https://docs.vllm.ai/en/stable/features/structured_outputs/): JSON schema qua API.
- [Tokenize protocol ở phiên bản đã pin](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/entrypoints/serve/tokenize/protocol.py): chat template kwargs và generation prompt.
- [Engine arguments ở phiên bản đã pin](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/engine/arg_utils.py): các tham số server dùng trong controller.
- [Sleep mode](https://docs.vllm.ai/en/stable/features/sleep_mode/): lý do không chọn offload trọng số khi ngủ.
# Cập nhật GPU ngày 16/10/2026

Lượt RTX 4090 đã dừng ở preflight: weights 18,8 GiB, KV khả dụng 1,08 GiB so
với 2,15 GiB cần cho 32768 token. Chưa chạy analyze. Người dùng đã yêu cầu chuyển
sang **RTX 5090**; `pipeline-v6-runpod.json` hiện ghim preset/required GPU 5090,
giữ context 32K, một slot, không offload. Nội dung thiết kế 4090 bên dưới là
lịch sử của phương án ban đầu, không phải profile GPU hiện hành.

