# Mục tiêu và các quyết định đã chốt

> Trạng thái: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

| Quyết định | Yêu cầu đã chốt |
|---|---|
| Q1 | Ưu tiên ít gợi ý sai; chấp nhận `unknown` nhiều hơn. |
| Bổ sung Q1 — tài nguyên | Gợi ý loại thoại chỉ dùng đặc trưng ảnh/mask, không nhận text/OCR/text box/dialogue score. Analyze kết hợp nội dung thoại và gợi ý để chốt loại thoại trong cùng lượt hiện có; không thêm model/call style riêng. |
| Q2 | Một nhóm bubble có các phần con và quan hệ nối giữa chúng. |
| Q3 | Lưu cả contour toàn bộ và contour thân riêng. |
| Q4 | Vị trí từng phần tách riêng; các phần của cùng nhóm được ghép thành lời thoại ở bước sau để analyze và dịch chung. |
| Q5 | Giữ cầu nối còn tồn tại. Việc xét nối thêm dùng khoảng cách contour; không làm mất quan hệ nhóm khi chia thân thành các phần con. |
| Q6 | Cố dựng contour kín cho mọi bubble, kể cả khi phải dựng thêm đoạn thiếu. |
| Q7 | Analyze quyết định một người nói chung cho nhóm. Không xác định được thì dùng trạng thái không liên kết như MAGI hiện tại; không lấy điểm association lớn nhất để ép một nhân vật. |
| Q8 | Dịch một chuỗi chung cho cả nhóm, sau đó phân bổ cân bằng vào các contour thân. Dùng margin và vùng OCR ban đầu để xác định vùng dàn chữ; tránh cầu nối. |
| Q9 | Vùng OCR là mốc bố trí; chữ được mở rộng trong thân bubble đến giới hạn margin. |
| Q10 | Cùng cỡ chữ trong một nhóm; chia lượng chữ theo sức chứa thực tế và thứ tự đọc. |
| Q11 | Khi không vừa: giảm chữ đến mức tối thiểu, rồi dịch lại ngắn hơn với số lần thử giới hạn. Vẫn không vừa thì báo cần xử lý. |
| Q12 | Bỏ bước phục hồi từ OCR. Giả định LLM hiểu các dạng ngắt như `cont-ribu-tion`; không thêm dehyphenation vào pipeline. |
| Q13 | SFX và nhãn hành động/trạng thái đã xác nhận được giữ làm ngữ cảnh và dịch ở mục riêng. |
| Q14 | Tiếp tục luồng xác nhận tên hiện tại; không thêm gate/quarantine độc lập vì role còn chưa rõ. |
| Q15 | Một key tên có thể trả nhiều identity; chọn bằng ngữ cảnh/hình ảnh, không rõ thì giữ unknown. Không hợp nhất chỉ vì trùng họ/tên. |
| Q16 và bổ sung appearance | Giữ nhánh một call rõ + ảnh phù hợp. Nhiều calls confirmed cùng person/recipient ưu tiên hơn visual similarity; chibi/hóa hình/góc vẽ/già đi không tự tách person. |
| Q17 | Dịch SFX/trạng thái chỉ xuất JSON/Markdown riêng, không xóa hoặc dàn chữ effects trên ảnh. |
| Mở rộng identity | Tên/alias chữ thường, họ/tên đệm/honorific ở person store riêng. Cluster và person là hai tầng; chỉ thêm cluster–person links, không sửa bank/IDs/embeddings MAGI. |

Toàn bộ thử nghiệm và inference của thiết kế này chạy local. Runpod không thuộc
luồng thực hiện. Pilot dùng After School chapter 1; Habatake không thuộc pilot.

### Ba đơn vị dữ liệu cần phân biệt

1. **Candidate detection:** một dự đoán của model từ ảnh toàn trang hoặc crop.
2. **Bubble group / part:** nhóm hình học và các thân con; giữ tọa độ riêng, đuôi,
   cầu nối, các panel giao nhau và bằng chứng contour.
3. **Utterance:** một đơn vị lời thoại được normalize/analyze/dịch chung; tham chiếu
   đến nhóm và toàn bộ các text box gốc. Thay đổi đơn vị lời thoại không sửa dữ liệu
   MAGI gốc.

Một part có thể chứa nhiều text box hoặc nhiều đoạn văn. Số text box không quyết
định số part. Việc chia thân không làm đứt nhóm vật lý.

---

[← Tổng quan v6](../README.md)
