# Điều tra MAGI 2 / 5 / 15 — Iris Zero r9

Run: `outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898`.

## Kết luận

Có lỗi gán tên/identity thật trong kết quả đã lưu. HTML đang đọc đúng số MAGI và
trường `name` của artifact; chính các trường này đã mâu thuẫn với ảnh.

| MAGI | Tên trong analyze/reanalyze | Bằng chứng kiểm tra |
|---|---|---|
| 2 | `sasamori` | Crop là nam tóc đen. Ảnh event trang 10 cho thấy cô gái MAGI 5 gọi “Mizushima Toru-san” với nhân vật MAGI 2. Bằng chứng này ủng hộ MAGI 2 là Toru, trái với nhãn Sasamori. |
| 5 | `mizushima` | Crop là nữ tóc sáng, có nơ. Registry lưu cả `mizushima-san` và `koyuki` vào cùng person. Trang 31 cho thấy nhân vật MAGI 15 gọi “Koyuki” với cô gái MAGI 5. Bằng chứng ủng hộ MAGI 5 là Koyuki. |
| 15 | `mizushima toru` | Crop là nhân vật tóc sẫm dài ngang mặt, khác crop MAGI 2. Không có cơ sở coi MAGI 2 và 15 cùng là Toru. Chưa kết luận tên đúng của MAGI 15 trong cuộc điều tra này. |

Đây không chỉ là trường hợp một nhân vật bị MAGI tách thành hai ID: dữ liệu còn
gán tên Koyuki/Sasamori sang MAGI 2 và gán hai nhóm tên khác nhau vào MAGI 5.
Chưa thực hiện audit toàn bộ crop của từng bank; không kết luận mọi lần xuất hiện
của mỗi MAGI đều sạch lỗi clustering.

## 1. Sai ngay trong phản hồi model ở graph

File `05_graph/requests/c01babdb6b7c71979c97bc6af748266a2c91a7a1c9bab09293e3877eb671861b.json`
ghi nguyên phản hồi:

- `Toru Mizushima` → `possible_person_ids: ["p14"]`, confidence 10.
- `Koyuki Sasamori` → `possible_person_ids: ["p1"]`, confidence 10.

`05_graph/reference-ledger.json` giải mã:

- `p14` → `person:11b8722b696eaedd795d` → MAGI 15.
- `p1` → `person:a2f1956f505cb644a8e8` → MAGI 2.
- `p4` → `person:bbd1cdc5e54921165151` → MAGI 5.

Các ID trong artifact sau giải mã khớp ledger. Không phải HTML hoán đổi số MAGI,
cũng không thấy lỗi giải mã ledger ở hai liên kết này: model đã chọn sai compact
person ID trong phản hồi graph.

Snapshot `snapshots/persons-v6.json` có 0 person có `primary_name`.
Catalog graph vẫn có `name: null` cho cả ba MAGI. Sai lệch ở thời điểm này nằm
trong **giả thuyết nối tên với person**, chưa nằm trong tên registry.

Packet graph là dữ liệu text/bbox/observations, không kèm crop nhận diện trực quan.
MAGI 15 có 13 observation ở các trang 30–43, nhưng graph đã nối nó với Toru trong
scene ở trang 8–10. Đây là lựa chọn chưa có chứng cứ trực quan tại các trang đó.

## 2. Analyze tiếp nhận giả thuyết sai và biến thành tên registry

Analyze nhận `chapter_context` chứa hypotheses của graph. Request tại
`scene:6:event:1` vẫn cung cấp MAGI 15 trong `visual_people.people` dù
`cluster_refs: []` trên trang event. MAGI 2 và MAGI 5 có detection tại trang 10.
Điều này cho phép model chọn person toàn chapter dù không có detection local.
Việc graph gây thiên lệch cho analyze là suy luận phù hợp với chuỗi artifact;
chưa thực hiện thí nghiệm bỏ context để đo nhân quả.

Các điểm tạo tên đầu tiên trong analyze:

| MAGI | Câu nguồn | Liên kết được ghi |
|---|---|---|
| 2 | `p9:g3:u` — “IT'S SASA-MORI-SAN!” | mention `c77a61c5ffa7ec05b5aa` nối Sasamori vào MAGI 2, target confidence 10. |
| 15 | `p10:g3:u` — “MIZUSHIMA TORU-SAN...” | mention `c507b5c8e32ba17e81e1` nối Toru vào MAGI 15, không có recipient cluster, target confidence 10. |
| 5 | `p24:g4:u` — “MIZU-SHIMA-SA...” | mention `3b88bfeda0a0b0c6fa1c` nối Mizushima vào MAGI 5, target confidence 10. |
| 5 | `p31:g3:u` — “...KOYUKI...?” | mention `ea5691cb0d3b7c8f0f06` nối thêm Koyuki vào cùng MAGI 5, target confidence 10. |

Mọi ID câu ở bảng có tiền tố `b68ed9e490d577e6:`.

Hai mâu thuẫn rõ:

- Trang 10, `scene:6:event:1`: anchor chỉ người nói là MAGI 5; model đổi thành
  MAGI 2 và chọn người nghe MAGI 15. Evidence còn viết “Koyuki (MAGI 2)”. Ảnh
  input hiển thị cô gái là MAGI 5, nhân vật nam được gọi tên là MAGI 2.
- Trang 24, `scene:18:event:1`: speaker là MAGI 5, addressee là MAGI 2, nhưng
  mention “Mizushima-san” lại trỏ MAGI 5. Evidence nói “Koyuki gọi tên họ của
  Toru”, trong khi target person chính là người nói. Liên kết sai này được đăng
  ký thành tên và còn được ghi calling event với caller = recipient = MAGI 5.

## 3. Vì sao tên sai bám lại

Code hiện tại giải thích đúng cấu trúc artifact quan sát được:

- `src/manga_pipeline/v6/merge.py:55–57`: mention được xác nhận có target person
  thì gọi `persons.add_name`; chưa đối chiếu mâu thuẫn nhóm tên Koyuki/Toru hay
  mâu thuẫn target mention với người nghe trong ví dụ trang 24.
- `src/manga_pipeline/v6/persons.py:57`: name form được ghi `status: confirmed`.
- `src/manga_pipeline/v6/persons.py:83`: `primary_name` lấy tên đầu tiên nếu chưa
  có. Vì thế MAGI 5 nhận `mizushima` trước và giữ tên này sau khi có `koyuki`.
- `src/manga_pipeline/v6/event_analysis.py:75`: person có bank ID vẫn được đưa vào
  context ngay cả khi không có cluster local; đây là thiết kế cho phép chọn
  người ngoài ảnh, không phải bằng chứng rằng lựa chọn đó đúng.

Tổng cộng 27 câu có speaker model khác locked source anchor. Đây là tín hiệu
cần rà soát, **không phải kết luận cả 27 câu đều sai**: anchor có thể sai và
policy hiện tại cho phép inference ghi đè.

Các tham chiếu code trên là working tree được đọc khi điều tra; chưa có bundle
source frozen để đối chiếu byte-for-byte. Kết luận về những lựa chọn sai dựa trực
tiếp trên request, response, ledger và artifact của run, không phụ thuộc giả định
working tree giống hệt code đã chạy.

## 4. Reanalyze không sửa bảng tên

`07_reanalyze/result.json.analysis.story_graph.characters` giống hệt bảng
characters của analyze, bao gồm ba nhãn sai này.

Reanalyze chấp nhận 6 mục: 4 sửa câu và 2 mục khác; bốn sửa câu chỉ đổi phân loại
semantic/evidence/status, không đổi speaker/listener/mention của các trường hợp
đang điều tra. Có 23 đề xuất bị từ chối. Một số đề xuất vẫn gọi MAGI 15 là Toru,
nên không có bằng chứng chapter review đã nhận ra vấn đề gán tên toàn cục.

## 5. Vai trò của HTML

Viewer lấy `magi_ids` và `name` từ `story_graph.characters`, không suy luận tên.
Việc đổi nhãn từ `person:…` sang “MAGI · tên” đã làm lộ lỗi sẵn có. Tuy nhiên,
hiển thị tên suy luận như tên chắc chắn cũng gây hiểu nhầm: person vẫn có
`status: provisional` và graph ban đầu chỉ đưa ra hypotheses.

## Bằng chứng ảnh mở trực tiếp

- [Trang 10: cô gái MAGI 5 gọi tên Toru, nam MAGI 2](../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/images/0b8264a314621e576cf45649cbd25d76eb4ecdaf7cfc38768b6ffab97c734128.jpg)
- [Trang 31: MAGI 15 gọi Koyuki, cô gái MAGI 5](../../outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/local-verified/20261026T130516Z-344ff898/06_analyze/images/abf2da76ef475b76a481f6a22b8047c4a5da485a56a5c8efffdc46f6d94cd998.jpg)

Crop bank nằm dưới `outputs/runpod-iris-zero-ch1-reanalyze-v7-l40s-20261026-r9/live/banks/iris-zero/crops/`:

- MAGI 2: `character-2-36d6fa571900.png`.
- MAGI 5: `character-5-ed97dddd1379.png`.
- MAGI 15: `new-84a01fd7-a6ca1771ba25.png`.

## Hướng sửa đề xuất, chưa thực hiện

1. Tách nhãn MAGI với tên được suy luận; hiển thị tên dưới dạng claim kèm nguồn,
   không coi primary_name đầu tiên là kết luận đã xác minh.
2. Giữ liên kết tên–person của graph là giả thuyết. Khi analyze quyết định khác
   hình/anchor, cần evidence tương ứng và đưa mâu thuẫn vào chapter review.
3. Review việc đăng ký tên: phát hiện một MAGI nhận hai nhóm tên khác nhau, cùng
   nhóm tên trỏ nhiều MAGI, direct-address target tự trỏ speaker trong tình huống
   như trang 24. Đây nên là tín hiệu review, không phải luật cấm self-address.
4. Cho reanalyze thấy các mâu thuẫn identity kèm crop và các nguồn độc lập; nếu
   sửa identity phải cập nhật nhất quán mentions, name registry và graph catalog.
5. Không vá nhãn riêng trong HTML để che lỗi. Không đổi tên MAGI 15 thành một tên
   suy đoán, không sửa checkpoint cũ hay tự merge MAGI 2/15.

Điều tra chỉ đọc JSON/code/ảnh đã lưu và tạo báo cáo này. Không chạy model,
không chạy pipeline, không truy cập Runpod, không sửa bank hay artifact của run.
