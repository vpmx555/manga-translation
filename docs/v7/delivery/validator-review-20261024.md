# Kiểm tra validator identity–relationship — 24/10/2026

Phạm vi: đọc source hiện tại và chạy lại hàm `identity_binding.validate` với
10 response/request đã lưu từ run `20261023T133512Z-265e4c2f`. Không gọi model,
không truy cập Runpod, không sửa artifact inference hoặc logic pipeline.

## 1. Validator làm gì

Validator là code Python kiểm tra cấu trúc, ID, bằng chứng và các quy tắc nghiệp
vụ. Nó không tự nhìn ảnh, không tự hiểu câu thoại, không chấm điểm độ đúng của
lý do, và không chạy một LLM thứ hai để phản biện kết luận.

Luồng hiện tại:

`frozen request + ảnh đã kiểm hash → model trả JSON → validate → decision receipt
→ reconcile các nhóm tên → registry/profile → relationship`.

Ảnh được kiểm tra tính toàn vẹn riêng trong `identity_runtime.images` trước khi
gọi model hoặc dùng lại decision. Validator ngữ nghĩa chỉ chấp nhận ID bằng chứng
thuộc packet thực sự đã gửi sau bước packing.

## 2. Name-binding validator

Source chính: `src/manga_pipeline/v6/identity_binding.py`, `schema` dòng 39,
`validate` dòng 56; gọi và xử lý lỗi trong `identity_runtime.py` dòng 119–208.

| Kiểm tra | Điều kiện hiện tại |
|---|---|
| JSON gốc | Đúng các field `group_status`, `group_reason`, `decisions` |
| Coverage | Mỗi occurrence đầu vào có đúng một decision, không thiếu/trùng |
| Decision | Đúng field, status hợp lệ, reason là chuỗi không rỗng |
| Reference | Mỗi evidence ref phải là ID crop hoặc câu context có trong packet |
| Chưa xác nhận | `unresolved`/`not_person_name` bắt buộc owner=null, caller=null, kind=unknown |
| Owner confirmed | Phải thuộc cả candidates của request và allowed_magi_ids riêng occurrence |
| Câu nguồn | evidence_refs phải chứa chính xác `occurrence.utterance_id` |
| Crop owner | Phải trích ít nhất một bank crop có MAGI ID đúng owner |
| Owner hiện diện | Phải có panel ref thuộc occurrence với annotation đúng MAGI owner |
| Caller tùy chọn | Nếu có thì phải hiện diện, nằm trong candidates, có cả bank crop và panel đúng caller |

`occurrence_id` và `utterance_id` là hai loại ID khác nhau. Trả đúng
`occurrence_id` không thay thế yêu cầu chép `utterance_id` vào evidence_refs.
Crop của người nói cũng không thay thế crop của người được gán tên.

Các loại bằng chứng được chấp nhận:

| evidence_kind | Ràng buộc |
|---|---|
| direct_address | Analyze đã gán mention_type=direct_address và content_type=dialogue; nếu owner không hiện diện thì phải đúng off_panel_magi_id đã cung cấp |
| self_introduction | mention_type=introduction, dialogue, owner hiện diện; không mâu thuẫn speaker đã biết; caller chỉ null hoặc chính owner |
| narrator_introduction | content_type=narration **hoặc** speaker_kind=narrator; owner hiện diện; caller phải null |

Một câu thoại nhắc đến người thứ ba không được đổi thành narrator_introduction
chỉ vì model suy ra tên người đó. Analyze vẫn là nguồn quyết định mention_type.

## 3. Phạm vi loại bỏ và trạng thái pipeline

`run_jobs` gọi `validate(value, wire)` cho **toàn bộ response**. Hàm validate
dừng ở lỗi đầu tiên. Một decision sai khiến cả request được lưu `status=failed`,
không lưu response đó thành `result` đã duyệt trong decision receipt. Raw response
vẫn được giữ ở request journal/cache.

Reconcile chỉ đọc outcome `completed`. Các decision khác trong cùng request
không được cứu riêng. Request/child khác hợp lệ vẫn được dùng. Không tự retry
model. Receipt failed được dùng lại khi resume cùng job/hash; không tự POST lại.

Lỗi ngữ nghĩa thông thường không làm dừng toàn pipeline: được đưa vào
`inference_failures`/`needs_review`. Stage có thể commit và run kết thúc
`completed_with_review`, dù chưa có identity hay cạnh quan hệ.

Qua validator vẫn chưa bảo đảm được chấp nhận: reconcile còn chặn nhóm alias
suspect, xung đột chủ sở hữu tên đã biết, ghép hai identity đã xác nhận và cannot-link.
Một child kết luận nhóm alias suspect có thể chặn cả nhóm theo thiết kế.

## 4. Đối chiếu lần chạy r3

Chạy lại validator hiện tại trên dữ liệu đã lưu cho kết quả:

| Lỗi đầu tiên của mỗi request | Số request |
|---|---:|
| Confirmed name needs its source utterance and owner crop | 8 |
| Unresolved name cannot supply an owner or caller | 1 |
| Name owner is outside the occurrence candidate pool | 1 |
| Qua validator | 0 |

Đây là lỗi **đầu tiên**, không phải toàn bộ lỗi trong từng response. Không nên
hiểu mô tả tổng quát trước đây thành cả 10 request cùng phát sinh một exception.

Đếm riêng các điều kiện trên 18 decision: model tự đánh dấu 16 confirmed, cả 16
thiếu source utterance ref; 7 thiếu owner crop; 1 thiếu panel owner hiện diện;
10 dùng evidence_kind không khớp analyze; 7 thiếu bằng chứng caller tùy chọn.
Các nhóm số đếm trùng nhau; không cộng lại thành số request/decision lỗi.

Ví dụ thật:

- `WHAT HAPPENED TO SASAMORI- SAN? ...`: model dùng narrator_introduction cho
  một lời thoại nhắc người thứ ba; có occurrence chỉ cho phép MAGI 2 nhưng model
  trả owner MAGI 5. Validator kiểm tra theo candidate pool của occurrence.
- `IT'S SASA- MORI- SAN!`: model trả unresolved, owner=null nhưng caller=MAGI 6.
  Validator hiện cấm caller ở decision unresolved, nên cũng từ chối request.

## 5. Các điểm cần cân nhắc trong thiết kế

1. Schema gửi model chỉ khai báo evidence_refs là array of string; không enum
   các ref, không buộc câu nguồn/crop owner/panel. JSON có thể đúng schema nhưng
   chắc chắn bị validator Python từ chối. Schema owner dùng candidates toàn
   request, còn validator dùng thêm pool riêng occurrence; đây là khoảng lệch nữa.
2. Thông báo thiếu câu nguồn/crop owner dùng một điều kiện OR, nên không nói rõ
   thiếu loại nào. Validator chỉ báo lỗi đầu tiên, che các lỗi phía sau.
3. Bất kỳ lỗi nào cũng loại cả request, kể cả lỗi ở caller tùy chọn. Đây là phạm
   vi loại bỏ rộng hơn một decision; cần quyết định rõ nếu muốn đổi sang salvage
   từng occurrence. Không tự thay đổi cơ chế trong đợt kiểm tra này.
4. Có thể cân nhắc response với field typed riêng cho source, owner crop, panel,
   caller và evidence_kind giới hạn theo từng occurrence. Vẫn cần validator Python
   kiểm tra đối chiếu. Không tự bổ sung evidence hoặc hạ điều kiện để ép chấp nhận.
5. Ref tồn tại và đúng loại chưa chứng minh hình ảnh/câu thoại thực sự hỗ trợ kết
   luận. Reason chỉ được kiểm kiểu/không rỗng; chất lượng suy luận còn phụ thuộc model.

## 6. Profile và relationship validator

`identity_profiles.py:101`: đúng identity và đủ field; unknown phải giữ value/basis
unknown; thuộc tính confirmed cần bằng chứng thoại; suy đoán ngoại hình phải có
crop; vai trò xã hội không được xác lập từ ảnh; giá trị tuổi/presentation thuộc enum.
Profile kế thừa chỉ được giữ nguyên khi chỉ viện dẫn version trước.

`relationship_v2.py:126`: mỗi pair trả đúng hai cạnh có hướng A→B và B→A, không
trùng hoặc tự nối. Xưng hô confirmed cần bằng chứng thoại; suy đoán tuổi phải
provisional và tham chiếu cả hai profile; unknown không được kèm xưng hô đã chốt.
Từ xưng/gọi phải qua kiểm tra từ vựng/tên của đúng identity. Quan hệ xã hội
không được xác lập chỉ từ ảnh, phải có ref thoại và đúng pair.

Trong r3, name-binding không có kết quả hợp lệ nên chưa tạo profile và không có
pair relationship để gọi model. Không thể dùng lần này kết luận chất lượng hai
validator phía sau hoặc chất lượng model relationship.
