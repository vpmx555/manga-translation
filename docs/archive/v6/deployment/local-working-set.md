# Plan chạy Bocchi v6 trên disk local của Runpod

> Plan lịch sử riêng cho Bocchi. Run mới theo
> [runbook hiện hành](../../plans/runpod-v6-agent-operational-plan.md);
> không sao chép model/GPU/scope hay quyền chạy của plan này sang task mới.

Người dùng cho phép triển khai sau khi chốt plan, không cần xác nhận thêm và cho
phép mở Runpod khi cần cho tác vụ này. Chạy production chapter 1–3, 76 trang;
không dùng bank cũ. Không build/push image fast lớn.

## M1 — Chuẩn bị offline

Phương pháp: bundle source có hash, input manifest theo chapter, xuất dependency
locks từ image v6 đã build. Input: source/config hiện tại, 31/24/21 ảnh Bocchi và
locks MAGI/Koharu/Hayai. Output: plan bất biến, config snapshot, source ZIP và
lock ZIP nhỏ. Không chạy model hoặc test CPU dài.

## M2 — Khởi động và bố trí filesystem

RTX 4090, image boot đã có `vpmx555/manga-translate-v2:v5.0.2-fast-code.1`.
Đọc giá/stock trước khi mở GPU. Input: plan, bundle, lock ZIP. Output: source
`/opt/manga-work/project`, env/tool `/opt/manga-work/execution`, runtime
`/opt/manga-work/runtime`; volume bền `/workspace/manga-runtime`.

Kiểm tra `findmnt` và disk free; từ chối dùng FUSE/NFS/CIFS cho working-set.
Source ở local; model cache, Ollama, input, bank, masks, output và log cũng ở
local. Volume chỉ giữ ingress archive/receipt, checkpoint archive và state nhỏ.
`MANGA_EXECUTION_ROOT` riêng lẻ không đủ; runtime phải chuyển cùng source.

## M3 — Bootstrap và preflight một lượt

Cài từ locks đã chuẩn bị, không resolve lại khi lock tồn tại. Env MAGI,
Koharu/RF-DETR và Hayai riêng; tải weight một lần vào cache local. Gemma
`gemma4:e4b-it-q4_K_M`, context 16384; Hayai OCR v2.5 nova theo revision config.
Chuẩn bị cả Koharu và AOT inpaint. Input upload nguyên ảnh, không đổi định dạng.
Chỉ nhận chapter đã publish atomic với `.input-manifest.json`, kiểm tra ingress.
Output: receipt filesystem, signatures, CUDA thật và inference preflight.

## M4 — Inference và durability

Chạy một GPU queue, chapter 1 → 2 → 3, dùng pipeline v6 đã chốt. Giữ prompt
xưng hô nhất quán, thresholds, font AstroCity, spacing 1.6 và H-center.
Bank đầu phiên mới; các chapter sau dùng bank do chính phiên này tạo.

Mỗi stage sau commit tạo ZIP checkpoint trên local, chứa stage artifact,
manifest/snapshots và bank tại ranh giới stage. SHA-256 + inventory + CRC;
chuyển một archive sang volume và ghi ready receipt atomic sau xác minh.
Khôi phục archive về đúng runtime canonical nếu local disk mất; không sửa
manifest/source path hoặc đánh dấu stage completed bằng tay.

Khi chapter ngừng ghi, publish full output ZIP để collector tải trực tiếp,
không giải nén hàng nghìn file lên FUSE. Giữ partial/failed, thử hết chapter;
không sửa source/prompt/case giữa inference. Log local, state monitor nhỏ trên
volume. Sau 10 phút không có counter/checkpoint/bytes tiến triển: chẩn đoán
giới hạn, không retry vô hạn hoặc giữ GPU chờ offline work.

## M5 — Thu thập và bàn giao

Input: archive chapter/bank/audit đã publish, receipt checksum. Output local:
`outputs/runpod-bocchi-v6-localdisk-20261012` cùng collection receipt.
Xác minh archive/inventory và artifact contract; dựng review từng stage bằng
code local. Nhập `banks/bocchi` và `banks/_persons_v6/bocchi` từ bank mới, backup
bank local cũ trước swap. Không trộn cluster_id với person_id.

Đủ backup đã xác minh thì terminate đúng pod của tác vụ; không xóa pod cũ hay
volume khác. Giữ tất cả JSON, visualization, natural/localized translations,
ảnh inpaint/typeset, locks, effective config, source hash và lỗi nếu có.
