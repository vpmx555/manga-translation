# Artifact, checkpoint và các loại ID

> Historical documentation before chapter-story-graph-v7. See [current docs](../../../v7/README.md).

[← Chỉ mục](../README.md)

## Run và stage

Một run nằm dưới `outputs/<story>/<chapter>/<run-id>/`. `manifest.json` giữ
trạng thái stage, source/config fingerprints và các đường dẫn artifact. Config
và source được snapshot; RunStore từ chối resume nếu chúng bị sửa lệch.

Stage mới với inpaint bật:

```text
01_layout/             07_build_identity/
02_extract/            08_relationship/
03_bubble_geometry/    09_export/
04_normalize/          10_inpaint/
05_scan/               11_translate/
06_analyze/            12_typeset/
```

Không hardcode các số này để đọc run cũ. Tắt inpaint thì `translate` là stage10;
run dùng identity cũ có DAG/đánh số riêng. Lấy mapping từ RunStore/manifest.

`result.json` đã commit là dữ liệu stage có thẩm quyền. Các target đã commit
có thể sống sót khi stage bị ngắt; resume không cần gửi lại tất cả request.
`review.md` và visualization là các cách xem dữ liệu, không phải nguồn để nhập
ngược quyết định vào pipeline.

## Phân biệt ID

| Tên | Ý nghĩa | Được dùng ở đâu |
|---|---|---|
| Page / panel / box / group | Vị trí và tổ chức ảnh | Layout, geometry, source positions |
| Utterance/target ID | Một đơn vị thoại ổn định, có source hash/page/group | Analyze, translation, render |
| MAGI / bank ID | Cụm nhân vật theo embedding thị giác | Bank, crop review |
| `person_id` | Khóa identity ngữ nghĩa, có thể có nhiều MAGI members | Registry, profile, graph, export |
| `batch_id`, `scene_id` | Tổ chức request/context | Scan, analyze, translation |

Review dành cho người dùng hiển thị số MAGI trong bank và nguyên câu thoại.
Khóa person/target vẫn được giữ trong JSON để truy vết và join; không đổi khóa
máy thành tên hiển thị, vì tên có thể mơ hồ hoặc nhiều alias.

`unknown`/null/`Other` biểu thị thiếu phân giải trong ngữ cảnh tương ứng; không
tạo một nhân vật thật mang tên Other. `resolved` trong một dòng không tự chứng
minh đã có identity có tên; kiểm tra speaker/person/mentions và evidence riêng.

## Đọc export và bản dịch

Export có `kind=structured_dialogue`; `utterances` và `effects` là các target
dịch natural, `excluded` không phải danh sách render mặc định. Không áp dụng
field guide v4 `translation_only/source_text_id` nguyên xi vào contract này.

| Trường | Cách đọc |
|---|---|
| `id` | Join target với translation/plan; không join bằng vị trí mảng |
| `text`, `reading_order` | Text nguồn sau lớp patch hợp lệ và thứ tự đọc |
| `source_boxes` / source positions | Truy ngược OCR box/trang/part để đặt chữ |
| `content_type` | dialogue/narration/sfx/action_state/ui/watermark/unknown |
| `speech_style` | normal/whisper/shout/thought/caption/unknown; khác content type |
| `speaker_person_id`, `speaker_name` | Khóa canonical person và tên nếu đã có |
| `speaker_cluster_ref` | Dấu vết cụm nguồn để relink/provenance |
| `speaker_kind`, `voice_type` | Phân biệt person/narrator/others theo dữ liệu thực có |
| `addressee_ids` | Danh sách canonical person nhận lời; không suy từ người được nhắc tên |
| `mentions` | Name evidence/ownership, components, recipient/target và confirmation |
| `semantic_resolution`, `semantic_needs_review` | Trạng thái ngữ nghĩa; tách khỏi việc stage đã commit |
| `scene_id`, `batch_id`, `batches` | Nguồn chia scene/batch và truy vết request |
| `person_registry_snapshot` | Person state đã freeze cho export |
| `relationship_snapshot_hash` | Lineage graph đã commit trong DAG mới |
| `semantic_coverage_complete`, `unresolved_targets` | Độ phủ theo contract, không phải accuracy |
| `translations.natural` | Object gồm translation, needs_review, review_reason, glossary_proposals sau stage dịch |

SFX và action_state đi vào `effects`; UI/watermark vào `excluded`; các nội dung
còn lại giữ trong utterances theo logic export. Chỉ đọc các trường tồn tại ở
revision đang xử lý; không tự điền missing field bằng person/role suy đoán.

Ví dụ một dòng review `resolved` nhưng `speaker_anchor.person_id=null` có thể
chỉ nói anchor nguồn không khóa được person. Nó không tự nghĩa OCR bị mất hoặc
named identity đã xác nhận. Xem speaker inference sau merge và registry snapshot
riêng; trường source anchor không phải kết quả cuối bị ép giữ nguyên.

## Bank và dữ liệu ngữ nghĩa lâu dài

Bank theo story nằm dưới `banks/<story>/`, gồm metadata/vector/crop theo schema
adapter. Person registry là tầng link riêng; profile ở `profiles-v2.json`, graph
ở `relationships-v2.json` cạnh registry. Không đổi embedding/crop MAGI khi chỉ
muốn gộp identity.

Profile và graph có version/history, khóa ghi và atomic publication. Run freeze
version/snapshot hash để chapter sau có thể dùng lại lịch sử mà không làm thay
đổi bản dịch của run cũ. Graph legacy `relationships.json` không được dùng làm
seed/fallback tự động cho graph v2.

## Evidence và request của hai stage mới

Run có marker bootstrap bổ sung `07_build_identity/name-binding/` với plan,
packed packets, requests/decisions, result/summary và reference crops có hash.
Collection `name_groups_v1` trong person registry lưu độc lập nhóm tên chưa có
owner và binding history; run có `snapshots/name-groups-v1.json` riêng.
`binding_snapshot_hash` được chuyển sang export. Các row được bổ sung unknown
có `analyze_original`, `identity_binding` provenance và communication evidence;
artifact analyze gốc không bị viết lại.

`build_identity` và `relationship` giữ `result.json`, `review.md`, frozen plan,
packed evidence, `requests/`, durable `decisions/`, `request-summary.json` và
reference crops đã hash. Dùng summary để phân biệt:

- Không có job hợp lệ: completed nhưng không inference.
- Có response hợp lệ: quyết định được commit/reuse theo fingerprint.
- Failed hoặc uncertain: giữ receipt và review; không replay khi resume.
- Chưa gửi: có thể chạy sau khi server sẵn sàng.
- Lỗi hash/provenance: lỗi integrity, không phải semantic unknown.

RunStore xác minh hash crop tham chiếu khi mở stage đã commit. Runpod verifier
kiểm tra profile/graph fingerprints, directed-edge uniqueness, export lineage và
crop integrity. Không bỏ các kiểm tra này để tận dụng output cũ.

## Các bản output Runpod

```text
outputs/runpod-<batch>/
  live/       bản sao dữ liệu thực thi đang thay đổi
  durable/    publication/archive và trạng thái job
  ...         receipt/verification/local review theo helper hiện hành
```

Live đã xuất hiện không có nghĩa backup hoàn tất. Terminal state và verification
archive/hash/size/ZIP CRC mới là căn cứ thu hồi pod. `POD.json` và các receipt
vận hành ghi pod/batch gốc; pod thay thế phải có record riêng, không viết đè lịch
sử như thể chưa từng đổi máy.
