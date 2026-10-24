# Thử phân loại ngữ nghĩa và ID trong cùng lượt LLM

Đã chạy chẩn đoán riêng bằng `gemma4:e4b-it-q4_K_M`, `think=false`, không ảnh. Không chạy lại pipeline sản xuất, không ghép panel hoặc cập nhật bank. Kết quả là output thô trước sửa cặp; chưa áp dụng retry/fallback và bộ nhớ tên của thiết kế mới.

## Phạm vi và kết quả

Chín case gồm năm câu từ chương mẫu và bốn câu đối chứng tự tạo. Context tối đa năm câu. Các câu thực dùng tập ID ứng viên từ run hiện tại; tên/alias đọc từ bank hiện tại. Các đối chứng dùng ID giả lập 1 và 3. Đây không phải bộ đánh giá độ chính xác.

| Lượt | Schema | Kết quả | Lượt gọi LLM | Thời gian |
| --- | --- | --- | --- | --- |
| 01 | Một schema chung, không giới hạn số mention riêng từng target | 6/9 câu trả thành công; batch đầu vẫn sai coverage tên sau retry | 4 | 315.07 giây |
| 02 | Một nhánh/target, số mention đúng target; không liệt kê tổ hợp nhãn | 9/9 câu trả thành công; không lỗi coverage | 3 | 307.84 giây |

Lượt 02 vẫn có bốn target mâu thuẫn cặp. Thành công ở bảng trên chỉ là đủ output hợp lệ về cấu trúc, không có nghĩa tất cả dự đoán đúng.

| Case | Content type | Speaker | Addressee type/IDs | Vai trò tên | Lỗi cặp |
| --- | --- | --- | --- | --- | --- |
| P01/T01 | narration | 1 | audience / [3] | Không có ứng viên | audience phải đi với public_audience |
| P02/T23 | narration | 1 | audience / [3] | Arase Mahoru: introduction, target null | audience phải đi với public_audience |
| P03/T06 | narration | 3 | audience / [1] | Madoi Ayame: introduction, target null | audience phải đi với public_audience |
| P02/T02 | narration | 3 | unknown / [unknown] | MAD01 AYAME: introduction, target null | Không có |
| P04/T03 | dialogue | 3 | single / [unknown] | Ayase-San: direct_address, target null | Không có; danh tính người nghe vẫn chưa xác định |
| Giới thiệu người khác bằng lời nói | narration | 1 | group / [unknown] | Hana Mori: introduction, target null | Không có |
| Tự giới thiệu | narration | 1 | single / [3] | Hana Mori: introduction, target null | Không có |
| Nhắc về người vắng mặt | narration | 1 | single / [3] | Hana Mori: reference, target null | Không có |
| Gọi trực tiếp | narration | 1 | single / [3] | Hana Mori: direct_address, target null | Đã chọn người nghe nhưng chưa kết luận ID của tên gọi |

Các loại vai trò tên trong tám case có ứng viên đều khớp mục tiêu kiểm tra đã đặt. Nhãn narration xuất hiện cả trong đối chứng gọi trực tiếp “Hana Mori, can you hear me?”: đây là dấu hiệu prompt đang quá rộng. Cần diễn đạt chức năng chính của câu; không coi mọi câu cung cấp thông tin là narration.

## Hàm ý cho thiết kế

- Có cơ sở để dùng `introduction` làm cổng ghép panel độc lập với content type. P02/T23 và P03/T06 đã được nhận là giới thiệu; thí nghiệm chưa chứng minh khả năng chọn người trong panel vì không thực hiện phần đó.
- P01/T01 không còn bị chọn single trong lượt này. Audience vẫn là nhãn người dùng chấp nhận cho case này, nhưng ID đi cùng sai. Không thể kết luận một probe đảm bảo sửa được cả chuỗi hội thoại.
- Nhãn rộng không giải quyết speaker của caption: model vẫn chọn ID MAGI ở các câu giới thiệu. Không dùng speaker này làm ID của người được giới thiệu.
- P04/T03 chưa đạt yêu cầu nối Ayase-San với ID 1. Không tự suy ra ID chỉ bằng việc loại speaker khỏi tập hai ứng viên; thiết kế phải giữ kết luận chưa xác định khi text thiếu căn cứ.
- Q9 đã chọn chuẩn hóa các cặp có giá trị cố định bằng code, ghi cảnh báo và không gọi thêm LLM cho chúng. Cặp cần lựa chọn danh tính mới vẫn phải do LLM kết luận hoặc để unresolved. Đây là quyết định thiết kế sau probe; các kết quả thô ở bảng chưa được chuẩn hóa.
- Schema nhỏ vẫn cần giới hạn coverage và ID riêng từng target. Không cần quay lại grammar nhân tổ hợp content type/addressee type/ID.
- Kiểm tra cặp phát hiện audience/ID và direct name/recipient mâu thuẫn, nhưng không phát hiện mọi nhãn sai nhất quán. Cần review ngữ nghĩa và thử trên tập rộng hơn khi triển khai.

Không suy ra mức tăng tốc từ hai lượt chạy này: schema, khối lượng output và retry khác nhau. Chưa đo so sánh đối chứng với pipeline cũ trên cùng cấu hình/nhiệm vụ.

## Artifacts

- Script: [probe_joint_dialogue.py](../../scripts/diagnostics/probe_joint_dialogue.py).
- Lượt 01: [report.json](../../outputs/diagnostics/joint-semantics-probe-20261004-01/report.json).
- Lượt 02: [report.json](../../outputs/diagnostics/joint-semantics-probe-20261004-02/report.json).
- Thiết kế đang khảo sát: [dialogue_semantics_redesign.md](dialogue_semantics_redesign.md).
- Probe chỉ có ngữ nghĩa, không chọn ID: [semantic_probe_20261004.md](semantic_probe_20261004.md).

Script hiện tại là phiên bản schema của lượt 02. Mỗi report giữ prompt, kết quả, lỗi và cấu hình think/images; report lượt 02 còn ghi coverage trả về của từng request. Lượt 01 giữ nguyên, không ghi đè.
