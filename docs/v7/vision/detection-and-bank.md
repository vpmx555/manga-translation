# Detection, geometry và bank nhân vật

[← Chỉ mục](../README.md)

## Layout và text extraction

Koharu cung cấp panel/bubble mask và thông tin layout. MAGI chịu trách nhiệm
text boxes, character boxes, liên kết text–character thô và embedding nhân vật.
GLM-OCR đọc text crop từ bước này; OCR không thay text detector.

| Ngưỡng mặc định | Giá trị | Quyết định |
|---|---:|---|
| `layout.panel_threshold` | 0.50 | Candidate panel |
| `layout.bubble_threshold` | 0.45 | Candidate bubble |
| `text_detection_threshold` | 0.45 | MAGI text detection |
| `character_detection_threshold` | 0.30 | MAGI character detection |
| `character_character_matching_threshold` | 0.81 | Matching/clustering trong MAGI |
| `text_character_matching_threshold` | 0.25 | Liên kết thô text–character |
| `text_classification_threshold` | -0.5 | Ngưỡng logit text classification |

Không dùng chung ý nghĩa các ngưỡng trên. Đặc biệt `0.81` của MAGI khác
`character_bank.max_distance=0.55`: một tầng matching nội bộ MAGI, một tầng hậu
matching vào bank bằng distance. Threshold logit cũng không phải xác suất.

## Bubble geometry và normalize

Geometry giữ group và nhiều part, contour/body, tail/bridge, panel membership,
mapping text và phần biên được dựng lại. Hint hình học chỉ dùng ảnh/mask; analyze
kết hợp hint với text/ngữ cảnh để quyết định thoại/thought/caption/effect.

Normalize xây utterance theo group, giữ source boxes/parts để cuối cùng đặt chữ.
Một utterance được analyze/dịch như một câu toàn vẹn; không dịch riêng từng
contour fragment. Raw OCR và association được giữ để audit, không bị thay thành
speaker inference rồi mất dấu nguồn.

Các tham số geometry, margin và font có trong [config reference](../foundation/configuration.md).
Không diễn giải confidence heuristic như xác suất đã calibration.

## Bank ID và assignment

Policy `page-frozen-frequent-id-then-distance-v1` đóng băng ứng viên/prototype
cho một lượt assignment ở trang, ưu tiên bank ID có lịch sử xuất hiện nhiều,
rồi xét distance/ứng viên theo policy. Các ràng buộc cạnh tranh detection trong
cùng trang/panel được giải quyết theo assignment, thay cho việc đơn giản gán
mọi detection theo thứ tự duyệt.

Hậu matching mặc định nhận candidate khi distance đáp ứng ngưỡng **0.55**, với
`min_margin=0`. `pending_max_page_gap=1` chỉ cho phép khoảng cách một trang ở
đường tái sử dụng pending tương ứng, tức hai trang liên tiếp; không giới hạn
toàn bộ bank đã có chỉ được nhớ một trang.

## Chọn prototype và crop

Policy `closest-2-to-16-paired-mean-v2` chuẩn hóa L2 vector, chọn nhóm compact
bằng khoảng cách cặp trong nhóm và tie-break ổn định, rồi lấy trung bình và
chuẩn hóa L2 lại. `closest_indices` xét các nhóm lân cận từ từng vector làm tâm,
so sánh mean pairwise distance của toàn nhóm. Đây là lựa chọn consensus theo
embedding, không phải model chấm chất lượng ảnh riêng.

Số vector/crop mặc định min2, max16 **nếu có đủ quan sát**. Nhân vật mới chỉ có
một crop không được nhân đôi để giả đủ hai. Bank giữ crop ghép với các embedding
đã chọn; không giữ 16 ảnh bất kỳ còn prototype dùng một tập khác. Với không
quá16 vector hợp lệ, tập chọn có thể gồm toàn bộ; thêm crop không tự bảo đảm
prototype loại được mọi outlier.

Nhiều crop có ích khi có thêm góc nhìn tốt và nhóm embedding đủ chặt; crop mờ,
chibi hoặc nhân vật khác lẫn vào không tự biến thành thông tin tốt. Các tham số
min/max và strategy hiện được validator khóa đúng policy2–16; muốn đổi chúng
cần thay code/policy và freeze batch mới, không chỉ sửa JSON.

## Bank matching khác identity merge

Bank tìm cùng nhân vật từ thị giác khi extraction. `build_identity` sau analyze
có thể liên kết MAGI ID khác nhau vào cùng identity có tên; không renumber hay
gộp file embedding bank. Ngưỡng visual identity **strictly `<0.38`** độc lập với
distance0.55 của bank và threshold0.81 của MAGI.

Khi so sánh diagnostic, giữ nguyên input, source revision, seed bank, page order,
policy prototype và assignment. So hai run bắt đầu từ bank đã tiến hóa khác nhau
không cô lập được tác động của một threshold.
