# Rủi ro, phương án không chọn và phạm vi chưa chứng minh

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

- Khoảng cách contour nhỏ không chứng minh hai bubble cùng nhóm. Giữ bridge gốc
  và xét ảnh/parent candidate là phần cần thiết của M3.
- Dựng contour kín luôn có thể tạo biên sai ở vùng thiếu nhiều. Lựa chọn Q6=C
  được thực hiện bằng output kín cùng provenance; classification và inpaint phải
  biết phần nào được dựng thêm.
- Watershed/skeleton chỉ dựa vào nhiều text anchor có thể chia quá mức một bubble
  bình thường; M4 cần bằng chứng cổ nối và giữ group ngay khi có part con.
- Không chọn gán speaker theo association cao nhất; không coi các source link
  khác nhau là các nhân vật khác nhau trong cùng lời thoại.
- Không chọn dịch độc lập theo part, chia chữ bằng nhau, hoặc dàn xuyên cầu nối.
- Không thay contour hiện tại bằng contour AIP419 toàn bộ. Các ý tưởng fusion có
  thể tham khảo AIP, nhưng cần kiểm chứng riêng thay vì lấy số bubble ít hơn làm
  bằng chứng tốt hơn.
- Chưa thêm model segmentation/style mới hoặc huấn luyện model. Nếu M11 cho thấy
  model không phát hiện đủ instance hoặc phần thân không thể cứu bằng hậu xử lý,
  đó là nhánh nghiên cứu tiếp theo, với input/output và benchmark riêng.
- Model local, font và OCR environment phải qua preflight. Thiết kế này chưa có
  số đo latency, chất lượng dịch, boundary accuracy hay precision mới.

### Appearance và caller support

Chibi/hóa hình/tuổi/góc vẽ có thể làm embedding xa, không tự là cannot-link.
Rủi ro là gọi nhầm recipient, trùng tên/giả danh: cần context confirmed, không
chỉ word count. I2 không thêm call so ảnh mặc định. Revert person links không
ảnh hưởng bank MAGI vì lưu ở tầng riêng.
---

[← Tổng quan v6](../README.md)
