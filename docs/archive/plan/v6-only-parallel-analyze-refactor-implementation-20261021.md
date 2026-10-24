# Kết quả triển khai v6 independent batches

Ngày: 2026-10-21. Kế hoạch nguồn: [v6-only-parallel-analyze-refactor.md](v6-only-parallel-analyze-refactor.md).

Đã hoàn tất phần triển khai và kiểm chứng offline của kế hoạch. Không truy cập
Runpod, SSH, model server, tải model, build/push image hoặc chạy inference thật.
Qualification GPU và đo tốc độ/sức chứa thuộc một yêu cầu thực thi riêng.

## Thay đổi chính

- Contract mới `independent-batches-v6.1`, namespace `manga_pipeline.v6` giữ nguyên.
  Config dùng `llm`; real provider chỉ vLLM/Qwen, detector chỉ GLiNER; giữ
  Koharu/MAGI/Hayai. Model/revision hiện hành, context 32768, engine 8 sequence,
  `max_num_batched_tokens=2048` được ghim. Run/config cũ bị từ chối rõ ràng.
- Scene đọc toàn bộ essential dialogue/caption trong một request; code tạo range
  liên tiếp, summary 1–2 câu tối đa 512 ký tự, reserve 4096/8192, một retry có
  journal. Không chia chapter hoặc cắt nguồn để tránh context overflow.
- Hai nhánh caption/dialogue có batch độc lập theo scene. Snapshot nền cố định;
  không history, forward/backward, alias/narrator state giữa batch. Merge source
  order; identity tổng hợp bằng code sau analyze, không thêm request model.
- Soft cap 8 chờ anchor; hard split ưu tiên anchor còn vừa và ghi overflow budget,
  exception provenance. Singleton overflow báo lỗi. Tokenization exact có cache
  theo messages/model/template; serialization canonical ổn định khi thứ tự key
  object thay đổi. Provider giữ bản copy config frozen.
- Cluster references có scope riêng cho run, kể cả hình học giống nhau. Bank person
  ID tương thích giữ ổn định. Pool chỉ lấy current scope và trang batch; kiểm tra
  known-name ownership, mention ownership, anchor và evidence references. Chỉ có
  một sentinel `others`; semantic lỗi giữ valid peers và source-only unresolved.
- Một queue refill liên tục cho mọi scene/nhánh; admission 1–8 bắt đầu 2. Có
  metrics 5 giây, cửa sổ 10 giây, cooldown/backoff, preemption, rollback throughput
  và metrics-loss recovery. Cached results không được tính là request engine thật.
- OOM đã kết thúc được đưa lại vào queue sau backoff để chờ drain, tối đa một
  retry có health check và budget tồn tại qua resume. Uncertain completion pause
  queue trước tokenize/POST mới. Receipt trước POST, response/hash sau completion;
  malformed/interrupted receipts không được tạo duplicate request.
- Calling-event key dùng occurrence vật lý, không dùng run/text IDs làm bằng
  chứng mới. Recipient provenance đúng image/page/observation của recipient.
  Registry kiểm tra source-bank schema/model/geometry contract khi có marker;
  bank/registry v6 tương thích tiếp tục dùng được. Không tự migrate legacy và
  không sửa embeddings/crops bằng identity semantic.
- OCR-box là typesetting mode duy nhất. Giữ font/layout/geometry/inpaint helpers
  cần thiết; bỏ orchestration/entrypoint/provider/selector chỉ phục vụ pipeline cũ.
- Native workflow vẫn qua `local_v6.py`; helper chuyển sang `native_prepare.py`
  và `native_transport.py`. Giữ integrity, identity/canary, source/config bundle,
  checkpoint/publication checks. Qualification receipt phải khớp profile và real
  usage; không tạo qualification giả. Sửa start/stop quanh probe và paths/local
  roots trong Docker/bootstrap. Không build hay chạy những đường này trong phiên.
- Stage review và detailed review tự sinh scene summary, batch/pool, anchor,
  exception, unresolved/errors; backfill không gọi model và không thay artifact.

## Kiểm chứng

Chạy riêng từng nhóm unit/mock/fixture ngắn: **144 ca, 141 pass, 3 skip**.
Ba skip là hai fixture filesystem/lock chỉ dành Linux và một CUDA parity fixture
cần opt-in. Một fixture Syncthing daemon thật không chạy trong task này.

- 19 test independent batch cơ bản; 20 test edge/isolation/delivery; 9 test vLLM
  protocol/budget; 4 mention/translation-history contract; 15 test pipeline v6.
- Các nhóm storage/timing, bank, GLiNER/Hayai, CLI/progress/review, geometry/font/
  OCR-box/inpaint, native ingress/identity/startup/runtime cũng pass theo phạm vi
  mock/fixture. Không chạy broad model test hoặc pilot nội dung.
- Collection: **145 ca, không lỗi import**. AST toàn `src`, `deployment/runpod`,
  `tests` hợp lệ; bốn config hiện hành load được; `git diff --check` pass.
- Không có deletion trong `docs`, `data`, `outputs`, `models`. Không sửa dữ liệu
  manga, cache/model hay outputs thật. Chưa commit; worktree đã có nhiều thay đổi
  từ trước phiên nên không reset/checkout chúng.

Tests cho các contract đã bỏ được giữ trong `tests/retired/*.py.txt`, không che
failure bằng skip marker. Các assertion còn phù hợp đã chuyển sang v6. Xem
[ghi chú test lịch sử](../../tests/retired/README.md). Thử nghiệm Ollama cũ được giữ
ở `scripts/retired/*.py.txt`; pytest chỉ collect `tests`.

## Giới hạn của bằng chứng

Các hệ số output reserve và ngưỡng admission vẫn là mặc định chưa đo GPU. Chưa
có qualification receipt thực cho profile mới và không có cam kết throughput/VRAM.
Không hỗ trợ resume checkpoint trước contract mới. Summary language/sentence
validation là heuristic cộng với model schema/prompt, không phải bộ nhận diện
ngôn ngữ hoàn chỉnh. Bản [bàn giao trước](v6-only-parallel-analyze-refactor-handoff-20261021.md)
được giữ để lưu lịch sử; báo cáo này là trạng thái mới nhất.
