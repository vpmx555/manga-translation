# Kiểm tra kết quả bằng file gọn

Mở `<run-directory>/review.md`, dùng Markdown Preview nếu đọc trong VS Code (`Ctrl+Shift+V`). File tổng quan liên kết các bước và ảnh/crop để đối chiếu.

| File từng bước | Nội dung |
| --- | --- |
| `01_extract/review.md` | Run mới chỉ hiển thị essential: câu OCR, người nói MAGI, liên kết ảnh |
| `02_normalize/review.md` | Chỉ text thay đổi/gộp và text bị loại cùng lý do |
| `03_scan/review.md` | Tên tìm thấy, confidence NER, mapping đã biết và nguyên câu |
| `04_classify/review.md` | Mỗi câu essential: speaker/listener, ứng viên và nguồn, tên/vai trò, address type, bằng chứng |
| `05_link/review.md` | Tên, ID nếu đã gán và lý do gán/bỏ qua/chưa rõ |
| `06_export/review.md` | Mỗi câu essential: kết quả cuối, ứng viên nói/nghe và bằng chứng; liên kết text dành riêng cho dịch |

`P08/T04` là trang 8, text nguồn thứ 4; cùng mã xuất hiện ở các bước để đối chiếu. `ID 1` là stable character ID. Tên mới trong scan vẫn là ứng viên, chưa phải mapping đã xác nhận. Nguyên câu được giữ đầy đủ. Phần lỗi chỉ xuất hiện nếu có lỗi.

Nguồn ứng viên: `MAGI` là liên kết nguồn của câu; `context` là các câu lân cận; `name/alias` là tên đã nối ID; `memory:MAGI`, `memory:name/alias`, `memory:LLM` là bằng chứng từ tối đa 10 câu trước. Ứng viên nói/nghe là pool cung cấp cho mô hình, không phải danh sách xếp hạng hoặc điểm confidence.

Run mới `essential-v2` hiển thị sentinel nguyên dạng kèm nghĩa: `others` là speaker chưa gán ID (không phải một người cố định); `narrator` là người dẫn truyện chưa có danh tính cụ thể; `public_audience` là độc giả/người xem; `self` là chính người nói chưa có ID; `unknown` là người nghe chưa xác định. Thought của ID 3 hiển thị người nghe ID 3; narration của ID 3 giữ người nói ID 3 và người nghe public_audience. `group` kèm unknown nghĩa là còn thành viên chưa có ID, không phải lỗi target. Tổng quan tính cả trường hợp này vào số câu chưa rõ/chưa đủ người nghe.

Run cũ vẫn hiển thị theo dữ liệu cũ: `UNKNOWN`, `NARRATOR`, `NOT_APPLICABLE` không được tự chuyển thành nhãn mới khi tạo lại review. Xem [định nghĩa nhãn](essential_dialogue.md).

Các file được tạo sau mỗi bước và khi resume. Run cũ của pipeline v2 có thể tạo lại bằng:

```powershell
.\venv\Scripts\python.exe run.py review --run-dir "outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189"
```

Lệnh chỉ đọc artifact đã lưu và ghi review. Không gọi lại MAGI, NER, LLM/VLM, không thay config/checkpoint/bank. Review có thể tạo lại; sửa nó không chỉnh kết quả xử lý.

[Mở review của run After School, We Do](../../outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189/review.md). JSON và targets vẫn được giữ cho chương trình resume/chẩn đoán, nhưng không cần mở chúng để kiểm tra thông thường.
