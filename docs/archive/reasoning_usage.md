> Historical: describes the pre-refactor pipeline. Current usage is in ../news/usage.md.

# Reasoning scene và Stage 3: hướng dẫn chạy

Pipeline mới dùng một VLM qua các lượt: đọc bằng chứng ảnh, phân scene, sửa speaker,
resolve ID/name, resolve addressee, và sửa thứ tự trong cùng scene/trang. Không dùng
embedding clustering, không tự sửa character bank và chưa xây đồ thị quan hệ.

## Chạy từ ảnh gốc đến hết Stage 3

```powershell
.\venv\Scripts\python.exe -B src/run_chapter.py --output-dir outputs/chapter1-stage3 --profile cpu
```

Không truyền thư mục ảnh thì đọc `DEFAULT_IMAGE_FOLDER` trong `src/main.py`.
Có thể truyền một thư mục ảnh khác trước các flags. MAGI chạy ở tiến trình riêng,
kết thúc và giải phóng RAM trước khi reasoning bắt đầu. Bank hiện có được sao chép
vào thư mục output; Stage 1 cập nhật bản sao đó, Stage 3 chỉ đọc.

Trong thư mục output:

- `stage1/transcript.txt`, `stage1/transcript.raw.json`, `stage1/visualizations/`:
  đầu ra MAGI và ảnh kiểm tra; `stage1/magi.log` ghi log.
- `stage2/dialogue.normalized.json`: lời thoại sau normalization.
- `stage3/structured_dialogue.json`: kết quả cuối đến hết Stage 3.
- `check.json`: một file tổng hợp trạng thái và bản chụp đầy đủ sau Stage 1,
  normalization, visual observations, scene segmentation, 3A, 3B, 3C, 3D,
  local rerun nếu có và kết quả cuối. `steps.<step>.output` là dữ liệu để so sánh.
- `internal/cache/`: cache request phục vụ resume, không phải nhãn đã xác nhận.
- `character_banks/`: bank riêng của lượt chạy.

`check.json` được thay thế atomically sau mỗi bước và cập nhật task hiện tại giữa
các request. Khi chưa hoàn tất, output cuối chưa được tạo. Nếu lỗi/gián đoạn:

```powershell
.\venv\Scripts\python.exe -B src/run_chapter.py --output-dir outputs/chapter1-stage3 --profile cpu --resume
```

Resume kiểm tra ảnh nguồn không thay đổi, bỏ qua MAGI nếu extraction đã hoàn tất
và dùng lại request cache đã xác thực. Output cuối đã tồn tại thì chọn thư mục mới.

## Chuẩn bị

Dùng môi trường Python của dự án. Client HTTP/schema/checkpoint dùng standard
library; xử lý ảnh dùng Pillow đã có. Không nâng Transformers hoặc cài thêm model
vào môi trường MAGI. MAGI và reasoning chạy ở các tiến trình riêng, tuần tự.

Cài model mong muốn trong Ollama trước. Pipeline không tự tải hoặc tự đổi model:

```powershell
ollama pull gemma4:e4b-it-q4_K_M
# Chỉ tải trên máy bạn sẽ dùng để thử các ứng viên GPU:
ollama pull qwen3.5:9b
ollama pull qwen3.5:27b
```

Các profile `cpu`, `gpu16`, `gpu48` là cấu hình thử nghiệm, không bảo đảm vừa RAM/VRAM
ở mọi số ảnh/context. Chỉnh các JSON trong `configs/` hoặc dùng CLI overrides.
Không nạp đồng thời cả ba model. Mỗi lần chạy chỉ chọn một tag và ghi digest của nó.

## Dữ liệu MAGI

Sau Stage 1–2 cần `chapter.raw.json` và `chapter.normalized.json`. Pipeline tự tìm
raw tương ứng theo tên `.normalized.json`, hoặc theo `raw_json_path` đã lưu.
Fingerprint raw phải khớp normalized; khi dữ liệu thay đổi, normalize lại.

```powershell
python src/dialogue_pipeline.py reason chapter.normalized.json --raw chapter.raw.json --bank character_banks/story/metadata.json --profile cpu --output runs/chapter.structured.json
```

`--bank` là tùy chọn đọc metadata; pipeline không ghi bank. Ảnh được đọc từ raw JSON,
crop panel trong bộ nhớ và resize theo profile; không sửa ảnh gốc. Panel không có
lời thoại cũng được xử lý để tránh bỏ mất dấu hiệu chuyển cảnh.

## Nhập utterance JSON có sẵn

```powershell
python src/dialogue_pipeline.py import-utterances input.json --document-id chapter-1 --output runs/chapter-1.raw.json
python src/dialogue_pipeline.py normalize runs/chapter-1.raw.json --output runs/chapter-1.normalized.json
python src/dialogue_pipeline.py reason runs/chapter-1.normalized.json --images . --profile cpu --output runs/chapter-1.structured.json
```

Thư mục `--images` cần `page_1.png`, `page_2.png`, ... khớp trường `page` trong input.
Adapter này đọc ảnh cả trang, không bịa panel/bbox/cluster khi input không có chúng.
Tên ảnh trùng cùng số trang hoặc thiếu trang được báo lỗi. Speaker IDs nhập ngoài
giữ nguyên kiểu; không tự đổi chuỗi thành ID integer trong bank.

## Kiểm tra kế hoạch trước khi chạy

```powershell
python src/dialogue_pipeline.py reason chapter.normalized.json --raw chapter.raw.json --profile cpu --dry-run --output runs/chapter.plan.json
```

Dry-run không liên hệ Ollama. Nó kiểm tra nguồn và ghi profile, số utterance,
panel kể cả panel im lặng, và phạm vi các stage. Giới hạn từng request được kiểm tra
khi chạy, không suy ra tổng RAM từ kích thước file kế hoạch.

## Chạy trên máy thuê

```powershell
python src/dialogue_pipeline.py reason chapter.normalized.json --raw chapter.raw.json --profile gpu16 --output runs/chapter.gpu16.json
python src/dialogue_pipeline.py reason chapter.normalized.json --raw chapter.raw.json --profile gpu48 --config configs/reasoning.gpu48.json --output runs/chapter.gpu48.json
```

Có thể override `--model`, `--host`, `--num-ctx`, `--num-predict`, `--max-targets`,
`--max-images`, `--timeout`, `--think`/`--no-think`, `--unload-after`/`--no-unload-after`.
Model được unload sau khi kết thúc theo mặc định. Server phải hỗ trợ vision, và
thinking khi yêu cầu `--think`. Không tải thêm model trong lúc chạy pipeline.

`text_bytes_per_token` mặc định 1.0 dùng số byte UTF-8 làm upper bound cho text.
`configs/reasoning.chapter-cpu.json` là profile riêng cho lượt Chapter 1 chạy thật:
ước lượng 2 byte/token, reserve 1024, vẫn context 8192 và một ảnh/request. Ước lượng
này cho phép batch lớn hơn nhưng không phải upper bound; kiểm tra prompt usage
thực tế vẫn từ chối request vượt ngân sách và giảm batch trước khi cache kết quả.
Profile/config thực tế được ghi trong kết quả; không so timing hai profile như
cùng một cấu hình.

## Batch, checkpoint và chạy tiếp

Chương là đơn vị dữ liệu; mỗi request nhận một cửa sổ có overlap, ảnh liên quan,
metadata và candidates cần thiết. Không gửi lại toàn bộ JSON chương vào mọi batch.
Text budget dùng UTF-8 bytes như ước lượng bảo thủ; ảnh có reserve riêng. Khi vượt
ngân sách, dùng view gọn trước khi giảm target/context: bỏ metadata lặp, chỉ giữ
hypotheses của các panel trong cửa sổ và làm tròn geometry prompt tới 0.01 pixel.
Nguồn và output vẫn giữ tọa độ gốc; panel im lặng nằm giữa các anchor vẫn có context.
Request scene không mang bảng nhận diện nhân vật đầy đủ; Stage 3 vẫn dùng candidates.
Khi lỗi tài nguyên, giảm target/context, và giảm ảnh khi phù hợp. Request
nhỏ nhất vẫn không vừa sẽ báo lỗi; không cắt mất utterance để giả vờ thành công.

Cache mặc định ở `runs/reasoning/cache/`, checkpoint stage ở
`runs/reasoning/checkpoints/<run-id>/`. `--work-dir` đổi vị trí này. Request chỉ được
cache sau khi qua schema, coverage, candidate và evidence validation. Cache key
gồm model digest, prompt/schema, options, source context và ảnh đã encode.

Nếu bị gián đoạn, chạy lại cùng lệnh với một output chưa tồn tại; request hợp lệ
trong cache được dùng lại. Cache hỏng hoặc không còn hợp lệ sẽ được kiểm tra lại.
Manifest failed ghi giai đoạn/error; không ghi đè nguồn hoặc output đã tồn tại.

## Đọc kết quả

- `speaker_id`: identity candidate ổn định; `speaker_name`: tên riêng nullable.
- `original_speaker_id`, `original_speaker_cluster_id` và `original_association`
  giữ dự đoán MAGI để so sánh. Nguồn text/box/ID không bị thay đổi.
- `speaker_status`, `speaker_name_status`, `addressee_status`, `scene_status`,
  `order_status`, `review_flags` phân biệt predicted/confirmed/unknown/needs_review.
- `identity_proposals` lưu tên mới cần duyệt. Chỉ tên khớp metadata bank được cung
  cấp mới được đánh dấu confirmed; tên model đề xuất không tự cập nhật bank.
- Nếu model đưa candidate cùng status unknown/needs_review, các trường
  `proposed_speaker_*`/`proposed_addressee_*` giữ candidate để duyệt. ID speaker có
  hiệu lực được để null, người nghe có hiệu lực là unspecified/[] khi chưa giải quyết.
  Đề xuất người nghe chưa chắc chắn có thể chưa đủ ID cho type; giữ nguyên trong
  reasoning để duyệt. Dự đoán có hiệu lực vẫn phải khớp số lượng ID/type, và mọi
  ID kể cả trong đề xuất phải thuộc candidates đã cung cấp.
- `original_order` và `corrected_order` lưu thứ tự trước/sau. 3D chỉ được hoán vị
  trong cùng scene/trang. `ordering_audit` ghi đề xuất và bằng chứng.
  Nhóm scene/trang chỉ có một utterance dùng `order_status: not_applicable`.
- `local_rerun_count` tối đa 1; sau reorder chỉ chạy lại 3A/3C vùng ảnh hưởng,
  đồng bộ 3B khi candidate identity thay đổi. Không chạy vòng sửa scene.
- `reasoning_run` chứa config, model digest, ảnh nguồn, phiên bản prompt/schema,
  usage/timing và số cache hit. Điểm tự tin model không phải xác suất đã hiệu chuẩn.

Consumer dịch phải đọc status, không dùng tên/speaker/listener chưa xác nhận như
dữ kiện chắc chắn. Uncertain boundary được soft-merge với needs_review và không
chặn cứng context. Lời nói ngoài khung hình không bị ép gán vào người đang nhìn thấy.

## Pilot annotation và đánh giá

```powershell
python src/dialogue_pipeline.py review-reasoning chapter.normalized.json --output runs/chapter.reference.json
```

Template trống, không copy dự đoán model và không tự xác nhận. Duyệt từ ảnh/text gốc,
điền boundary và các trường speaker/name/addressee/order cùng cờ `*_reviewed`.
`boundary: null` là chưa rõ. `speaker_reviewed: true` cùng `speaker_id: null` nghĩa
là thực sự không xác định. `corrected_order` là vị trí global, zero-based. Chỉ sau
duyệt độc lập mới tự đặt `confirmed: true` và tên `reviewer`.

```powershell
python src/dialogue_pipeline.py evaluate-reasoning chapter.normalized.json --prediction runs/chapter.structured.json --reference runs/chapter.reference.json --output runs/chapter.metrics.json
```

Chấm masked boundary F1, speaker/identity/name, addressee type/ID sets, ordering
pairs, false corrections và tỷ lệ cần review. Chưa có bộ pilot đầy đủ thì không
kết luận winner, chất lượng production hoặc mức cải thiện bản dịch.

Các ablation chạy riêng với cùng ngân sách và nguồn:

```powershell
# Không có explicit scene segmentation:
python src/dialogue_pipeline.py reason chapter.normalized.json --scene-source none --output runs/no-scenes.json
# Có scene reasoning nhưng giữ thứ tự ban đầu:
python src/dialogue_pipeline.py reason chapter.normalized.json --no-order-correction --output runs/no-order-fix.json
# Chỉ phân scene:
python src/dialogue_pipeline.py reason chapter.normalized.json --scenes-only --output runs/scenes.json
# Oracle: chỉ boundary gold đi vào phân scene; gold speaker/addressee không vào prompt:
python src/dialogue_pipeline.py reason chapter.normalized.json --scene-source gold --reference runs/chapter.reference.json --output runs/gold-scenes-oracle.json
# Text-only baseline (cần ghi rõ là ablation):
python src/dialogue_pipeline.py reason chapter.normalized.json --text-only --output runs/text-only.json
```

`--reference` bị từ chối trên predicted/none runs để tránh vô tình đưa gold vào
inference. Labels evaluation phải khớp fingerprint normalized source. Addressee
nguồn được giữ riêng và loại khỏi các fields đang suy luận trước Stage 3.

## Benchmark nhiều chương/model tuần tự

Sửa `configs/reasoning.benchmark.example.json` thành manifest với các đường dẫn
thực và labels đã duyệt. Đường dẫn tương đối tính từ thư mục manifest. Có thể thêm
model configurations với tên riêng, profile, model tag và config JSON.

```powershell
python src/dialogue_pipeline.py benchmark-reasoning configs/reasoning.benchmark.example.json --output runs/pilot-benchmark.json
```

Lệnh kiểm tra chapter IDs, đường dẫn và nội dung trùng giữa các entries trước khi
gọi model. Các chương development/test phải tách biệt. Gold-scene variant cần
boundary labels đầy đủ; bỏ variant đó khi còn boundary ambiguous. Mỗi case được
chạy tuần tự và checkpoint, với model được unload theo profile sau mỗi case.
Case failed được ghi riêng; summary incomplete trả exit code 1. Không tự chọn
winner/ngưỡng production và không tự tune prompt bằng test labels.

## Kiểm tra

```powershell
python -B -m unittest discover -s tests -v
# Opt-in smoke với Ollama thật, 2 utterance và ảnh nguồn:
python -B tests/smoke_reasoning_live.py input.json --images . --output runs/live-smoke.json
```

Smoke thật kiểm tra transport/schema/provenance, không thay thế benchmark accuracy.
File `.status.json` cạnh output báo tiến độ và lỗi; các request đã hợp lệ giữ trong cache.

## Giới hạn bản đầu

Reading-order sai có thể làm phân scene sai trước Stage 3D. 3D không tự thay scene;
các ca này cần review. Cửa sổ hữu hạn, mô tả ảnh và candidate shortlist có thể bỏ
lỡ bằng chứng xa/nhân vật chưa biết. Khi request không còn vừa hoặc model không
đáp ứng contract sau retry, pipeline dừng với checkpoint thay vì tự bịa kết quả.
Các block ordering lớn được xử lý cục bộ cùng seam; đây chưa phải tối ưu toàn cục.
Chưa fine-tune, chưa đồ thị quan hệ, chưa QA dịch; các bản sửa đã duyệt là nguồn
dữ liệu cho bước phát triển tiếp theo.
