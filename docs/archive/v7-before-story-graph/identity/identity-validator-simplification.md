# Thiết kế giản lược validator khi tạo identity

> Historical documentation before chapter-story-graph-v7. See [current docs](../../../v7/README.md).

Đặc tả luồng/request/output đầy đủ: [build_identity mới](build-identity-redesign.md).

**Trạng thái: đã triển khai `chapter-name-binding-simple-v2`, ngày 24/10/2026.**
Đã kiểm tra bằng test không gọi model; chưa chạy inference thật hoặc Runpod.
Giữ nguyên cơ chế liên kết nhiều MAGI character vào một identity;
không thêm gộp identity hoặc hợp nhất MAGI ID.

## 1. Điều kiện tạo identity có tên

Một MAGI character được tạo identity có tên khi:

```text
crop_count_current_chapter >= 8
AND
model gắn được một tên được gọi/nhắc đến cho MAGI character đó
```

Ngưỡng mặc định là 8, vẫn có thể cấu hình. Cách đếm áp dụng cho MAGI character
đang xét trong chapter hiện tại, theo quy ước đã chốt trước đó.

- Crop count là số detection/quan sát thực trong chapter, không phải số ảnh
  đại diện đã giữ trong bank hoặc số ảnh gửi vLLM.
- Không dùng tổng appearance_count xuyên lịch sử để vượt ngưỡng chapter.
- Một detection không bị đếm lại vì cùng được dùng ở nhiều request/panel context.
- Tên có thể được gọi trực tiếp, nhắc đến, tự giới thiệu hoặc xuất hiện trong
  lời mô tả. Không bắt buộc đồng thời có cả direct_address lẫn reference.
- Tên dùng bản scan/OCR hiện hành đã sửa. Không đối chiếu OCR thô để bác bỏ tên.
- Model phải gắn tên cho MAGI cụ thể; việc một tên xuất hiện đâu đó trong chapter
  không tự tạo identity cho mọi character đủ crop.
- Tên chưa có alias group tiếp tục để review, chưa đưa sang identity theo Q14=B.

## 2. Không yêu cầu model chứng minh bằng crop/reference

Model có thể được cung cấp crop, panel, thoại, registry hoặc profile để suy luận.
Các dữ liệu đó là input giúp model, không phải một bộ citation bắt buộc trong output.

Validator không yêu cầu:

- Có owner_crop_ref, caller_crop_ref hoặc panel_ref trong kết quả.
- Chép utterance_id vào evidence_refs để quyết định được chấp nhận.
- Có một tập crop/panel cụ thể mới được xác nhận tên.
- Bằng chứng phải đủ một số lần gọi tên xác nhận riêng.
- Analyze đã đánh confirmed_person_name=true hoặc một mention_type chính xác
  mới cho phép model tạo identity.
- Reason phải được code chứng minh là khớp nội dung ảnh/thoại.

Code tự gắn thông tin nguồn đã cung cấp. Không trình bày các input đó thành
"model đã cite" nếu model không thực sự trích dẫn chúng.

## 3. Phần kiểm tra còn giữ

| Kiểm tra | Xử lý |
|---|---|
| MAGI ID tồn tại, dữ liệu đọc được | Lỗi ID/kiểu chỉ ảnh hưởng record liên quan |
| Crop count chapter >= ngưỡng | Chưa đủ thì chưa tạo identity mới, giữ để review |
| Có tên và model gắn tên cho MAGI | Chưa có thì giữ unnamed/chưa tạo named identity |
| Snapshot/version toàn vẹn | Bảo vệ dữ liệu khỏi hỏng hoặc dùng nhầm version |
| Bubble đã chốt dialogue | Không mang nhãn narrator_introduction |

Quy tắc dialogue chỉ xử lý nhãn không hợp lệ; không được dùng nó như một lý do
để loại cả request hoặc xóa các trường tên/owner còn dùng được. Không tự đổi
dialogue thành narration hoặc tự suy ra direct_address thay model.

Lỗi caller/reference tùy chọn không làm mất owner/name. Không có automatic retry.
Không yêu cầu con người xác thực bằng chứng crop để pipeline được tạo identity.

## 4. Không thay đổi cơ chế identity cũ

- Nhiều MAGI ID vẫn có thể thuộc cùng một identity theo cơ chế liên kết hiện có.
- Không hợp nhất hai identity thành một và không hợp nhất các MAGI entry trong bank.
- Tên/alias và dữ liệu hiện hành vẫn có lịch sử/version để review.
- Ngưỡng tạo mới không dùng để xóa identity đã tồn tại khi chapter sau ít crop.
- MAGI ít crop có thể được xử lý theo cơ chế liên kết với identity đã có; không
  dùng nó làm cớ tạo thêm identity mới hoặc cộng các character chưa liên kết
  tùy ý để vượt ngưỡng 8.

## 5. Phân biệt thiết kế mới với code hiện tại

Hiện `identity_binding.validate` còn yêu cầu source utterance, owner crop, panel,
evidence_kind và caller proof. Các điều kiện citation/ngữ nghĩa đó cần được bỏ
ở đường thực thi mới.

Hiện `identity_profiles.eligible` kiểm ngưỡng 8 unique observations sau khi đã
có named identity, tính trên các member của identity và có ngoại lệ cho identity
đã có trong graph. Do đó không thể nói code hiện tại đã thực hiện đúng gate
**tạo identity mới** được yêu cầu trong tài liệu này. Khi triển khai cần tách rõ
gate tạo mới và eligibility profile/graph, không chỉ sửa một error message.

## 6. Các ca chấp nhận

| Trường hợp | Kết quả tạo mới |
|---|---|
| 8 detection chapter, model gắn tên được nhắc đến, không trả crop refs | Được tạo identity |
| 12 detection chapter, tên được gọi, không có panel refs | Được tạo identity |
| 7 detection chapter, tên rõ | Chưa tạo mới; giữ tên/kết quả để review |
| 100 detection lịch sử nhưng chapter hiện tại chỉ có 3 | Không dùng lịch sử để vượt ngưỡng tạo mới |
| 10 detection chapter, chưa gắn được tên cho character | Chưa tạo named identity |
| Tên đã được scan sửa, không khớp nguyên văn OCR thô | Dùng tên hiện hành; không bác bỏ bằng OCR thô |
| Caller field lỗi nhưng owner/name đủ điều kiện | Giữ owner/name, đánh dấu riêng caller |
| Identity đã có, chapter mới ít crop | Giữ identity; không tạo lại hoặc xóa vì ngưỡng tạo mới |

Đọc cùng [scan mới](../text/scan-redesign.md). Đây là giản lược validator và làm
rõ gate tạo identity, không phải thiết kế một thuật toán nhận diện/merge mới.
