# Models, môi trường và tài nguyên

[← Chỉ mục](../README.md)

## Stack mặc định trong `configs/pipeline-v6-runpod.json`

| Thành phần | Model / phiên bản | Vai trò |
|---|---|---|
| Layout | `mayocream/koharu-layout-rfdetr-seg-2xl-1152` | Panel, bubble segmentation/layout |
| Detection/Re-ID | `ragavsachdeva/magiv2` | Text/character/association và embedding |
| OCR | `zai-org/GLM-OCR` | Recognition trên crop text |
| Semantic/translation | `cyankiwi/Qwen3.5-27B-AWQ-4bit` | Scan, analyze, profile, relationship, natural translation |
| LLM engine | vLLM `0.31.0` | Serving đa phương thức và continuous batching |
| Inpaint | `mayocream/aot-inpainting` | Xóa chữ khi không dùng được white fill |
| Font | `assets/fonts/astro-city-teddy-bear.ttf` | Dàn chữ Việt theo config |

Revision đã pin:

- Koharu: `aed55fdb8ca953c6bec33cf6ed6dd52a9b72bfa2`.
- MAGI: `fbc890fec52977142e8ee00bfe26e9458b65517c`.
- GLM-OCR: `2e85a62840ccac27daa451df36c736c4636b8628`.
- AOT: `cffe2346ac2b5ebe1f2d61335d602d12cc144c6f`.
- Qwen checkpoint: `5cba9b5f1bca69fab9b75c88ae7817ae22d7d898`.

`vllm.revision` là revision của **checkpoint LLM trên Hugging Face** trong schema
hiện có, không phải source commit của thư viện vLLM. Phiên bản engine dùng key
`vllm.version`; không nhầm hai pin này khi chuẩn bị model/env.

Không sửa model/revision visual tùy ý: adapter kiểm tra pin và asset receipts.
GLiNER vẫn có trường cấu hình để tương thích backend cũ; `ner.backend=llm-scene`
không chạy GLiNER để scan tên. Hayai còn là backend tương thích; GLM-OCR là mặc
định của run mới. Paddle/PP-OCR là thử nghiệm lịch sử, không phải main backend.

## Tách môi trường

MAGI, Koharu, GLM-OCR và vLLM có dependency không đồng nhất. Runpod setup tạo
các môi trường đã khóa riêng, không cài Transformers mới vào môi trường MAGI.
`requirements-glm-ocr.txt` là môi trường OCR riêng; môi trường visual dùng
RF-DETR/Transformers 5. Các lock/asset receipts trong batch là nguồn đối chiếu
thực thi, không phải môi trường Python đang mở trên PC.

Source, env, model cache, input, bank, output và logs thực thi nằm trong:

```text
/opt/manga-work/project     source đã đóng băng
/opt/manga-work/execution   envs, coordination và tài nguyên thực thi
/opt/manga-work/runtime     cache/data/output runtime
/workspace/manga-runtime/local-v6   archives/publication bền hơn phiên chạy
```

Chỉ đổi `MANGA_EXECUTION_ROOT` mà vẫn chạy runtime ở `/workspace` không đáp ứng
working-set policy. Native Syncthing sao chép live và durable về PC.

## Bốn giới hạn khác nhau

1. `vllm.max_num_seqs=64`: trần sequence của server, không phải bảo đảm có 64
   request 32k cùng chạy được trong VRAM.
2. `runtime.llm_slots`: trần admission chung cho workload; config Runpod chính
   là 64, có thể freeze thấp hơn theo tác vụ/qualification.
3. Giới hạn từng stage: translate tối đa 64; profile và relationship tối đa 8.
4. Batch size: số utterance/target trong một request; analyze tối đa cấu hình
   8 và bị ràng buộc thêm bởi context/evidence, không phải số request đồng thời.

Giới hạn concurrency thực tế bị chặn bởi runtime-wide admission, giới hạn stage
và tình trạng server. Model profile/relationship dùng tối đa 10 ảnh; managed
vLLM đặt `--limit-mm-per-prompt` theo frozen `max_images_per_prompt`, video 0.

## Context và token

Context model được kiểm tra ở **32768** token. Output reserve chiếm cùng context
với text, ảnh và template. Tăng output tokens làm giảm chỗ cho input. Đếm ký tự
không đủ để dự đoán multimodal token budget.

Profile/relationship tokenize toàn bộ message trước POST, giữ ảnh bắt buộc và
đoạn bằng chứng nguyên vẹn; không cắt giữa đoạn. Evidence dư được ghi nhận đã
bỏ. Mandatory evidence quá context tạo lỗi trước inference; không retry model.
Analyze/translation có packing/split theo chính hợp đồng của từng stage.

## GPU và CUDA

Runpod mới chỉ dùng **NVIDIA L40S Secure Cloud** trừ khi người dùng trực tiếp
chọn khác cho tác vụ đó. Không fallback Community/5090 khi thiếu máy. Trước
provision phải đọc stock/giá và xác định CUDA floor từ lock của workload.

Batch thử ngày 2026-10-23 có vLLM lock dùng CUDA 13.0 nên yêu cầu host tối thiểu
13.0; không suy từ tên boot image `cu128` rằng host 12.8 đủ. CUDA floor là thuộc
tính batch/lock hiện tại, không phải hằng số cho mọi lần chạy tương lai.

Boot image của batch thử là `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`;
không cần build/push image lớn mới nếu môi trường đóng băng đã tương thích.
Giá thuê và stock luôn phải đọc lại khi chạy; tài liệu không cam kết giá cố định.
