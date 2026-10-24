# Bàn giao triển khai v6 independent batches — 2026-10-21

> Đã tiếp tục theo yêu cầu mới của người dùng. Các việc offline còn lại bên dưới
> đã được xử lý; xem [báo cáo triển khai và kiểm chứng cuối](v6-only-parallel-analyze-refactor-implementation-20261021.md).
> Nội dung bàn giao cũ được giữ làm lịch sử, không còn là danh sách việc đang chờ.

## Cập nhật cuối trước khi dừng (07:43 UTC / 14:43 giờ Việt Nam)

Các mục chi tiết bên dưới được ghi sớm để chống mất phiên; cập nhật này có ưu tiên khi có khác biệt:

- **45 test chọn lọc pass**: independent batches 19; v6 pipeline 14; native identity 3; vLLM inference policy 9. Test vLLM đã đổi key `llm`, bounds tới 8 và env `MANGA_LLM_*`. Lần chạy v6 pipeline cuối khoảng 14 giây, không model/network.
- AST toàn src/deployment pass; CLI parser review pass; cả bốn config hiện hành load được; import native modules pass sau các sửa cuối về native lane.
- Detailed review `review/v6.py` đã thay analyze trace bằng scene summary/batch/pool/anchor exception/unresolved; bỏ fallback bubble-body. Stage review vẫn tự sinh, không stage gate.
- Đã thêm `docs/v6/independent-analysis.md`, banner hiện hành trong README, docs/v6/README và operational runbook. Nội dung lịch sử giữ nguyên bên dưới banner.
- Native concurrency lane đã start service trước probe và stop sau probe trong finally; prepare mới yêu cầu probe khi slots >1. Receipt gắn vLLM signature/config fingerprint, real usage, peak concurrency; batch kiểm tra receipt tương ứng. **Chưa chạy GPU và chưa có qualification receipt thật.** Cần bổ sung mock tests cho guard/lifecycle này.
- `native_transport.py` đã bỏ helpers sync/push_input/fetch_data và constants workspace cũ; chỉ giữ bundle/SSH/connection helpers còn dùng. Native prepare/batch validate policy contract mới.
- Occurrence calling-event đã loại run/text/page IDs khỏi khóa vật lý: dùng image hashes, OCR boxes, mention offsets và recipient image/boxes. Provenance vẫn giữ recipient page/index. Fixture kiểm tra đổi ID run không tăng occurrence.
- `pipeline.py` đã đổi tên biến/thông báo validator sang llm (vẫn cố ý nhận diện key ollama để từ chối config cũ).
- `git diff --check` cuối cùng **pass (exit 0)** sau khi trim ba blank EOF ở pipeline.py, review/renderer.py, translation/stage.py. Cảnh báo CRLF trong các file đã thay đổi từ trước không phải integrity failure.

**Ưu tiên còn lại thực sự:** mục 1 migration test legacy; mục 6/7 tăng coverage và rà edge cases scheduler/receipts/contracts/verifier; mục 9 audit requirements/Docker/bootstrap; kiểm tra static/preservation cuối. Các mục 2/3/4/5 đã được xử lý như trên nhưng vẫn cần review/test tích hợp mock thêm. Không chạy broad suite, model pilot, daemon Syncthing thật, build hay Runpod. Chưa commit và chưa hoàn thành toàn kế hoạch.

## Yêu cầu và trạng thái

Người dùng yêu cầu triển khai `docs/plan/v6-only-parallel-analyze-refactor.md`, sau đó yêu cầu làm tiếp tối đa khoảng 10 phút và lưu bàn giao vì gần rate limit. Đây là công việc **chưa hoàn tất toàn bộ**. Không được báo kế hoạch đã hoàn thành chỉ vì các test mới pass.

Workspace: `D:\AIP491\manga-translate-v2`. Repo có rất nhiều thay đổi/untracked từ trước phiên này. Không reset/checkout toàn repo. Chưa commit. Không truy cập Runpod, SSH, model server, tải model, build image hay inference thật trong phiên này. Chỉ chạy static/import và unit test CPU ngắn dùng mock/fixture. Giữ toàn bộ docs, outputs, data, models/cache cũ.

## Đã triển khai

- Contract `independent-batches-v6.1` tại `v6/policy.py`; config `llm` thay `ollama`; chỉ runtime vLLM và detector GLiNER. Config hiện hành đã cập nhật, model/revision Qwen giữ ghim, context 32768, engine 8 sequence, batched tokens 2048. Đây chưa phải profile được qualification GPU.
- Module mới `v6/scene.py`: một request cho toàn bộ essential dialogue/caption, range do code tạo, summary contract, reserve 4096/8192 và tối đa một retry có receipt. Input overflow không chia chapter.
- `v6/anchors.py`, `planning.py`, `batch_contract.py`: snapshot cố định, pool đúng scope/page, known-name ownership, anchor bất biến, soft cap 8 chờ anchor kế tiếp, hard token cut/singleton overflow, reserve CPU và singleton sentinel `others`.
- `v6/scheduling.py`: hàng đợi chung, refill liên tục, admission 1–8 bắt đầu 2, metrics/cooldown/rollback. Cần tăng coverage và rà soát kỹ thuật ở mục còn lại.
- `v6/requests.py`: journal request trước POST, response/hash, retry OOM một lần sau giảm tải/health, giữ budget qua resume, block uncertain/interrupted receipt. `v6/merge.py`: merge source order, evidence tổng hợp bằng code, không feed kết quả batch vào batch khác.
- `v6/independent.py` là analyze entrypoint; nonessential source-only, semantic lỗi unresolved và không model repair. `semantic.py` giữ scan/snapshot/identity/export và dispatch mới.
- Provider vLLM cache tokenization theo exact messages/model/template; planner/provider dùng lại count. Thêm metrics, reserved inference, receipt trước POST, block duplicate uncertain, lưu raw response để replay sau completion.
- Fix calling-event provenance tại `persons.py`: trang/observation recipient thay vì mặc định trang caller, occurrence có offsets và recipient source.
- RunStore chỉ nhận manifest v6 với policy mới, từ chối run/config cũ. CLI bỏ legacy stages, fix duplicate extract parser. OCR-box là selector duy nhất, giữ helper layout/font mà OCR-box còn dùng.
- Chuyển helper `deployment/runpod/v6.py` -> `native_prepare.py`, `sync.py` -> `native_transport.py`; nối native controller/import/upload paths. Bỏ compatibility entrypoint và `runpod-v6.ps1 legacy`; `runpod.ps1` cũng trỏ local_v6.
- Xóa module pipeline cũ/unified/dual-pass, provider Ollama/spaCy, legacy orchestration và diagnostic script phụ thuộc chúng. Giữ speech prompt và visual helpers còn dùng. Xem git diff/status để biết chính xác danh sách.
- Thêm `v6/verification.py`, nối verifier delivery sang contract mới; review stage có scene summary, batches, pool, anchor, exception, unresolved.

## Kiểm chứng đã chạy

Python executable dùng `python` (Windows Python 3.12). Với test cũ đặt `PYTHONPATH=D:/AIP491/manga-translate-v2/src`.

- `tests/test_v6_independent_batches.py`: **19/19 pass**, khoảng 0.3 giây. Scene coverage/retry, overflow, planner hard/soft cuts, pool run/page, independent merge+resume, valid peers/unresolved, adaptive admission/rollback, continuous refill, uncertain/OOM ledger, tokenizer reuse, crash-before-POST/retry guard, recipient provenance/dedup, legacy config rejection.
- `tests/test_v6_pipeline.py`: **14/14 pass**, khoảng 5.7 giây, sau chuyển fixture sang scene/independent response. Nonessential Clench nay source-only (không còn unified classifier), giữ pixel/inpaint/bank/identity/typography fixtures. Không có model inference.
- `tests/test_runpod_native_identity.py`: **3/3 pass**, mock only, khoảng 0.001 giây. Không chạy `test_native_sync_pair.py` vì nó chạy daemon thật.
- Static AST toàn src/deployment: pass ở thời điểm kiểm tra trước các sửa cuối; cần chạy lại một lượt cuối.
- Import pipeline/config/native modules: pass; load cả bốn config hiện hành pass. CLI phát hiện duplicate `extract` đã sửa; cần kiểm tra lại parser sau sửa.

Bug đã sửa: `RunStore.create` import noise `POLICY` che policy v6 làm manifest mới bị từ chối; đổi alias `NOISE_POLICY`.

## Còn phải làm — không được bỏ qua

1. **Test migration/cleanup chưa hoàn tất.** Nhiều test cũ còn import module đã bỏ và dùng config `ollama`/dual-pass. Không chạy broad suite rồi coi collection errors là lỗi môi trường. Rà soát, thay test còn giá trị bằng policy mới; bảo toàn các fixture geometry/registry/native có ích. `test_vllm_inference_policy.py` còn `ollama`, slots 1..4. `test_v6_dual_branch.py`, `test_v6_scene_parallel.py`, `test_v6_semantic_contract.py` có dependency cũ. Các tests names/normalization/translation/provider legacy cần xử lý có chủ đích; chưa archive/xóa chúng trong phiên này.
2. **Native qualification lane cần hoàn thiện.** `local_v6.remote_run`: doctor dừng vLLM rồi concurrency probe hiện chưa start lại server; sửa lifecycle trước/sau probe. Verifier probe đã nhận giới hạn cấu hình thay vì đúng 2, nhưng cần receipt gắn signature/profile và validate đúng khi launch/batch. Không tạo/fake qualification và không chạy GPU để lấy receipt trong task này.
3. `native_transport.py` còn helper `sync`, `push_input`, `fetch_data`/constants workspace từ lane cũ dù không có CLI; rà dependency rồi bỏ helper không còn được native workflow dùng. Không xóa helper bundle/SSH/bootstrap/verification cần thiết. Kiểm tra `code_files`/CODE_FILES có source mới, không tham chiếu script đã xóa.
4. **Detailed review** `review/v6.py` còn trace/header forward/backward/narrator/thread và fallback bubble-body; stage review mới đã đúng nhưng detailed renderer cần cập nhật scene summary/batch/pool/exception/unresolved. `local_review.py` version đã đổi independent policy.
5. README và docs hiện hành chưa cập nhật cho interface mới; không xóa docs lịch sử. Runbook cũ vẫn nói legacy v6.py/sync.py. Bổ sung current notice hoặc hướng dẫn mới rõ ràng, không rewrite lịch sử thành bằng chứng mới.
6. Rà semantic/budget/checkpoint edge cases: validator schema fields/extra references, scene English/sentence heuristic, no duplicate requests khi crash, giữ completed batch khi peer fail, admission preemption/metrics loss/bounds/rollback/health OOM. Thêm fixture concurrency=1 vs8, completion order, known-name ownership, invalid anchors/evidence, failed scene retry không tăng sau resume, corrupt receipts. Tránh test chỉ mirror implementation.
7. Rà verifier mới: coverage/order/plan hash/frozen pool đã có; cần check unique batch IDs, cut exception provenance, không batch result nào feed batch khác. Verify source bank/registry schema/model/geometry giữ được. Đừng biến review Markdown thành stage gate.
8. Còn biến/thông báo tên `ollama` trong pipeline config validator (đã là key llm) và vài selector legacy trong code; chạy static scan để cleanup. Có `if version in {5,6}` ở các helper dù RunStore chỉ cho v6; có thể làm gọn thêm mà giữ integrity checks.
9. Chưa audit toàn bộ Docker/requirements/bootstrap references sau xóa backend cũ. Không build/cài/tải trong task này. Chỉ chỉnh source/config tĩnh.
10. Kiểm tra docs/data/output preservation và static imports/syntax cuối cùng. Không claim toàn bộ test suite pass. Không truy cập hạ tầng thật.

## Cách làm việc cho agent tiếp

Đọc AGENTS.md và skill context-mode. Dùng `ctx_execute`/`ctx_execute_file` để phân tích và chỉ in kết luận. Native `apply_patch` dùng cho ghi file. Context subprocess FS writes không được dùng cho edit source. Chưa có quyền delegate/subagent.

Các tool có thể được discovery từ ALL_TOOLS; names context-mode: `mcp__context_mode__ctx_execute`, `ctx_execute_file`, `ctx_search`, `ctx_batch_execute`. JS ctx cwd là temp; luôn dùng đường tuyệt đối hoặc `child_process` với cwd repo. Tool output lớn sẽ auto-index; không dump full diff/source. Có thể dùng Python difflib qua child_process để tạo patch rồi chia chunk nhỏ, apply bằng native apply_patch. Không có shell curl/wget.

Ưu tiên tiếp: đọc code mới + plan, sửa native offline lane/detailed review, thêm missing regression tests, quyết định migration test legacy, hoàn thiện README/current docs, chạy chỉ các unit/static checks ngắn được phép, rồi ghi báo cáo trung thực.
