# Kết quả triển khai reasoning scene và Stage 3

Ngày kiểm tra: 2026-10-01. Thiết kế được xác nhận nằm tại
`docs/reasoning_scene_stage3_design.md`; hướng dẫn đầy đủ tại
`docs/reasoning_usage.md`.

## Đã triển khai

- Scene reasoning từ ảnh/text qua observations và quyết định từng ranh giới,
  bao gồm panel im lặng khi dữ liệu MAGI có panel. Ranh giới chưa rõ được lưu để
  review, không ép thành kết luận chắc chắn.
- Stage 3A sửa speaker association; 3B resolve ID/name riêng; 3C resolve addressee;
  3D sửa thứ tự trong cùng scene và cùng trang. 3D không sửa scene membership.
- Sau khi đổi thứ tự, chạy lại 3A/3C vùng ảnh hưởng tối đa một lượt và đồng bộ 3B
  khi identity thay đổi. Kiểm tra coverage, nguồn text/geometry và tính nhất quán.
- Chương là đơn vị chạy; inference dùng cửa sổ overlap có ngân sách context/ảnh,
  giảm batch/context khi cần và chạy tuần tự. Cache được xác thực và checkpoint
  lưu riêng theo nguồn/config/model digest.
- Profiles CPU/GPU16/GPU48 có thể chỉnh bằng JSON hoặc CLI. Chỉ sử dụng một model
  tại một thời điểm; không tự tải model, không ghi character bank.
- Nhãn duyệt độc lập, evaluation masked và benchmark nhiều chương/model/ablation.
  Development/test không được trùng chương; gold speaker/addressee không vào prompt.
- `unknown` và `needs_review` được giữ rõ ràng. Candidate chưa chắc chắn nằm trong
  trường `proposed_*` và lịch sử reasoning; public speaker ID là null và public
  addressee là unspecified/[] khi chưa giải quyết. ID bịa hoặc dự đoán có hiệu lực
  sai cardinality vẫn bị từ chối.

Các module chính nằm trong `src/reasoning_*.py`, `src/scene_reasoning.py`,
`src/dialogue_reconstruction.py`, `src/dialogue_ordering.py`. CLI tích hợp tại
`src/dialogue_pipeline.py`. Không thêm dependency cho client/schema/checkpoint;
Pillow của môi trường hiện tại dùng để xử lý ảnh.

## Kiểm tra thực tế

`venv/Scripts/python.exe -B -m unittest discover -s tests -v`: **43 tests đạt**.
Bao gồm source preservation, chống ID bịa, giới hạn hoán vị, local rerun,
batch/resource fallback, cache/resume, semantic repair, uncertainty, nhãn độc lập,
gold leakage và benchmark failure/isolation. Phần lớn là contract tests dùng
provider xác định; không dùng chúng để tuyên bố accuracy của model.

CLI được chạy trên `D:/download/utterances_selected.json`: import và normalize
giữ 36 utterance; dry-run nhận 11 trang ảnh, context CPU 8192 và đầy đủ các stage.
Artifacts:

- `runs/reasoning/sample36.raw.json`
- `runs/reasoning/sample36.normalized.json`
- `runs/reasoning/sample36.plan.json`
- `runs/reasoning/sample36.reference.blank.json` — chưa duyệt, chưa confirmed.

Smoke với Ollama thật, `gemma4:e4b-it-q4_K_M`, hai utterance và ảnh nguồn đã đạt:

- `runs/reasoning/live-smoke-20261001.json`: lượt hoàn tất dùng 4 cache hit và
  1 request addressee thật, khoảng 103 giây. Các bước visual/boundary/speaker/identity
  trong cache đến từ những request thật đã được xác thực ở các lượt trước.
- `runs/reasoning/live-smoke-final-20261001.json`: chạy lại sau sửa status của nhóm
  singleton, 5 cache hit, 0 inference request mới; kiểm tra resume và kết quả cuối.
- File `.status.json` cạnh mỗi output ghi kết quả passed. Kết quả cuối giữ cả hai
  speaker là unknown/null và addressee unknown/unspecified. Hai scene/trang đơn
  không cần hoán vị; trường hợp 3D đổi thứ tự được kiểm tra bằng contract tests.

Smoke xác nhận transport/schema/provenance/resume, **không xác nhận accuracy**.
Thời gian trên là lượt có cache, không phải benchmark cold-start cho cả chương.
`git diff --check` không có lỗi whitespace.

## Lệnh chạy trên mẫu hiện có

Chạy từ thư mục gốc dự án, sau khi Ollama đã có model:

```powershell
.\venv\Scripts\python.exe -B src/dialogue_pipeline.py reason runs/reasoning/sample36.normalized.json --images . --profile cpu --output runs/reasoning/sample36.structured.json
```

Lệnh này chạy inference toàn bộ mẫu; bước nghiệm thu hiện tại chỉ dry-run mẫu 36
utterance và smoke thật 2 utterance. Với dữ liệu MAGI đầy đủ, dùng cặp raw/normalized
và character bank của truyện như hướng dẫn usage.

Benchmark dùng manifest `configs/reasoning.benchmark.example.json`; thay các đường
dẫn mẫu bằng chương thật và reference đã duyệt trước khi chạy. Chưa chạy pilot
3–5 chương độc lập hoặc chọn model winner vì chưa có bộ nhãn đó. GPU16/GPU48 chưa
được chạy trên phần cứng tương ứng trong session này.

Chưa fine-tune, xây đồ thị quan hệ/đặc tính hoặc triển khai Stage 4–6. Các sửa đổi
đã duyệt và artifacts reasoning là đầu vào cho những bước tiếp theo.
