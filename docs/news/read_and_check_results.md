# Hướng dẫn đọc và kiểm tra kết quả từng bước

Các ví dụ bên dưới là run v2 đã lưu, có classify/link riêng. Run v3 mới dùng `04_analyze` và `05_export`; xem [hướng dẫn chạy và đọc kết quả hiện tại](usage.md#đọc-kết-quả-nhanh).

Để kiểm tra nhanh, mở [review tổng quan](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/review.md), rồi các file `review.md` từng bước. Chúng chỉ giữ thông tin cần đọc; không có hash, UUID, box arrays hoặc metadata checkpoint. Nội dung bên dưới là hướng dẫn JSON chi tiết khi cần chẩn đoán sâu.

Ví dụ trong tài liệu lấy từ run đã hoàn thành:

```text
outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/
```

Các đường dẫn bên dưới mở đúng run này. Với chapter/run khác, thay phần story, chapter và run ID tương ứng. Số liệu là kết quả đã lưu trong run, không phải nhãn đã được người dùng xác nhận.

## Tên được trích xuất ở đâu?

Mở [03_scan/result.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/03_scan/result.json), đọc danh sách `candidates`. Tên nằm ở `candidates[].name`. Đây là **ứng viên tên**, chưa đồng nghĩa tên nhân vật thật hoặc đã nối được với ID.

Run này có:

| Ứng viên | Trang | Phân loại ở bước 04 | Kết quả nối tên ở bước 05 |
| --- | --- | --- | --- |
| Mort | 2 | dialogue / third_person | skipped |
| Klink | 3 | dialogue / unknown | skipped |
| Madol-san | 8 | dialogue / direct_address | skipped |

Ba tên chưa được nối với ID. Cả ba câu đều được bước 04 phân loại là dialogue; nhánh ghép tên mới của pipeline hiện tại chỉ xét narration. `Mort` xuất hiện trong text có dạng `@nenenero ... <Mort>`, cần kiểm tra có phải comment ngoài truyện. `Klink` là text đơn lẻ, cần đối chiếu ảnh xem có phải hiệu ứng âm thanh. Không coi hai ứng viên này là tên đã xác nhận.

Tên đã nối thành công được lưu tại `characters["<id>"].display_name` trong [bank metadata](../../banks/after-school-we-do/metadata.json), và được đưa vào `speaker_name`/`mentions[].referenced_id` ở bước export khi phù hợp. Bank của run này có ID 1–4, tất cả `display_name` hiện là null.

## Bản đồ file

| Vị trí | Dùng để đọc/kiểm tra |
| --- | --- |
| `manifest.json` | Trạng thái cả run và từng bước, config, đường dẫn ảnh nguồn/bank |
| `01_extract/result.json` | OCR, panel, detection nhân vật, association và ID MAGI nguồn |
| `01_extract/transcript.txt` | Bản text dễ đọc, speaker theo ID MAGI |
| `01_extract/visualizations/page_*.png` | Overlay kết quả MAGI trên ảnh |
| `02_normalize/result.json` | Text chuẩn hóa, nguồn, text bị loại và lý do |
| `03_scan/result.json` | Ứng viên tên |
| `04_classify/result.json` | Loại câu và vai trò từng ứng viên tên |
| `05_link/result.json` | Kết quả ghép tên hoặc lý do chưa ghép |
| `06_export/dialogue.json` | Dialogue cuối để sử dụng |
| `*/targets/*.json` | Commit/error của từng mục, dùng chẩn đoán retry/resume |
| `logs/magi.log` | Log extraction |
| `banks/<story>/metadata.json` | ID, tên, crop paths, pending và lịch sử tên nếu có |

`result.json` là artifact được commit của từng bước. `06_export/result.json` và `06_export/dialogue.json` chứa cùng kết quả cuối ở run này. File `progress.json` chỉ phục vụ hiển thị, không phải kết quả phân tích.

## 0. Kiểm tra run đã hoàn thành

Mở [manifest.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/manifest.json).

- `status: completed` và cả sáu `steps.<step>.status: completed`: các bước đã hoàn thành, artifact đã được commit.
- `partial`: còn target lỗi, xem `failures` và `targets`, rồi resume cùng run.
- `failed`: xem error của bước và log.
- `running`: tiến trình đang chạy hoặc đã bị ngắt trước khi ghi trạng thái cuối; kiểm tra tiến trình/log trước khi resume.

Run ví dụ hoàn thành sáu bước, không có failures. Trạng thái completed xác nhận việc thực thi thành công; độ đúng của OCR, speaker và phân loại vẫn cần đối chiếu ảnh.

## 1. Extract: kiểm tra ảnh, OCR, ID và người nói nguồn

Mở [transcript.txt](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/01_extract/transcript.txt) để đọc nhanh. `<1>: ...` là MAGI gán câu cho character ID 1; `<Other>` là chưa có stable speaker ID.

Mở [thư mục visualization](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/01_extract/visualizations). Ví dụ [page_8.png](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/01_extract/visualizations/page_8.png): khung xanh dương chứa nhân vật và nhãn ID, khung đỏ chứa text; đường nối thể hiện association của MAGI. Đây là overlay extraction, chưa phải overlay kết quả tên/người nghe cuối.

Đọc [01_extract/result.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/01_extract/result.json):

| Trường | Ý nghĩa |
| --- | --- |
| `pages[].image_path` | Ảnh nguồn tương ứng |
| `pages[].panels` | Các box panel `[x1, y1, x2, y2]` |
| `pages[].characters` | Các box detection nhân vật |
| `pages[].character_ids` | Stable ID song song với danh sách detection; null là chưa có ID ổn định |
| `texts[].text`, `bbox` | OCR và box text |
| `texts[].speaker_id` | Stable ID người nói theo association MAGI, có thể null/sai |
| `texts[].character_detection_index` | Index detection trong trang; không phải character ID |
| `texts[].panel_index` | Index panel trong trang |
| `texts[].reading_order` | Thứ tự text trong chapter |

Trang bắt đầu từ 1 theo danh sách ảnh đầu vào. Index panel/detection/text và `reading_order` bắt đầu từ 0. Run này có 11 trang: `page_1.png` tương ứng ảnh nguồn `02.png`, `page_8.png` tương ứng `09.png`.

Kiểm tra: OCR có sai tên hoặc mất dấu gạch nối không; balloon có nối đúng người nói không; cùng người có giữ ID qua các panel không; có ID bị dùng cho hai người khác nhau không; text từ comment/UI/SFX có bị nhận thành lời thoại không. Run này có 83 text nguồn.

## 2. Normalize: kiểm tra text giữ lại và text bị loại

Mở [02_normalize/result.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/02_normalize/result.json).

- `utterances`: câu còn lại sau chuẩn hóa. So sánh `text` với `text_original`.
- `source_text_ids`, `source_boxes`: trở về text/box nguồn ở extract.
- `content_type`: nhãn sơ bộ từ quy tắc, chưa phải kết luận LLM cho mọi câu.
- `normalization_reasons`, `content_review_status`: lý do và trạng thái provisional/confirmed.
- `excluded`: text bị loại, kèm `source_text_id`, loại và lý do.
- `merge_candidates`: gợi ý nối text, không phải các câu đã tự động gộp.

Run này giữ 82 utterance, loại 1 text ở trang 2 (`...:p2:t3`) với lý do `explicit_ui_pattern`, không có merge candidate. Kiểm tra text bị loại có đúng là UI, text còn giữ có lẫn comment/SFX và thứ tự đọc có hợp lý. Những quy tắc hiện tại không loại được mọi text ngoài hội thoại.

ID normalized thêm hậu tố `:u` vào ID text nguồn. Ví dụ `...:p8:t3:u` truy về `...:p8:t3`.

## 3. Scan: kiểm tra tên có bị thiếu hoặc nhận nhầm

Mở [03_scan/result.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/03_scan/result.json).

Ví dụ thật:

```json
{
  "name": "Madol-san",
  "start": 71,
  "end": 80,
  "source": "spacy",
  "key": "madol-san",
  "utterance_id": "58a4396d6f5714c0:p8:t3:u",
  "known_id": null,
  "state": "new",
  "id": "9ff8c82cf8d9ec3781af"
}
```

- `utterance_id`: tìm câu tương ứng trong bước 02; không cần đoán theo vị trí file.
- `start`, `end`: vị trí ký tự trong text normalized; end không bao gồm ký tự tại vị trí end.
- `source: spacy`: NER phát hiện; `source: bank`: đối chiếu tên đã biết.
- `state: new`: chưa có mapping; `known`: đã có mapping; `bank_conflict`: tên trùng nhiều ID.
- `known_id`: ID đã biết, không phải đề xuất ID mới.
- `id`: ID của mention/candidate, không phải character ID.

Kiểm tra từng candidate trong cả câu và ảnh: là tên người, tên tổ chức, hiệu ứng âm thanh hay OCR sai? Đọc thêm các câu không có candidate để phát hiện tên bị bỏ sót. SpaCy English small có thể bỏ sót tên romanized ngắn; absence trong candidates không chứng minh câu không có tên. Run này có 3 candidate, failures rỗng.

## 4. Classify: kiểm tra vai trò tên và narration

Mở [04_classify/result.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/04_classify/result.json). Bước này chỉ lưu các utterance có candidate, không phải toàn bộ 82 câu.

`content_type` ở đây do LLM phân loại cho câu mục tiêu khi có tên mới: dialogue, narration, thought hoặc unknown. `classification_source` cho biết model hay nhãn normalization được giữ lại.

| `mention_type` | Ý nghĩa |
| --- | --- |
| `direct_address` | Gọi trực tiếp người mang tên đó |
| `self_introduction` | Người nói tự giới thiệu tên mình |
| `third_person` | Nhắc đến người khác |
| `narration_introduction` | Lời kể giới thiệu nhân vật |
| `narration_reference` | Lời kể nhắc đến nhân vật |
| `unknown` | Chưa xác định được vai trò/tính chất tên |

`skipped: true` nghĩa là đã bỏ inference cho mention theo quy tắc tên biết sẵn/conflict; không phải lỗi API.

Kiểm tra bằng câu mục tiêu và hai câu trước/hai câu sau trong bước 02. Xem câu có thực sự là lời kể không; tên có phải người nghe hay chỉ người được nhắc đến không. Tính liền mạch của năm câu không tự chứng minh narration. LLM không nhận ảnh/box để phân loại bước này.

Run ví dụ: Mort là third_person, Klink là unknown, Madol-san là direct_address; cả ba content_type là dialogue. Đây là kết luận model cần review, không phải nhãn người dùng đã duyệt.

## 5. Link: kiểm tra tên được nối ID nào và vì sao bỏ qua

Mở [05_link/result.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/05_link/result.json). Dùng `candidate_id` nối về `candidates[].id` ở bước 03, rồi `utterance_id` về câu ở bước 02/04.

Đọc `status` và `reason` trước. Chỉ kết quả ghi tên thành công trong bank mới là mapping được áp dụng; kiểm tra lại tên/ID ở metadata. Các trạng thái bỏ qua, chưa xác định hoặc bảo vệ tên thủ công không nên coi là đã gán tên mới.

Quy tắc áp dụng:

1. Tên đã mapped hoặc mention không thuộc narration đủ điều kiện: bỏ qua.
2. Panel không có stable ID: không gán.
3. Một stable ID và một tên mới đủ điều kiện: tự gán, không VLM.
4. Một stable ID nhưng nhiều tên mới: chưa xác định.
5. Từ hai stable ID khác nhau: VLM xét panel, có thể abstain; không được chọn pending.

Các reason thường gặp gồm `known_or_not_narration`, `name_already_mapped`, `no_stable_id_in_panel`, `multiple_new_names_one_id`, `one_stable_id_one_new_name`, `vlm_narration_check` và `conflicting_names`. Với `known_or_not_narration`, xem lại bước 03/04 để phân biệt tên biết sẵn với câu không phải narration.

Run này cả ba link là skipped / known_or_not_narration vì cả ba câu là dialogue. Không có mapping mới, không có VLM linking. Thiếu mapping hợp lệ là kết quả xử lý theo điều kiện, không phải failures.

Nếu có mapping, đối chiếu panel và crop của ID: tên có chỉ nhân vật hiện trong panel không, hay nhắc người ngoài panel; model có chọn nhầm người đang nói không; có ghi đè tên thủ công không.

## 6. Export: kiểm tra kết quả cuối

Mở [06_export/dialogue.json](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/06_export/dialogue.json), đọc `utterances`.

| Trường | Cách đọc |
| --- | --- |
| `text`, `page`, `panel_index` | Câu và vị trí để đối chiếu ảnh |
| `source_speaker_id` | Association stable ID nguồn từ MAGI |
| `speaker_id`, `speaker_name` | Người nói cuối và tên đã biết |
| `voice_type` | character, narrator hoặc unknown |
| `content_type` | Loại câu cuối, có thể vẫn là nhãn sơ bộ nếu câu không được classify |
| `mentions[].name`, `mention_type` | Tên và vai trò được nhận ra |
| `mentions[].referenced_id` | Character ID đã nối tên; null nếu chưa nối/không duy nhất |
| `addressee_ids`, `addressee_type` | Người nghe đã xác định hoặc unspecified |
| `name_links` | Tổng hợp kết quả link để audit |

Ví dụ câu ở trang 8:

```text
I... I need to make sure nothing weird happens to you because of this, Madol-san...
speaker_id = 1
speaker_name = null
voice_type = character
mentions: Madol-san / direct_address / referenced_id = null
addressee_ids = []
addressee_type = unspecified
```

Điều này biểu diễn: MAGI gán người nói ID 1; text đang gọi Madol-san, nhưng pipeline chưa biết tên đó thuộc ID nào. `direct_address` xác định chức năng của tên trong câu, không tự xác định được character ID người nghe.

Run này có 56 câu voice_type character, 26 unknown, không có narrator; cả 82 câu có addressee_type unspecified. Đọc thông tin này cùng ảnh để đánh giá phần còn thiếu, thay vì chỉ nhìn trạng thái run completed.

Với narration ở run khác, effective speaker_id là null, voice_type narrator; source_speaker_id vẫn giữ association MAGI gốc. Nhân vật được nhắc đến không trở thành speaker của lời kể.

## Bank: đối chiếu ngoại hình và sửa tên

Mở [metadata.json](../../banks/after-school-we-do/metadata.json) và [thư mục crops](../../banks/after-school-we-do/crops).

- `characters["1"]` là stable ID 1. Đọc `crop_paths` để mở đúng ảnh reference của ID đó; đường dẫn tính từ thư mục bank.
- Không suy ra ownership chỉ từ tên file crop: một crop có tên `pending-...` có thể đã được chuyển vào stable character; metadata là nơi xác định crop hiện thuộc ID nào.
- `pending` chứa detection chưa thành stable identity; run này có 18 pending record. Kiểm tra state active/closed và page_keys. Pending không được gắn tên, không phải danh sách nhân vật cần tự điền tên.
- Khi đã đối chiếu chắc chắn, có thể sửa `characters["<id>"].display_name`. Không sửa ID hoặc embedding/crop để đổi tên. Cập nhật tự động bảo vệ tên thủ công theo quy tắc của bank.
- Export đã completed là snapshot; sửa bank sau đó không tự sửa dialogue.json cũ, và resume không chạy lại export completed.

## Theo dấu một kết quả sai

| Hiện tượng | Kiểm tra từ bước nào |
| --- | --- |
| Tên trong ảnh bị OCR sai hoặc mất | 01 extract |
| Tên có trong OCR nhưng không còn trong utterances | 02 normalize / excluded |
| Tên có trong normalized text nhưng không có candidate | 03 scan |
| Candidate là SFX/comment hoặc vai trò/narration sai | 02 normalize và 04 classify |
| Tên hợp lệ nhưng không có ID | 05 link: điều kiện panel, loại mention, status/reason; sau đó bank |
| Nhận nhầm người nói | 01 extraction association; luồng hiện tại không sửa speaker toàn chapter bằng model |
| ID/tên đúng trong bank nhưng export cũ chưa đổi | Snapshot export đã completed |
| Timeout/response lỗi | failures, targets và log của bước liên quan |

Để retry lỗi thực thi, dùng cùng run:

```powershell
.\venv\Scripts\python.exe run.py resume --run-dir "outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189"
```

Resume chỉ xử lý phần lỗi/chưa commit. Kết quả hợp lệ nhưng sai về nội dung, skipped hay unknown đã completed không được tự chạy lại để đoán khác. Không sửa trực tiếp artifact/target/config đã commit rồi resume: hash sẽ báo artifact/input thay đổi. Chỉnh tên qua bank; thay đổi xử lý/schema/config cần lượt chạy mới phù hợp.
