# Căn cứ từ mã và probe hiện tại

> Tài liệu này ghi **baseline trước khi triển khai v6**. Code hiện tại và phạm vi
> kiểm chứng nằm trong [trạng thái triển khai](../delivery/implementation-status.md).
>
> Trạng thái baseline: **thiết kế v6**, chưa triển khai các thay đổi được đề xuất. Chạy local; pilot After School chapter 1.

- `visual/koharu.py` detect toàn trang và theo crop panel, chuyển mask về tọa độ
  toàn trang, rồi suppress duplicate theo bbox IoU 0.65. Mỗi bubble được gán một
  `panel_index`. Adapter hiện nhận mask nhị phân với cutoff 0.5; thiết kế này không
  giả định đã có bản đồ xác suất segmentation.
- `visual/geometry.py` dựng biên theo cạnh ô pixel, giữ polygon có diện tích lớn
  nhất và simplify. Cách lấy biên hiện tại được giữ làm nền; bổ sung xử lý mọi
  component và các đoạn cần sửa.
- `visual/bubbles.py` phân loại từ contour và ảnh. Đặc trưng hình học toàn bubble
  có thể bị đuôi/cầu nối làm sai. `confidence` hiện là điểm heuristic, chưa được
  hiệu chỉnh thành xác suất đúng.
- Khi đổi cách dựng contour trên cùng 53 mask của probe, 8 nhãn gợi ý thay đổi.
  Đây là phép đo độ nhạy của classifier, chưa phải phép đo độ chính xác nhãn.
- Biểu tượng bóng đèn ở page 10 có điểm detect khoảng 0.402 và bị gợi ý thought;
  ngưỡng 0.32 vẫn giữ candidate này. Cần kiểm tra tính phù hợp với text và hình ảnh,
  ngoài điểm detect.
- Normalize hiện yêu cầu cùng panel/người nói khi gộp; khi gộp nhiều bubble ID,
  metadata bubble bị bỏ. Hai quy tắc này cần được thay bằng hợp đồng nhóm mới.
- Extract hiện dùng `speaker_id=null`, `speaker_display_label="Other"` khi không
  liên kết nhân vật. Analyze/export vẫn phải dùng adapter trạng thái unknown hiện
  có, không tạo ID nhân vật mới tên `Other`.
- Repo có inpaint, nhưng chưa có bộ typeset theo contour trong `src/manga_pipeline`.
  Các mốc dàn chữ bên dưới là tính năng mới. `inpaint.enabled` hiện là `false`.

Probe đối chiếu AIP419 có 11 trang: hiện tại 53 bubble, AIP419 37 bubble, cùng
ngưỡng bubble 0.25. Đây là baseline lịch sử; không so trực tiếp số lượng đó với
thiết kế mới dùng ngưỡng 0.45 để kết luận cải thiện.

Nguồn nội bộ:

- [Điều tra AIP419](../../news/bubble_geometry_aip419_comparison_vi.md)
- Ảnh so sánh từng trang là artifact local tại
  `outputs/diagnostics/bubble-geometry-aip419-comparison-20261011/side-by-side/`.
  Artifact này không là liên kết tài liệu bắt buộc sau git clone.
- [Review pipeline v1–v5](../../news/pipeline_v1_to_v5_review_vi.md)

---

[← Tổng quan v6](../README.md)
