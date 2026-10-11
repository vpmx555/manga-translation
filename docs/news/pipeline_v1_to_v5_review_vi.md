# Review pipeline từ v1 đến hiện tại và refactor ngưỡng

Ngày: 11/10/2026. Review dựa trên source trong workspace, manifest contract của
`RunStore`, tài liệu lịch sử trong `docs/archive` và các báo cáo trong `docs/news`.
Repo hiện không có tag v1–v5; commit gần nhất mang tên `update v4` không đại diện
cho toàn bộ thay đổi chưa commit. Vì vậy bảng dưới mô tả các thế hệ kiến trúc,
không khẳng định mỗi thế hệ tương ứng với một release Git.

## Các thế hệ pipeline

| Thế hệ | Luồng chính | Thay đổi và ý nghĩa |
|---|---|---|
| v1, trước refactor | MAGI → raw/normalize → scenes → reasoning scene/Stage 3 → review | MAGI cung cấp text, panel, nhân vật, OCR, reading order và speaker association. Embedding/scene reasoning phân đoạn; Stage 3A sửa speaker, 3B giải quyết tên/ID, 3C giải quyết addressee, 3D sửa thứ tự đọc. Có evaluation/benchmark và checkpoint riêng. Đây là kiến trúc lịch sử, không phải `pipeline_version=1` được controller hiện tại hỗ trợ. |
| v2 | extract → normalize → scan → classify → link → export | Bỏ scene segmentation và các lượt sửa speaker/order toàn chapter. Tách artifact từng stage, worker MAGI riêng, ID ổn định từ character bank. Scan/classify tập trung câu có tên mới; VLM chỉ dùng cho nhánh cần ghép tên trong panel. Speaker dialogue giữ association nguồn; narrator và người được nhắc tới được tách. |
| v3 | extract → normalize → scan → analyze → export | Policy `essential-v3`: analyze hợp nhất classify/link, xử lý content type, speaker, addressee và vai trò tên trong batch. Dùng context/memory có giới hạn, repair có giới hạn, journal và fallback rõ ràng. Chỉ nhánh introduction đủ điều kiện mới cần panel/VLM. |
| v4 | extract → normalize → scan → analyze → export → translate | Thêm normalize noise policy và dịch tiếng Việt hai lượt độc lập: natural/localized. Giữ ID speaker/listener, glossary/override, provenance nguồn, snapshot/attempt/target và resume phần thiếu. Các text không đưa vào analyze vẫn có bucket translation_only. |
| v5, hiện tại | layout → extract → bubble_geometry → normalize → [inpaint] → scan → analyze → export → [translate] | Koharu cung cấp panel và bubble mask; MAGI dùng panel Koharu để sắp text và giữ vai trò phát hiện text/nhân vật/association. Geometry bubble và speech style/evidence được giữ trong output. Hayai OCR đọc crop trong môi trường riêng. Inpaint là nhánh tùy chọn, mặc định tắt; translate xuất hiện khi cấu hình dịch có mặt. |

`RunStore` hiện hỗ trợ resume manifest v2/v3/v4/v5. Layout bật trong một run
essential-v3 đưa run đó thành v5; v3/v4 vẫn có thể tạo khi tắt layout. Resume giữ
phiên bản, config snapshot và checkpoint đã lưu; không tự nâng run cũ lên v5.

## Các phần đã thay đổi theo chiều sâu

- **Character identity:** ID ổn định tách khỏi display name; pending/unknown vẫn
  được giữ. Policy mới `individual-v1` không tự mở rộng group speaker; narrator
  phải phù hợp narration. Tên introduction và direct-address được ghép theo
  bằng chứng, tên thủ công được bảo vệ, alias không tự đồng nhất theo giống tên.
- **OCR và môi trường:** MAGI v2 ghim Transformers 4; Hayai dùng environment
  riêng để tránh đổi dependencies MAGI. Koharu RF-DETR 1.7.0 dùng Transformers 5
  riêng. Hayai mặc định cho run mới; manifest không có `ocr` giữ MAGI khi resume.
- **Ngữ nghĩa:** hình dạng bubble là bằng chứng hình học; `thought_hint`,
  `shout_hint`, `caption_hint`, `whisper_hint` không phải ground truth ngữ nghĩa.
  Analyze xử lý speech style/evidence theo contract riêng, không lấy confidence
  heuristic hình học làm xác suất câu là thought/shout.
- **Nguồn và artifact:** source boxes, reading order, association nguồn, model
  fingerprint và config được giữ. Worker receipt hỗ trợ phục hồi extraction;
  checkpoint và source/config snapshot được kiểm tra trước khi tái dùng.
- **Resume và verification:** cache xác minh DAG chỉ trong một pass; lần đọc mới
  vẫn phát hiện artifact bị thay đổi. Reanalyze tạo run mới, giữ run nguồn; thay
  OCR, geometry hoặc ngưỡng extraction cần fresh run tương thích.
- **Review:** sinh Markdown/visualization để người dùng xem. Review Markdown
  không phải approval gate cho agent. Việc đủ artifact hoặc ID không rỗng chỉ
  xác nhận hợp đồng dữ liệu, không chứng minh inference đúng hoàn toàn.
- **Vận hành:** mặc định local. `AGENTS.md` yêu cầu user cho phép rõ ràng và gọi
  trực tiếp Runpod trước khi dùng Runpod; pod cũ hoặc local chậm không cấp quyền.

## Refactor ngưỡng ngày 11/10/2026

| Tham số | Trước refactor trong source/config | Mặc định mới | Không gian điểm |
|---|---|---:|---|
| text_detection_threshold | Source default 0.15, pipeline.json đã là 0.45 | 0.45 | Detection score |
| text_character_matching_threshold | Chưa đưa ra pipeline config; MAGI mặc định 0.35 | 0.25 | Sigmoid affinity 0..1 |
| text_classification_threshold | Chưa đưa ra pipeline config; MAGI mặc định 0.5 | -0.5 | Dialogue logit, không áp sigmoid |
| layout.bubble_threshold | 0.25 | 0.32 | Koharu detection score |
| layout.panel_threshold | 0.50 | 0.50 | Koharu detection score |
| character_detection_threshold | 0.35; manifest cũ thiếu trường dùng MAGI 0.30 | 0.35 | Detection score |
| character_character_matching_threshold | 0.75 | 0.75 | Character affinity |
| Bubble mask binarization | > 0.50 | > 0.50 | Mask value; độc lập với detection score |

Đã gom mặc định MAGI vào `src/manga_pipeline/extraction/config.py`, dùng chung
cho `load_config`, wrapper `install_detection_patch` và probe MAGI. Hai trường
association/dialogue được truyền tới `predict_detections_and_associations`.
Keyword tường minh tại call site vẫn có quyền ghi đè default wrapper.

Validation từ chối bool, NaN/Inf và detection/affinity ngoài 0..1; dialogue cho
phép mọi logit hữu hạn, bao gồm số âm. Reanalyze so sánh các ngưỡng với run nguồn
trước khi tái dùng extraction, cả nhánh MAGI-only lẫn visual.

Manifest cũ thiếu association/dialogue vẫn dùng 0.35/0.5; thiếu character
detection vẫn dùng 0.30. Default mới chỉ áp dụng run mới, không sửa config
snapshot của các run đã có. Bubble mới 0.32 cũng không thay config layout đã
được lưu trong manifest cũ.

## Quan hệ giữa các ngưỡng và tác động dự kiến

Text detection quyết định vùng chữ được đưa vào các head tiếp theo. Dialogue
so sánh logit với -0.5 để tạo `is_essential_text`. Text-character chọn nhân vật
có affinity cao nhất và giữ liên kết khi điểm **lớn hơn** 0.25. Dialogue không
gate phép tính hoặc kết quả association. Hạ dialogue có thể đưa thêm câu vào
semantic analysis; hạ association có thể giữ thêm speaker link, kể cả link sai.

Bubble detection 0.32 loại các candidate điểm thấp hơn so với 0.25; số bubble
và mapping có thể giảm. Bubble mask/contour, rule phân loại hình dạng và các
ngưỡng mapping coverage 0.80/margin 0.15 giữ nguyên. Tăng detection threshold
không bảo đảm nhãn thought/shout chính xác hơn.

## Chẩn đoán thực tế và giới hạn kiểm chứng

Probe MAGI local chapter 1, text 0.45/dialogue -0.5/association 0.25:

| Truyện | Trang | Text | Dialogue > -0.5 | Liên kết > 0.25 | Liên kết > 0.35 |
|---|---:|---:|---:|---:|---:|
| After School | 11 | 58 | 53 | 51 | 50 |
| Habatake | 31 | 196 | 177 | 174 | 160 |

Các cột ngưỡng association so sánh cùng bộ detection và affinity, không phải
đánh giá accuracy người nói. Đã kiểm tra ảnh/crop đủ và cờ acceptance khớp điểm.
Chi tiết: báo cáo probe MAGI local tại `outputs/diagnostics/magi-threshold-probe-20261011.md`.

Probe bubble đã hoàn tất chỉ **After School**, với default tại thời điểm khởi
động là bubble 0.25. Habatake đã hủy trước khi bắt đầu bubble inference. Refactor
mới không đổi config đã nạp của process này; không coi kết quả 0.25 là benchmark
cho 0.32. Đủ 11/11 trang, 53 bubble, 0 trang lỗi; đã kiểm tra đủ overlay/mask/crop.
Phân bố style: normal 23, caption_hint 10, thought_hint 3, shout_hint 2, unknown 15.
Lọc theo score >= 0.32 trên bộ candidate đã lưu còn 48/53; đây là phép lọc hậu
kỳ, không phải inference lại ở 0.32. Run mới sau refactor dùng 0.32.
Ảnh nhãn: `outputs/diagnostics/speech-bubbles-default-20261011/after-school/`.
Đã có [điều tra input/geometry so với AIP419](bubble_geometry_aip419_comparison_vi.md):
đối chiếu 53 mask và chạy detector thật trên hai trang mẫu, tách rõ classifier
geometry bổ sung của pipeline hiện tại khỏi output native AIP419.
Không chạy full pipeline OCR/LLM/dịch
hay đo accuracy có gold labels trong lượt refactor này.

## Nguồn đối chiếu

Kiểm chứng refactor: 44 test liên quan đã qua (`tests.test_visual_semantics`,
`tests.test_visual_pipeline`, `tests.names.test_dialogue_analysis`,
`tests.analysis.test_speaker_policy`); `git diff --check` qua. Test kiểm tra
default/forwarding/override, logit âm, NaN/Inf/bool/range không hợp lệ, default
manifest cũ và chặn reuse extraction khi hai ngưỡng mới thay đổi.
Log: `outputs/diagnostics/threshold-refactor-tests.log`.

- [Kiến trúc v1 lịch sử](../archive/pipeline_implementation_report.md),
  [reasoning Stage 3](../archive/reasoning_implementation_report.md).
- [Refactor v2](refactor_report.md), [dialogue v3](dialogue_v3_implementation.md),
  [policy speaker](speaker_policy_individual_v1.md).
- [Dịch v4](vietnamese_translation_usage.md), [Hayai OCR](hayai_ocr_vi.md).
- [RunStore và version contract](../../src/manga_pipeline/storage/runs.py),
  [orchestrator](../../src/manga_pipeline/pipeline.py),
  [visual stages](../../src/manga_pipeline/visual/stages.py).
