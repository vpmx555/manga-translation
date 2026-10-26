# Triển khai scan và validator giản lược — 24/10/2026

**Cập nhật sau lượt test:** OCR mặc định đã nâng lên
[`canonical-scan-v4`, sửa tên giữ cách gọi](ocr-surface-spelling-fix.md).
Phần dưới ghi lại bản triển khai v3 ban đầu và kết quả kiểm tra tại thời điểm đó.

Đã triển khai local theo thiết kế người dùng chốt. Không kết nối Runpod, không
tạo pod, không chạy OCR hay model inference trong đợt triển khai này. Các kết quả
chapter đã chạy trước đây được giữ nguyên, không coi là kết quả của contract mới.

## Contract mặc định

| Config trong section `v6` | Mặc định | Ý nghĩa |
|---|---|---|
| `scan_policy` | `canonical-scan-v3` | Scan chung scene/tên/group, OCR sau sửa là nguồn chính |
| `scan_output_tokens` | 8192 | Reserve output; int1..16384, không retry tăng reserve |
| `identity_name_binding_policy` | `chapter-name-binding-simple-v2` | Name binding và profile/relationship dùng validator cấu trúc |
| `identity_min_chapter_crops` | 8 | Gate tạo identity có tên mới cho từng MAGI |
| `identity_binding_concurrency` | 8 | Giữ queue identity tối đa 8 request |
| `profile_concurrency` | 8 | Giữ queue profile tối đa 8 request |
| `relationship_concurrency` | 8 | Giữ queue relationship tối đa 8 request |

Đã cập nhật Python defaults và bốn config `pipeline.json`,
`pipeline-v6-runpod.json`, `pipeline-visual.json`, `pipeline-visual-runpod.json`.
Tên section vẫn `v6`; không có section `v7` mới. File cấu hình lịch sử không có
marker scan được nạp vào đường legacy, không ngầm đổi contract khi resume.

## Hành vi đã thay đổi

Scan một request cho toàn chapter. Model trả `scenes`, `groups`, `names`;
name item là `text_id/old_name/new_name/group_id`. Code replace-all tên theo thứ
tự trả về, bỏ qua hoa/thường, khoảng trắng và gạch nối. A→B rồi B→C tạo C.
Không có offset model, không xác thực chính tả, không đòi tên mới nằm trong OCR
thô. Lỗi từng mục được giữ review, group thiếu không kích hoạt fallback identity.

OCR sau sửa đi vào analyze và các stage sau qua adapter canonical; raw OCR chỉ
dùng đối chiếu trước/sau. Request analyze không gửi lại trường `ocr_raw_text`
hay raw_surface của candidate mới. Canonical snapshot có hash, coverage ID và
guard nguồn gốc; sửa dữ liệu bên ngoài vẫn bị chặn.

Build identity chỉ tạo tên mới khi MAGI đủ crop thật trong chapter và model gắn
tên được gọi/nhắc đến cho MAGI. Không dùng lifetime count/gallery làm gate. Tên
đề xuất cho MAGI dưới ngưỡng vẫn xuất trong review. Tên analyze đề xuất không
vượt qua gate này. Identity cũ vẫn giữ trong chapter ít crop; không gộp hai
identity đã xác lập. Liên kết MAGI và visual main prototype giữ cơ chế cũ.

Model không cần chép refs để identity/profile/relationship được sử dụng.
Code ghi riêng `supplied_evidence_refs` và `model_evidence_refs`; không nhận
nguồn đầu vào là citation của model. Profile/relationship mới thay object ngữ
nghĩa trước; lịch sử/version vẫn được giữ. Một chiều address lỗi không loại
chiều còn lại; chiều thiếu là unknown. Các field hỏng được giữ unknown/review.

Mention `narrator_introduction` trong dialogue đổi riêng nhãn thành unknown và
đánh review, không loại tên/owner dùng được.

## Request, budget và resume

- Scan chỉ một attempt; không tự chữa rồi gọi model lại khi parse/truncation lỗi.
- Name binding lập job theo group; chia context occurrence trước POST nếu vượt
  input/image budget. Context đơn không chia được và vượt limit được lưu
  budget_failed với `post_sent=false`.
- Profile và relationship tiếp tục pack evidence theo budget, giữ metadata về
  phần đã gửi/bỏ. Không thêm model retry.
- Runtime lưu request journal, packed packet và normalized receipt. Resume
  kiểm hash/job/source images trước khi dùng lại, không POST lại response đã có.
- Import kiểm scan contract. Đổi policy scan phải import **tới normalize** rồi
  chạy lại scan/analyze/build_identity/relationship; không sửa manifest đã freeze.
- Đổi gate tạo identity cũng đổi binding contract; không dùng kết quả identity
  trước đó như thể đã chạy với ngưỡng mới.

## Artifact và review

| Artifact | Nội dung |
|---|---|
| `scan/result.json` | Scene/candidates, canonical_rows/hash, groups, warnings, replacement history |
| `scan/ocr-current.json` | Text hiện hành sau sửa |
| `scan/replacement-history.json` | Trước/sau từng rule và từng câu |
| `build_identity/name-binding/plan.json` | Frozen group jobs, count chapter, candidate metadata và budget failures |
| `build_identity/name-binding/packed/*.json` | Packet thực sự gửi |
| `build_identity/name-binding/decisions/*.json` | Receipt, dữ liệu model dùng được và lỗi từng mục |
| `build_identity/name-binding/result.json` | Summary, accepted, tên chưa áp dụng, count chapter, review |
| Local `review.md`, `build_identity.html`, `relationship.html` | Review độc lập cho hai bước, ảnh/thoại/lý do |
| Local `errors.html`, `errors.md`, `errors.json` | Lỗi request và mục cần review, gồm tên dưới ngưỡng |

Tên thư mục stage thật có prefix số theo run. HTML dựng bằng code; không gọi
model. Trang lỗi của policy mới không replay luật citation/mention-type cũ.

## Mã chính

- `src/manga_pipeline/v6/scan_v3.py`: wire, sửa OCR, scenes/groups và review warnings.
- `name_normalization.py`, `semantic.py`, `independent.py`, `planning.py`,
  `verification.py`: dùng canonical OCR, giữ coverage và kiểm integrity.
- `identity_binding_simple.py`, `identity_binding_evidence.py`: gate crop và
  name binding giữ cơ chế liên kết identity cũ.
- `structural_validators.py`, `identity_profiles.py`, `relationship_v2.py`:
  cô lập lỗi field/edge và áp object ngữ nghĩa mới.
- `identity_runtime.py`: normalize response trước validation/receipt, single attempt.
- `deployment/runpod/identity_review.py`, `identity_failure_review.py`: HTML/JSON review.
- `deployment/runpod/preanalyze_import.py`: guard reuse khi đổi contract.

## Kiểm tra và giới hạn

Bộ kiểm tra tập trung gồm 152 test trong 12 module, bao gồm 23 test mới cho scan,
identity và structural validators. Kiểm tra cả Unicode/replace-all/cascade,
canonical OCR tới analyze, ngưỡng 7/8 crop, tên analyze không bypass gate,
không cần gallery/citation, không retry/re-POST, budget trước POST, bank bất biến,
profile/graph/export và HTML review. Các fixture legacy chỉ định rõ policy cũ.
Bốn config hiện hành nạp hợp lệ; AST của source v6 và script deployment hợp lệ.

Đây là kiểm tra logic và tích hợp bằng provider giả lập. Chưa có kết luận mới về
độ chính xác tên/identity/xưng hô trên Iris Zero hoặc thời gian/context thực của
Qwen. Cần người dùng yêu cầu chạy inference riêng trước khi có kết quả đó.
