# Cấu hình pipeline v7

Nguồn chuẩn: [pipeline.py](../../src/manga_pipeline/pipeline.py), [v6/config.py](../../src/manga_pipeline/v6/config.py) và [config Runpod](../../configs/pipeline-v6-runpod.json). Bốn JSON trong `configs/` đã chuyển sang contract mới. Tên file `pipeline-v6-runpod.json` được giữ vì controller đang sử dụng đường dẫn này.

## Contract bắt buộc

| Trường | Giá trị |
|---|---|
| `analysis_contract` | `chapter-story-graph-v7` |
| `v6.analyze_engine`, `v6.graph_policy` | `chapter-story-graph-v7` |
| `llm.num_ctx`, `llm.analyze_num_ctx` | `65536` |
| `llm.analyze_thinking` | `false`; request policy luôn tắt thinking |
| `llm.backend` | `vllm` |
| `ner.backend` | `llm-scene`; không còn đường chạy GLiNER scan |
| `v6.identity_name_binding_policy` | `analyze-authoritative-v3` |

Không sửa config/snapshot của run đã đóng băng. Đổi source/config thì tạo revision/batch mới.

## Graph, analyze, reanalyze

| Trường | Mặc định | Ý nghĩa |
|---|---:|---|
| `v6.graph_output_tokens` | 16384 | Dự phòng đầu ra graph toàn chapter |
| `llm.graph_timeout` | 1200 giây | Timeout HTTP riêng cho request graph toàn chapter; không retry khi chưa rõ trạng thái |
| `v6.analyze_batch_size` | 8 | Số utterance tối đa/event; không được vượt 8 |
| `v6.scene_concurrency` | 8 | Scene hoạt động đồng thời; còn bị giới hạn bởi runtime LLM slots |
| `v6.event_output_tokens` | 8192 | Dự phòng semantic + mô tả + memory của một event |
| `v6.reanalyze_output_tokens` | 8192 | Dự phòng danh sách correction/event update |
| `v6.event_image_max_side` | 1600 | Cạnh tối đa của ảnh vùng trang được lưu |
| `llm.image_max_side` | 1600 | Cấu hình ảnh của provider |
| `vllm.max_images_per_prompt` | 10 | Giới hạn ảnh/request; không tự bỏ trang khi vượt |
| `llm.timeout` | 600 | Timeout các request thông thường, gồm graph/reanalyze |
| `llm.analyze_timeout` | 1200 | Timeout analyze event |

64K gồm text, ảnh, schema và đầu ra dự phòng. Tokenizer/budget của provider bảo vệ context trước POST. Event quá lớn được ghi lỗi; không tự thay ranh giới hoặc âm thầm bỏ bằng chứng bắt buộc.

Số request analyze thực sự song song bằng số scene đang hoạt động, không phải số event. Trong một scene chỉ một event chạy tại một thời điểm. `v6.scene_concurrency` và `runtime.llm_slots` là hai giới hạn khác nhau.

## Upstream và downstream

| Nhóm | Trường quan trọng |
|---|---|
| Detection | Các trường threshold ở cấp pipeline; giữ default hiện hành trong config |
| Bank | `character_bank.max_distance=0.55`, margin 0, page gap 1, 2–16 vector/crop |
| OCR | `ocr.backend=glm-ocr`, model `zai-org/GLM-OCR`, environment riêng |
| Identity | `v6.min_confirmed_call_events=3`, `v6.identity_min_chapter_crops=8`, visual min-crop `<0.38` |
| Profile | `v6.profile_crops=10`, concurrency 8, output 4096 |
| Relationship | concurrency 8, tối đa 3 crop/MAGI, output 4096 |
| Translation | chỉ natural, concurrency 64, reuse membership các event analyze |

Chưa thêm gate confidence mới cho build_identity. Quyết định này được để lại theo phạm vi thiết kế.

Runpod mặc định L40S Secure, không tự fallback GPU/Community. Việc chuẩn bị config không cấp quyền kết nối hoặc thuê pod; xem `AGENTS.md`.
