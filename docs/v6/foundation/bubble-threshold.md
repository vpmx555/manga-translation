# Bổ sung: ngưỡng bubble 0.45

> Ngưỡng 0.45 đã được đưa vào default/config v6. Xem [kết quả kiểm chứng](../delivery/implementation-status.md).

Ngưỡng **0.45** đã được triển khai trong default/config sau khi người dùng
xác nhận thiết kế. Text detection 0.45, dialogue -0.5 và association 0.25 giữ như đã chốt.

Lọc các điểm confidence đã lưu trong probe bubble 0.25 cho kết quả sau:

| Ngưỡng lọc cache | Candidate còn lại |
|---|---:|
| 0.25 | 53 |
| 0.32 | 48 |
| 0.45 | 46 |

Hai candidate bị loại khi chuyển 0.32 → 0.45: page 7 B3 (0.3622, heuristic cũ
`shout_hint`) và page 10 B5 (0.4021, biểu tượng bóng đèn, heuristic cũ `thought_hint`).
Không suy ra page 7 B3 là bubble thật từ heuristic đó. Đây chỉ là lọc cache, không
phải inference lại với threshold mới. M0/M11 phải đo recall/group topology bằng
run và receipt phù hợp 0.45. Text không có bubble sau lọc vẫn được giữ để review
và phân tích; không xóa lời thoại chỉ vì candidate bubble không đạt ngưỡng.

---

[← Tổng quan v6](../README.md)
