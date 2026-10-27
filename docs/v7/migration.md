# Migration và mức kiểm chứng

## Những gì đã đổi

- Stage `scan` đổi thành **graph** và tạo hierarchy scene/event cùng nhóm tên/OCR hiện hành.
- Analyze batch độc lập được thay bằng event tuần tự trong scene, scene chạy song song.
- Thêm stage **reanalyze** trước build_identity. Downstream đọc effective snapshot qua `RunStore.analysis()`.
- Toàn bộ request dùng 64K và thinking off; contract mới chặn việc resume nhầm artifact cũ.
- Lệnh tạo run revision cũ `reanalyze` đổi thành **revise**. `reanalyze` bây giờ chỉ thực hiện stage đối chiếu.
- Bỏ các module scene/planning/batch-validator của luồng độc lập và model name-binding cũ; giữ đăng ký identity từ analyze và các helper cần thiết.
- Docs trước thay đổi được giữ ở [archive](../archive/v7-before-story-graph/README.md), không dùng làm hướng dẫn chạy hiện tại.

## Storage và reuse

Namespace code `manga_pipeline.v6`, controller `runpod-v6.ps1` và `pipeline_version=6` của storage vẫn được giữ. Thế hệ semantic mới được định danh bằng `analysis_contract=chapter-story-graph-v7`. Không đổi trường này bằng tay để đọc checkpoint cũ.

Ảnh trang, extraction và geometry là các đầu ra upstream có thể reuse khi provenance/config tương thích. Output graph/analyze cũ không thể chuyển thành kết quả mới chỉ bằng đổi tên thư mục. Không chỉnh batch/source bundle đã freeze; luôn tạo batch mới khi source/config đổi.

Đợt này không tự tách/sửa các identity đã publish sai trong registry cũ. Khi cần đánh giá lại từ nguồn trước gộp, dùng backup đã xác minh phù hợp; việc đổi contract không tự phục hồi trạng thái trước gộp.

## Kiểm chứng

Suite model-free dành cho contract mới:

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest discover -s tests -p test_chapter_story_graph.py -v
```

Bao phủ panel 10%, event ≤8, crop có panel im lặng/caption, context nối tiếp, scene song song, resume không gửi lại request, checksum ảnh, đuôi từ liên kết MAGI và annotation trước analyze, loại giả thuyết nhân vật graph, sửa reanalyze với confidence thấp hơn, giữ nguyên metadata đuôi, không lan truyền sửa event, fallback reanalyze, HTML và đường dispatch tới export.

Reanalyze hiện chia hai nửa độc lập tại ranh giới event gần giữa nhất; kiểm tra thêm ownership, chạy hai request song song, ghép patch không ghi đè nhau, lỗi một nửa vẫn giữ sửa đổi nửa kia và resume không gửi lại request. Chỉ analyze ưu tiên anchor 8/10. Prose và review mới của cả ba stage dùng tiếng Anh, giữ nguyên OCR/tên nguồn.

Các kiểm tra này dùng provider giả và ảnh fixture; **không gọi model, GPU hoặc Runpod**. Kiểm tra nguồn/AST và load bốn config được thực hiện riêng. Chất lượng nhận diện người/mention và tính khả thi token/VRAM trên chapter thật cần được đánh giá bằng một tác vụ inference được cấp quyền sau đó.

Các test chỉ dành cho contract cũ được tách khỏi suite hiện hành; không sửa expected của chúng để giả vờ rằng ngữ nghĩa cũ và mới giống nhau.

## Kết quả kiểm tra đợt triển khai 2026-10-26

- `python -B -m unittest discover -s tests`: **159 test, 155 qua, 4 bỏ qua**, không có failure/error; khoảng 9 giây trên môi trường local.
- Suite bao gồm graph mới, spelling OCR, visual/geometry, CLI/review, runtime và controller fixtures. Các request HTTP/model trong test được giả lập; không dùng pod thật.
- AST của 120 file Python trong source/deployment hợp lệ; cả 4 config hiện hành load thành công.
- 19 trang Markdown trong docs v7 được kiểm tra liên kết local, không có link hỏng; `git diff --check` hợp lệ.
- Chưa thực hiện inference/quality evaluation với model thật hoặc Runpod cho contract mới.

Lệnh chạy toàn bộ suite hiện hành:

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest discover -s tests
```
