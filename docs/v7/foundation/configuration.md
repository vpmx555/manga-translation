# Danh mục config v7

[← Chỉ mục](../README.md)

## Cách nạp và chỉnh

Base mặc định nằm trong `src/manga_pipeline/pipeline.py` và defaults của từng
module. JSON được merge theo section, key không biết bị từ chối. Tài liệu dưới
lấy **`configs/pipeline-v6-runpod.json` sau khi merge defaults** làm cấu hình
Runpod tham chiếu, không coi mọi giá trị trong Python defaults đều giống file này.

Đường dẫn vẫn tên `pipeline-v6-runpod.json`, section vẫn `v6`; chưa có parser
cho section `v7`. Khi truyền file config phải giữ
`analysis_contract=independent-batches-v6.1`. Không dùng các JSON Ollama/legacy.

Quy trình thay đổi: copy JSON sang file mới → sửa các key cần thiết → kiểm tra
`load_config`/prepare tĩnh → freeze **batch mới** → đối chiếu effective
`pipeline-v6.json` trong batch. CLI prepare có thể override runtime/slots nên
không chỉ nhìn file đầu vào. RunStore snapshot config; resume không nhận thay
đổi ngầm của file ngoài.

Trong bảng: **khóa policy/pin** là giá trị validator yêu cầu, không phải nút
tuning. **Tuning** vẫn cần run/batch mới nếu ảnh hưởng snapshot. Một số key còn
ở schema tương thích nhưng không điều khiển đường thực thi mới, ghi rõ bên dưới.

## Root và nguồn

| Key | Runpod tham chiếu | Ràng buộc / tác dụng |
|---|---|---|
| `analysis_contract` | `independent-batches-v6.1` | Khóa contract |
| `dialogue_analysis` | `essential-v3` | Khóa cho run mới |
| `speaker_policy` | `individual-v1` | Khóa policy |
| `device` | `cuda` | `auto`, `cpu`, `cuda`; extraction device |
| `visualizations` | `true` | Giữ ảnh review; không tắt chỉ để giảm output |

Story/chapter/input/output/bank roots là tham số CLI, không thêm key tùy ý vào
JSON. Nguồn chapter mặc định tìm dưới `D:\download\EN-data` khi người dùng
không chỉ nguồn khác.

## Layout và detection

| Key | Giá trị | Miền validator / tác dụng |
|---|---:|---|
| `layout.enabled` | true | Run mới yêu cầu Koharu bật |
| `layout.python` | null | Interpreter riêng hoặc null; Runpod setup quản lý |
| `layout.model`, `revision` | Pin Koharu | Khóa adapter; xem models guide |
| `layout.model_root` | `<project>/models/visual` | Đường dẫn asset; Runpod relocate theo runtime |
| `layout.device` | auto | auto/cpu/cuda |
| `layout.panel_threshold` | 0.50 | `(0,1]` |
| `layout.bubble_threshold` | 0.45 | `(0,1]` |
| `layout.crop_padding` | 24 | int0..256 pixel |
| `layout.mapping_coverage` | 0.80 | `(0,1]`; mapping hình học |
| `layout.mapping_margin` | 0.15 | `(0,1]`; ambiguity margin |
| `text_detection_threshold` | 0.45 | `[0,1]` |
| `character_detection_threshold` | 0.30 | `[0,1]` |
| `character_character_matching_threshold` | 0.81 | `[0,1]`; MAGI matching |
| `text_character_matching_threshold` | 0.25 | `[0,1]`; association thô |
| `text_classification_threshold` | -0.5 | Số hữu hạn; ngưỡng logit |

Giảm detection threshold có thể tăng recall và tăng false positive; không đồng
nghĩa nhân vật mới chắc chắn được nhận đúng vào bank.

## Character bank

| `character_bank.*` | Giá trị | Có thể chỉnh bằng JSON? |
|---|---|---|
| `max_distance` | 0.55 | Có, finite0..2; khoảng cách hậu matching |
| `min_margin` | 0 | Có, finite0..2; độ tách ứng viên |
| `pending_max_page_gap` | 1 | Hiện khóa1 |
| `prototype_strategy` | `closest-2-to-16-paired-mean-v2` | Khóa policy |
| `assignment_strategy` | `page-frozen-frequent-id-then-distance-v1` | Khóa policy |
| `min_embeddings_per_character`, `min_crops_per_character` | 2 | Khóa2; nếu có đủ quan sát |
| `max_embeddings_per_character`, `max_crops_per_character` | 16 | Khóa16 |

Không đổi count/strategy chỉ trong JSON: validator chủ động từ chối. Muốn đổi
phương pháp cần code/policy/version và diagnostic có seed bank kiểm soát.

## OCR, NER và noise

| Key | Giá trị | Tác dụng |
|---|---|---|
| `ocr.backend` | `glm-ocr` | Mặc định mới; Hayai chỉ compatibility |
| `ocr.engine` | `core` | Giữ theo adapter mặc định |
| `ocr.batch_size` | 8 | Crop batch OCR; không phải request slots |
| `ocr.model`, `revision` | GLM-OCR pin | Không thay model khác mà giữ nguyên adapter |
| `ocr.max_new_tokens` | 2048 | Output cap OCR |
| `ocr.max_num_patches` | 384 | Số nguyên dương; trường compatibility của OCR schema, không đồng nghĩa crop batch GLM |
| `ocr.python` | null | Interpreter OCR riêng; setup/env chọn backend tương ứng |
| `ner.backend` | `llm-scene` | Scan tên cùng chia scene |
| `ner.model`, `revision` | GLiNER pin lịch sử | Chỉ có tác dụng với backend gliner |
| `ner.threshold` | 0.85 | `(0,1)`, GLiNER compatibility |
| `ner.device` | cpu | auto/cpu/cuda, GLiNER compatibility |
| `noise_filter.policy` | `whole-box-rules-v1` | Khóa policy |
| `noise_filter.rules_path` | null | File rules riêng hoặc null |

Validator OCR nằm trong `extraction/hayai.py` và contract GLM trong
`extraction/glm_ocr.py`; backend name không quyết định dùng chung môi trường.
Đổi batch OCR không chữa lỗi text crop/missing detection.

## LLM và vLLM

| `llm.*` | Giá trị | Miền / ghi chú |
|---|---|---|
| `backend` | `vllm` | Khóa |
| `host` | `http://127.0.0.1:8000` | Local service trong pod; không public port mặc định |
| `model` | `cyankiwi/Qwen3.5-27B-AWQ-4bit` | Workload pin/qualification phải khớp |
| `num_ctx` | 32768 | Validator vLLM khóa32768 |
| `num_predict` | 2048 | Default reserve, số nguyên dương |
| `scene_num_predict` | 4096 | Key có trong schema; scene path hiện hardcode8192 |
| `analyze_num_predict` | 12288 | Tổng output thinking + JSON; planner dự trữ toàn bộ |
| `analyze_thinking` | true | Chỉ analyze bật thinking; các stage khác tắt |
| `analyze_num_ctx` | 65536 | Context riêng analyze; server vLLM chạy ceiling64k, stage khác32k |
| `analyze_timeout` | 1200 | Giây cho mỗi request analyze; không đổi timeout stage khác |
| `analyze_temperature` | 1.0 | Chỉ khi analyze bật thinking; stage khác dùng `temperature` |
| `analyze_presence_penalty` | 1.5 | Chỉ khi analyze bật thinking; hạn chế lặp trong suy luận |
| `translation_num_predict` | 2048 | Default stage reserve; parallel path truyền reserve tính theo target count |
| `image_max_side` | 768 | Số nguyên dương; giới hạn ảnh provider |
| `temperature` | 0 | 0..2 |
| `top_k` | 20 | int1..100 |
| `top_p` | 0.95 | 0.01..1 |
| `presence_penalty` | 0 | -2..2 |
| `compact_context` | true | Khác Python base defaultfalse; frozen config quyết định |
| `require_full_gpu` | true | Preflight xác minh GPU |
| `timeout` | 600 | Giây, số nguyên dương |
| `retries` | 0 | Validator0..3; stage v2 vẫn cấm retry |
| `timeout_retries` | 0 | Validator0..1; không bật để replay uncertain v2 |

| `vllm.*` | Giá trị | Miền / ghi chú |
|---|---|---|
| `version` | 0.31.0 | Phiên bản engine bị khóa |
| `revision` | 5cba9b5f1bca69fab9b75c88ae7817ae22d7d898 | HF checkpoint revision của LLM, SHA40; không phải commit source vLLM |
| `quantization`, `dtype` | compressed-tensors / bfloat16 | Khóa engine profile |
| `gpu_memory_utilization` | 0.94 | 0.5..0.95; KV/cache headroom |
| `max_num_batched_tokens` | 2048 | int512..num_ctx; scheduler token budget |
| `max_num_seqs` | 64 | Chỉ8,32,64; không phải proof concurrency |
| `max_images_per_prompt` | 10 | int10..32; ít nhất đủ profile10 crop |
| `startup_timeout` | 600 | int30..1200 giây |

Đổi engine parameters thay signature/qualification; không reuse proof cũ khi
fingerprint không khớp. Tổng output reserve cần chừa input context; input ảnh
cũng tiêu token. Tăng context không được hỗ trợ chỉ bằng sửa `num_ctx` JSON.

## Runtime chung

| `runtime.*` | Runpod tham chiếu | Miền / tác dụng |
|---|---|---|
| `enabled` | true | Coordination Linux; Windows không chạy batch GPU coordinator |
| `preset`, `required_gpu` | l40s / L40S | Theo GPU policy hiện hành |
| `story_workers` | 1 | 1..8; truyện đồng thời |
| `llm_slots` | 64 | 1..64; trần request chung; có thể freeze thấp hơn |
| `typeset_workers` | null → preset2 | 1..16 hoặc null |
| `num_thread` | null → preset8 | 1..32 hoặc null; còn giới hạn theo CPU headroom |
| `cpu_threads` | null → preset4 | 1..32 hoặc null |
| `adaptive_batches` | true | Adaptive resource limits |
| `analyze_max_batch`, `translation_max_batch` | 8 / 8 | int1..8; không thay policy partition bằng key này |
| `context_fraction` | 0.85 | 0.5..0.95; context safety budget |
| `recovery_attempts` | 2 | int0..3; không override no-retry của stage mới |
| `gate_timeout` | 600 | int30..7200 giây |
| `gpu_sample_seconds` | 10 | int5..60 giây |

Preset L40S có fallback values riêng; resolved config chỉ thay các trường null,
không mặc định ghi đè `llm_slots=64` thành8. Kiểm tra effective batch khi prepare
có `--llm-slots`. Bốn thứ khác nhau: stories, requests, targets/request, server
sequences; không gọi chung là “batch16/64”.

## Geometry và analysis trong section `v6`

| `v6.*` | Giá trị | Miền validator |
|---|---:|---|
| `enabled` | true | bool; main path hiện hành |
| `mask_merge_iou` | 0.35 | `(0,1]` |
| `mask_containment` | 0.85 | `(0,1]` |
| `repair_radius` | 2 | int0..16 |
| `neck_width_ratio` | 0.035 | `(0,1]` |
| `min_body_area` | 2500 | int>=1 |
| `max_reconstructed_fraction` | 0.20 | `(0,1]` |
| `hint_acceptance` | 0.80 | `(0,1]`; không phải calibrated probability |
| `min_confirmed_call_events` | 2 | int>=1; person/name evidence policy |
| `analyze_batch_size` | 8 | int1..8; soft planning boundary |
| `caption_max_distance` | 7 | int1..64 |
| `context_image_max_side` | 384 | int128..768 |
| `analyze_engine` | independent-batches-v6.1 | Khóa |
| `speaker_merge_policy` | inference-first-v1 | Khóa |

## Identity, profile và relationship trong section `v6`

Mặc định mới dùng `v6.scan_policy=canonical-scan-v4` và
`v6.scan_output_tokens=8192` (int 1..16384). Scan một request, không retry.
`scan_policy=null` giữ đường scan legacy cho cấu hình lịch sử.

| `v6.*` | Giá trị | Miền / phạm vi |
|---|---:|---|
| `identity_relationship_policy` | identity-relationship-v2 | Khóa DAG mới |
| `identity_name_binding_policy` | analyze-authoritative-v3 | Đăng ký bằng code từ analyze + scan; v1/v2/null chỉ dành cho lịch sử |
| `identity_min_chapter_crops` | 8 | int1..100000; số detection thật của từng MAGI trong chapter để tạo identity có tên mới; không phải số crop giữ trong gallery |
| `identity_binding_concurrency` | 8 | int1..8; một queue cho mọi group/child |
| `identity_binding_output_tokens` | 4096 | int1..8192; reserve cho tokenize/chia child trước POST |
| `identity_visual_min_crop` | 0.38 | finite `(0,2)`; so sánh strict `<` |
| `profile_crops` | 10 | int1..10; crop gần prototype main member |
| `profile_concurrency` | 8 | int1..8; không vượt runtime slots |
| `profile_output_tokens` | 4096 | int1..8192 |
| `relationship_min_appearances` | 8 | int1..100000; unique chapter detections cho eligibility |
| `relationship_crops_per_magi` | 3 | int1..3; packet relationship theo policy |
| `relationship_concurrency` | 8 | int1..8; không vượt runtime slots |
| `relationship_output_tokens` | 4096 | int1..8192 |

Đổi số crop profile không thay số crop bank lưu. Đổi minimum appearances không
thay lifetime count chọn main member. Không có key “retry profile” được hỗ trợ;
hai stage này không retry kể cả runtime recovery chung khác0.

## Translation và typeset

| Key | Giá trị | Miền / tác dụng |
|---|---:|---|
| `translation.policy` | vi-natural-v2 | Khóa natural-only |
| `translation.batch_size` | 8 | Validator1..8; không repartition analyze-aligned jobs |
| `translation.context_radius` | 2 | int0..10; source rows lân cận |
| `translation.num_predict` | 2048 | Validator1..16384; parallel path dùng reserve theo target count thay key này |
| `translation.glossary_path` | null | File glossary hoặc null |
| `translation.overrides_path` | null | File manual natural targets hoặc null |
| `translation.instructions` | chuỗi rỗng | Chỉ dẫn dịch bổ sung |
| `v6.translation_concurrency` | 64 | int1..64; cùng runtime slots |
| `v6.typeset_mode` | ocr-box | Khóa; mode experimental không phải default |
| `v6.font_path` | assets/fonts/astro-city-teddy-bear.ttf | string hoặc null; kiểm tra glyph Việt |
| `v6.font_min_px`, `font_max_px` | 10 / 48 | int>=1, min<=max<=256 |
| `v6.line_spacing` | 1.6 | 1..2 |
| `v6.body_margin_px` | 10 | int>=1; geometry/layout theo mode |
| `v6.body_margin_ratio` | 0 | 0..1; theo mode |
| `v6.shorten_attempts` | 2 | int0..3; rút gọn khi không fit |

## Inpaint

| `inpaint.*` | Giá trị | Miền |
|---|---:|---|
| `enabled` | true | Python basefalse; Runpod JSON bậttrue |
| `model`, `revision` | Pin AOT | Khóa adapter |
| `model_root` | `<project>/models/visual` | Asset path được setup relocate |
| `device` | auto | auto/cpu/cuda |
| `dilation` | 4 | int0..32 |
| `max_side` | 1024 | int64..1024 |
| `white_min` | 245 | int0..255 |
| `white_fraction` | 0.98 | finite0..1 |
| `white_std` | 5.0 | finite0..255 |
| `white_chroma` | 8 | int0..255 |

## Chọn nhóm tham số theo mục tiêu

- Thêm/giảm detection: các threshold MAGI/Koharu; review crop và false positives.
- Hạn chế tách bank: `character_bank.max_distance`, margin; giữ seed bank cố định
  khi so sánh, đừng nhầm với visual identity threshold.
- Giảm áp lực VRAM: slots, stage concurrency, ảnh/crop và output reserve trong
  giới hạn contract; không hạ integrity/qualification checks.
- Thay cách xưng hô: evidence/profile/graph policy và dữ liệu; không sửa một edge
  hai chiều chung vì graph mới đã dùng hai hướng độc lập.
- Chất lượng tiếng Việt: glossary, manual overrides, instructions, source OCR;
  typeset font/margins chỉ giải quyết trình bày.

Nguồn validator: `pipeline.py`, `v6/config.py`, `bank/config.py`,
`visual/config.py`, `runtime/resources.py`, `translation/stage.py` và OCR adapter.
Nếu bảng khác effective JSON/validator của revision được chạy, revision đó là
nguồn thực thi; cập nhật tài liệu khi thay default hoặc policy.
