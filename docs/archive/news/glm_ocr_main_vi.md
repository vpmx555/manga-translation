# GLM-OCR trong pipeline chính

Mặc định bốn config pipeline dùng `glm-ocr`, model `zai-org/GLM-OCR`, revision
`2e85a62840ccac27daa451df36c736c4636b8628`. MAGI vẫn detect text/nhân vật và
Koharu cung cấp layout. Crop giữ quy tắc floor/ceil, clipping và grayscale→RGB
của pipeline trước; không thêm padding hoặc thay detection thresholds.

GLM chạy trong env riêng (Transformers 5.20.2, Torch 2.8.0+cu128, Pillow 13.0.0,
NumPy 2.2.6), CUDA-only, BF16, SDPA, greedy, prompt `Text Recognition:`, batch 8,
max 2048 token/crop. Không offload hoặc chạy model trên CPU. Env/lock độc lập
với MAGI, visual và vLLM. Lock Linux được chuẩn bị trước khi thuê GPU.

Đầu ra lưu raw text, text chuẩn hóa whitespace, token count, empty/truncation.
Chỉ bỏ code fence bao trọn kết quả; không tự nối tên hoặc dehyphenate nội dung.
Markdown rỗng thành chuỗi rỗng và được đánh dấu trong `ocr_review` của extract.
Output bị cắt token không được chấp nhận như OCR hoàn chỉnh. Alignment trang/crop
được kiểm tra; OOM giảm batch có giới hạn bằng cơ chế recovery hiện có.

Runpod dùng local_v6/native Syncthing chuẩn, bốn lock: main/visual/GLM/vLLM.
GLM prefetch và CUDA doctor dùng interpreter riêng. Sau extract, GLM worker kết
thúc trước các lượt Qwen analyze/relationship/translation, không giữ cả hai model
trên GPU. Metadata extract giữ config và các raw OCR details để review.

Concurrency qualification cho trần 32 gồm hai pha trên cùng server/profile:
8 request input dài với analysis output reserve và 32 request input ngắn với
decode dài; cả hai phải có usage thật, JSON đầy đủ và peak running đúng mức.
Không có nghĩa 32 request context dài luôn vừa VRAM. Scheduler vẫn tăng dần và
giảm tải theo metrics; không tắt integrity/concurrency gates.

Kiểm tra offline: `test_glm_ocr.py`, `test_hayai_ocr.py`, mocked full v6 pipeline,
runtime và mixed-concurrency receipt tests. Không chạy model pilot CPU.
