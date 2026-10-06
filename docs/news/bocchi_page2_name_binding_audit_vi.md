# Lỗi gán tên trang 2 — Bocchi, run 4090

Run kiểm tra: `outputs/runpod-4090-20261005/bocchi-the-rock/chapter-001/20261005T154833Z-61352b66`.

## Kết luận có bằng chứng

VLM **đã được gọi** ở bước gán tên trang 2. Các quyết định checkpoint đều ghi `vlm_introduction_panel`; không đi qua nhánh tự gán `one_stable_id_one_new_name` tại trang này.

| Tên | VLM chọn | ID đúng theo ảnh có nhãn |
| --- | --- | --- |
| Shima Iwashita | 6 | 6 |
| Kikuri Hiroi | 2 | 4 |
| Eliza Shimzu | 1 | 5 |

Trong ảnh extract, ID 1 và 2 thuộc đám đông, không phải hai người được giới thiệu. ID 4 là người hát/chơi bass ở giữa; ID 5 là guitarist bên phải; ID 6 là drummer ở góc trên phải. Không phải cả ba quyết định đều sai: Shima → 6 đúng theo ảnh này. Tên Eliza đang giữ cách OCR đọc `Shimzu`; bảng này không xác nhận cách viết tên chuẩn.

## Phản hồi VLM đã lưu

Kikuri: `character_id=2`, nhưng reason nói “The text identifies character 4 as Kikuri Hiroi, and character 2 is the most likely visual match for this character.”

Eliza: `character_id=1`, nhưng reason nói “The text identifies character 5 as Eliza Shimzu, and character 1 is the most likely visual match for this character.”

Như vậy model nhận thấy ID 4/5 liên quan tới tên nhưng vẫn chọn ID 2/1. Đây là lỗi suy luận và gán ID trong đầu ra VLM; không phải thiếu lời gọi VLM. Phản hồi này là dữ liệu cache thực tế, không phải suy đoán từ tên đã ghi vào bank.

Checkpoint: `04_analyze/targets/9c7abeb83ce77c3df872ad5b9a3265bdf9a7e4cf3fb13c23b468661b2fefce09.json` (`panel-decision:9:40c0ffc00e53a2b2:p2:0`). Shima ở checkpoint `8afbdb8e3b58ffbd064d73d4f19bd93e52e3acece92265707db93b4cbdb89274.json`.

## Vì sao đầu vào dễ gây sai và code không chặn?

- MAGI trả một panel cho toàn trang 2. `panel_candidates` lấy cả người biểu diễn và đám đông làm ứng viên ổn định, vì tất cả nằm trong panel này. Có cả ID 1, 2, 3, 4, 5, 6 để VLM chọn.
- `panel_image` vẽ nhãn bằng font mặc định trên ảnh lớn rồi thu ảnh về cạnh tối đa 768 px. Nhãn nhỏ cũng bị thu nhỏ. Đây là yếu tố làm đầu vào khó đọc, chưa có thí nghiệm đối chứng để khẳng định nó là nguyên nhân duy nhất.
- Request cung cấp tên và source speaker ID, nhưng không cung cấp bbox vùng chữ giới thiệu cho từng tên; model phải tìm quan hệ tên–người trên toàn ảnh. `priority_character_ids` chỉ là gợi ý, không phải ràng buộc.
- Validator chỉ kiểm tra đủ candidate, ID thuộc tập stable ID trong panel và reason là chuỗi ngắn. ID 1/2 đều hợp lệ về schema; sự mâu thuẫn trong lý do không bị kiểm tra. Sau đó kết quả được coi là `resolved` và ghi vào bank.
- Không có nhánh đánh giá độ tin cậy của panel trước khi ghi tên. Nhánh một ID/một tên còn bỏ qua VLM, nhưng nhánh đó không gây ra hai lỗi cụ thể của trang 2.

## Sửa đúng phạm vi

1. Khi panel quá rộng hoặc chứa nhiều nhóm người, dùng vùng chữ giới thiệu, vùng nhân vật và ảnh toàn trang làm bằng chứng; cung cấp crop ứng viên riêng, nhãn lớn và rõ.
2. Yêu cầu VLM chọn `detection_index` đang nhìn thấy, suy ra ID bằng code; trả bằng chứng theo tên và detection cụ thể. Không cho model tự “tìm người giống” ở một ID khác như phản hồi hiện tại.
3. Ghi các trường hợp mơ hồ hoặc bằng chứng không nhất quán thành `unresolved`/review; không ghi tên tự động chỉ vì response đúng schema.
4. Một stable ID/một tên không đủ để khẳng định tên thuộc người đó nếu câu nói giới thiệu người khác hoặc crop thiếu chủ thể; cần kiểm tra thêm trước khi bỏ qua VLM.
5. Sửa quyết định tên còn cần tái tạo các dữ liệu phụ thuộc như export và translation. Không chỉ sửa metadata rồi coi kết quả cũ đã đúng.

Chưa sửa bank hay gọi lại model trong lần kiểm tra này. Pod vẫn Stop; dùng ảnh, cache và checkpoint đã tải về PC.
