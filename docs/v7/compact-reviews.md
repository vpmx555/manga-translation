# Review gọn cho graph, analyze, reanalyze

Pipeline tự sinh `review.html` và `review.md` cho ba stage bằng
`src/manga_pipeline/v6/story_review.py` và template `story_review.html` cùng thư mục.

HTML mở offline: chọn scene ở cột trái, chọn event ở cột giữa, đọc thoại và
kết luận ở cột phải. Mỗi lần chỉ dựng chi tiết một event. Có tìm kiếm,
lọc event cần chú ý, nút trước/tiếp. Ảnh, giả thuyết và metadata phụ chỉ mở khi bấm.
Reanalyze có bộ lọc sửa đổi và before/after chỉ chứa trường thay đổi.
Artifact JSON giữ toàn bộ metadata, memory và kết quả gốc; Markdown là mục lục ngắn.

Người nói/người nghe hiển thị số MAGI từ `story_graph.characters[].magi_ids`,
kèm tên nếu có. Nhân vật chưa có ánh xạ hiển thị “Chưa gán MAGI”; viewer không
tự đoán số MAGI hoặc hiển thị mã `person:…` thay cho nhãn người nói/người nghe.

Để tái tạo review từ artifact đã lưu, tại thư mục gốc repository:

```powershell
python scripts/rebuild_compact_reviews.py --run-dir "<thư mục run chứa manifest.json và 05_graph, 06_analyze, 07_reanalyze>"
```

Lệnh chỉ ghi lại `review.html`, `review.md` của ba stage và
`compact-review-report.json`; không khởi tạo provider, không recover run,
không chạy model, không truy cập Runpod. Report lưu hash các artifact đầu vào,
manifest và HTML đầu ra. Không thay đổi contract của checkpoint lịch sử.
Với cùng artifact và cùng template, HTML được sinh xác định.

Kết quả kiểm tra trên Iris Zero r9: đủ 39 scene/78 event ở cả ba stage,
4 câu có sửa đổi trong reanalyze; các đường dẫn ảnh hiện có đều tồn tại.
Đã kiểm tra cú pháp Python/JavaScript và hash artifact/manifest không đổi.
