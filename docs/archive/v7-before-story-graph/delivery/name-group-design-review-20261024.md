# Vì sao hai người bị phân tán thành nhiều nhóm tên

> Historical documentation before chapter-story-graph-v7. See [current docs](../../../v7/README.md).

Đối chiếu run `20261023T133512Z-265e4c2f`, source scan/normalize và frozen
name-binding plan. Đây là kiểm tra thiết kế, không sửa kết quả hoặc gọi model.

## Các con số chính xác

- 45 occurrence tên được scan, gom fallback thành **20 nhóm chuỗi tên**.
- **9 nhóm** có occurrence đủ điều kiện gửi name-binding.
- Tổng **10 request**, vì nhóm `sasamori` được chia thành hai child request.
- 18 occurrence được gửi; 27 occurrence bị bỏ qua theo điều kiện hiện tại.
- 0 identity có tên được chấp nhận, do cả 10 request không qua validator.

Nhóm tên, request và identity là ba đối tượng khác nhau. Các nhãn trong errors.html
là canonical_name của packet, không phải danh sách nhân vật đã được xác nhận.

## Chín nhóm đang xử lý thực chất thuộc hai họ alias chính

Bảng dưới dùng hai identity người dùng xác định để đối chiếu; không ghi đè
registry/model output và không tự xác nhận các occurrence bằng bảng này.

| Identity cần gom | Các nhóm fallback được gửi | Request |
|---|---|---:|
| Sasamori Koyuki | `sasamori`, `sasamori san`, `sasa- mori`, `koyuki` | 5 |
| Mizushima Toru | `mizushima`, `mizu- shima`, `mizu- shima- sa`, `mizushima- sa`, `toru` | 5 |

`sasamori` có 4 occurrence đủ điều kiện, chia 2+2. Hai child packet có 4 và 8 ảnh;
hợp của chúng là 11 ảnh, vượt `max_images_per_prompt=10` của request cha.
Việc chia request là cơ chế đóng gói, không tạo identity hay nhóm tên mới.

## Nguyên nhân 1: tầng scan không cung cấp alias group

Artifact scan có `name_normalization.groups=[]`. Cả 45 candidate đều không có
alias_groups. Do đó việc gom short name/full name/OCR variant chưa được thực hiện
trong dữ liệu upstream được tái sử dụng.

Prompt scan có yêu cầu đề xuất nhóm alias, nhưng schema và xử lý cho phép trả
mảng groups rỗng; không có gate coverage bảo đảm các biến thể đã được gom hoặc
được giải thích vì sao chưa gom. Identity bootstrap cũng không có lượt LLM riêng
để tổ chức lại toàn bộ nhóm tên khi scan bỏ trống phần này.

Source: `scene.py:67`, `name_normalization.py:11`, `name_normalization.py:140`.

## Nguyên nhân 2: fallback chỉ dựa trên cách viết

`identity_binding_evidence.collect` ưu tiên alias group của scan. Nếu không có,
nó tạo nhóm bằng `persons.name_form(candidate['name']).base_name`.

`name_form` chỉ NFC, lowercase, gộp whitespace và nhận hậu tố có dấu gạch nối
như `-san`, `-chan`, `-kun`. Nó không giải bài toán cùng một người:

- `sasamori-san` và `sasamori-chan` có thể về cùng base `sasamori`.
- `sasamori san`, `sasamorisan`, `sasa- mori` vẫn thành base khác nhau.
- `koyuki` không tự nối với `sasamori`.
- `sasamori koyuki` không tự nối với `koyuki sasamori`.
- `toru`, `mizushima`, `mizu- shima` và tên đầy đủ không tự hợp nhất.

Đây là fallback chuỗi tên thận trọng, không phải bước nhận diện alias theo
ngữ cảnh như thiết kế mong muốn. Không nên dùng fuzzy string để gộp họ/tên tùy
tiện vì có thể gộp sai hai người cùng họ.

Source: `identity_binding_evidence.py:138`, `persons.py:10`.

## Nguyên nhân 3: sửa tên bằng span phần lớn bị từ chối

Scan có 15 đề xuất patch: 14 bị từ chối, 1 giữ trạng thái suspect, **0 confirmed
patch**. Vì thế các biến thể OCR/khoảng trắng vẫn còn. Patch spelling và alias
group là hai cơ chế khác nhau; sửa spelling cũng chưa đủ nối họ với tên riêng.

Ví dụ thực tế:

```text
Nguồn: IT'S SASA- MORI- SAN!
Tên phát hiện đúng: [5,20) = SASA- MORI- SAN
Model đề xuất:     [4,19) =  SASA- MORI- SA
```

Validator từ chối đúng vì offset chọn thừa khoảng trắng và thiếu ký tự cuối.
Đây không phải chỉ là validator quá chặt: vị trí model trả về thực sự sai.

Source: `name_normalization.py:59`.

## Tên đầy đủ có trong dữ liệu nhưng chưa làm điểm nối

Các nhóm `sasamori koyuki`, `koyuki sasamori`, `mizushima toru`, `toru mizushima`
đều tồn tại, nhưng occurrence của chúng bị đánh dấu missing_or_unknown_mention_type
nên không được gửi name-binding. Chúng cũng không được dùng để hợp nhất các nhóm
short-name khác vì alias group của scan đang trống.

Tầng reconcile chỉ hợp nhất các claim đã qua validator. Nó không tự quét lại
mọi tên để xây nhóm alias ngữ nghĩa. Khi toàn bộ request thất bại thì cũng không
có owner chung để nối các nhóm tên phía sau.

## Liên hệ với lỗi validator

Phân tán nhóm và lỗi validator là hai lỗi thiết kế/tích hợp khác nhau:

1. Alias group trống + fallback chuỗi làm mất liên kết họ/tên và phân tán ngữ cảnh.
2. Model vẫn phải trả đúng source/crop/panel refs và evidence_kind cho từng
   occurrence; các request vừa rồi vi phạm các điều kiện đó.

Gom về hai nhóm sẽ giúp model nhìn bằng chứng nhất quán hơn nhưng **không tự sửa**
lỗi reference, owner ngoài pool hoặc gán narrator_introduction cho dialogue.
Không thể kết luận chỉ giảm 10 request xuống 2 là sẽ hết lỗi.

## Hướng thiết kế phù hợp

1. Tách name entity/alias grouping khỏi việc gán tên cho MAGI: scan tạo nhóm
   nghĩa ổn định, ví dụ Sasamori Koyuki và Mizushima Toru, với occurrence cụ thể.
2. Dùng occurrence IDs do code cấp khi model chọn thành viên nhóm; tránh bắt model
   tính lại start/end chỉ để gom alias. Patch sửa ký tự vẫn được code kiểm span riêng.
3. Nếu groups rỗng nhưng nhiều short/full/OCR variants liên quan, ghi rõ grouping
   chưa hoàn thành để review, thay vì để fallback trông giống nhóm ngữ nghĩa đã xong.
4. Name-binding xử lý các occurrence trong cùng nhóm; chia child request khi cần
   nhưng mọi child giữ cùng group_id. Số request không bằng số identity.
5. Sửa response schema/validator contract riêng: typed refs, evidence_kind theo
   occurrence và quyết định rõ phạm vi loại bỏ khi một occurrence sai.

Không hardcode chapter luôn chỉ có hai identity. Scan còn nhắc `asahi`, `hijiri`,
`hjijiri`, `yuuki`, `yuki`, `koyuk`… Chúng không tạo request trong lượt này; cần
bằng chứng riêng để phân biệt tên người khác, nickname, OCR lỗi hay fragment.
Không tự gộp tất cả vào hai nhân vật chính chỉ vì tên xuất hiện ít.
