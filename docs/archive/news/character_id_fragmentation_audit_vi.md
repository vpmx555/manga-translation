# Kiểm tra nhân vật bị tách nhiều ID — 05/10/2026

## Phạm vi và kết luận

Kiểm tra dữ liệu hiện có của `bocchi-the-rock/chapter-001/20261005T090450Z-537ce530`: 31 trang, bank có 38 ID chính thức và 52 bản ghi pending. Đây là số bản ghi nhận diện, không phải số nhân vật thật. Chưa có xác nhận từ người dùng về tên truyện; Bocchi là bản chạy gần nhất có mức phân mảnh lớn.

Đã xem trực tiếp crop: ID **7, 10, 15, 21** đều là nhân vật tóc tết, mái ngang, khuyên tai, mặc áo có sọc. ID 9 và 13 là góc quay lưng cùng kiểu áo/tóc; cần đối chiếu đủ ảnh trước khi quyết định gộp toàn bộ prototype. Không tự động sửa bank hoặc các kết quả đã xuất.

## Nguyên nhân có bằng chứng

1. `src/manga_pipeline/bank/assignment.py:_resolve_existing` yêu cầu đồng thời khoảng cách <= 0,65 và chênh lệch với ứng viên thứ hai >= 0,08. Một ảnh giống ID cũ vẫn bị từ chối nếu ứng viên thứ hai cũng gần. Bank đang có nhiều ID trùng làm điều kiện chênh lệch khó đạt hơn nữa.
2. Nhánh `assign_names_to_characters` coi việc không ghép được là cơ hội tạo nhân vật mới: nếu nhóm có ít nhất hai detection thì tạo ID ngay; nếu khớp pending ở trang sau thì promote thành ID. Không phân biệt “có ứng viên rất gần nhưng mơ hồ” với “nhân vật thực sự mới”. Vì vậy mơ hồ biến thành nhiều ID chính thức.
3. Quy tắc `used_by_page_panel` chặn một ID xuất hiện trong hai cluster cùng panel. **Trang 5** có ID 9 ở hình lớn và ID 7 ở hình minh họa nhỏ trong cùng panel cuối. Truyện có thể vẽ lại một người trong inset/hồi tưởng/hình minh họa, nên ràng buộc này không đúng trong mọi trường hợp. Xem [trang 5 có nhãn](../../outputs/bocchi-the-rock/chapter-001/20261005T090450Z-537ce530/01_extract/visualizations/page_5.png).
4. Bank không có postprocessor (`postprocessors: []`) để giải quyết trường hợp mơ hồ; tên và nội dung hội thoại không được dùng trong bước ghép embedding này.

Tất cả detection trong 31 trang đều có panel theo phép kiểm tra tâm bbox. Vì vậy lỗi gom các detection không có panel vào cùng khóa `None` không giải thích bản chạy này.

## Kiểm tra số học từ embedding đã lưu

Dùng đúng công thức khoảng cách hiện tại, so prototype với các ID có số nhỏ hơn (ứng viên đã tồn tại trước ID mới):

| Prototype | Ứng viên gần nhất | Khoảng cách | Ứng viên thứ hai | Khoảng cách | Chênh lệch |
| --- | --- | --- | --- | --- | --- |
| ID 10, prototype 0 | ID 7 | 0,3564 | ID 4 | 0,3837 | 0,0273 |
| ID 15, prototype 0 | ID 13 | 0,2376 | ID 12 | 0,2880 | 0,0504 |
| ID 21, prototype 0 | ID 17 | 0,3029 | ID 20 | 0,3107 | 0,0078 |

Cả ba đều đạt ngưỡng khoảng cách nhưng không đạt margin 0,08. Đây là phép kiểm tra bank cuối đã lưu, **không phải log chính xác tại thời điểm cấp ID**: prototype có thể đã được cập nhật và observation thật dùng vector trung bình của cluster. Dữ liệu này xác nhận cơ chế gây phân mảnh, không cho phép kết luận mọi quyết định cấp ID đều có cùng điểm số trên.

## Hướng sửa

### Vì sao ID 7 không dùng lại ID 4 ngay lúc xuất hiện?

Đã đối chiếu crop ID 4 và ID 7: cùng nhân vật tóc tết, mái ngang và khuyên tai. ID 4 xuất hiện ở trang 1–2; ID 7 được tạo ở trang 3 từ cluster 0 gồm hai detection. Trước observation đầu tiên của trang 3 chưa có ID bị chặn bởi quy tắc panel.

Có thể tái dựng query ban đầu của ID 7 từ hai vector đầu trong `character_7`: lúc tạo lưu hai vector, sau đó thêm vector ở trang 5; hàm `_diverse_indices` giữ nguyên thứ tự khi chưa vượt 8 vector. ID 4 có 4 vector từ trang 1–2 và không nhận thêm detection sau đó; ID 2 cũng chỉ xuất hiện trang 1–2, có 2 vector. Vì vậy các vector dùng cho hai ứng viên này vẫn được giữ đầy đủ.

Query trung bình đã chuẩn hóa của hai vector đầu ID 7 cho điểm **ID 4 = 0,40107**, **ID 2 = 0,47668**, chênh **0,07561**. ID 4 đạt điều kiện khoảng cách 0,65, nhưng chênh lệch không đạt 0,08. Dù prototype các ứng viên khác có thể đã đổi về sau, sự tồn tại của ứng viên ID 2 gần như vậy đã đủ khiến ID 4 không thể đạt margin yêu cầu. Code sau đó thấy cluster có hai detection và tạo ID 7. Đây là trường hợp có bằng chứng cụ thể về việc ID cũ bị từ chối rồi sinh ID mới.

Như vậy không phải ID 4 đã hết hạn hoặc không có sẵn; bank không có điều kiện loại ứng viên vì lâu không sử dụng. Cần phân biệt trường hợp tái dựng này với bảng so prototype bank cuối ở phần trên.

- Phân biệt kết quả ghép chắc chắn, mơ hồ và mới; trường hợp mơ hồ phải giữ trạng thái chờ giải quyết thay vì tự tạo ID chính thức.
- Xử lý hình inset/nhân vật được vẽ lại trước khi áp dụng ràng buộc khác người trong cùng panel; không bỏ toàn bộ ràng buộc vì có thể ghép nhầm hai người thật.
- Ghi trace ứng viên, điểm, margin và lý do chặn cho từng observation để kiểm tra quyết định thực tế.
- Dọn các ID trùng đã xác nhận bằng thao tác có bản sao và ánh xạ ID xuyên suốt bank, raw result và các bước sau. Không chỉ đổi nhãn hiển thị.
- Không chỉ giảm margin hoặc tăng ngưỡng khoảng cách: cách đó có thể ghép nhầm nhân vật khác mà chưa giải quyết việc mơ hồ sinh ID mới.

Kiểm tra này dùng ảnh, JSON và embedding có sẵn trên PC; không thuê GPU, không chạy lại extraction gần hai giờ của bản chạy hiện tại.
