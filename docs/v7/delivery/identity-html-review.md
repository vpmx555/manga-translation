# HTML review cho build_identity và relationship

HTML được dựng bằng code từ JSON đã lưu, không gọi model. Mỗi run có thư mục
`local-reviews/<story>/<chapter>/<run>/identity-relationship/` trong output batch.

| File | Nội dung |
|---|---|
| `build_identity.html` | Tên → MAGI, panel/crop bằng chứng, lý do, profile, tên chưa phân giải |
| `relationship.html` | Đồ thị có hướng, từng chiều tự xưng/gọi, lý do, câu thoại, ảnh và trạng thái |
| `review.md` | Review riêng hai stage, đọc được bằng editor hoặc agent |
| `report.json` | Dữ liệu máy đọc, provenance và hash snapshot |

Mở HTML trực tiếp bằng trình duyệt; không cần CDN hoặc server. Giữ nguyên vị trí
thư mục review và `live` để các đường dẫn tương đối đến ảnh tiếp tục hoạt động.
Bấm ảnh để phóng to, dùng ô tìm kiếm để lọc, bấm cạnh/nút trong bảng để xem lý do.
Mỗi chiều xưng hô là một cạnh riêng; quan hệ xã hội được trình bày riêng.

`deployment/runpod/local_review.py` tự tạo các file này khi replica có artifact
mới. Có thể tạo lại độc lập bằng Python của dự án:

```powershell
venv/Scripts/python.exe deployment/runpod/identity_review.py `
  --run-dir "<batch>/live/outputs/<story>/<chapter>/<run>" `
  --output "<batch>/local-reviews/<story>/<chapter>/<run>/identity-relationship" `
  --source-folder "<folder ảnh chapter>" `
  --bank-root "<batch>/live/banks"
```

Renderer chỉ đọc artifact, không sửa manifest, registry hoặc kết quả stage.
Name-binding preview được đánh dấu rõ khi build_identity chưa commit. Graph
rỗng hoặc request thất bại được hiển thị đúng trạng thái; renderer không tự tạo
identity, bằng chứng hoặc quan hệ để lấp chỗ trống.

Ví dụ ảnh upstream thật: [examples](../examples/README.md). Bộ ví dụ có manifest
và hash nguồn, không được dùng thay kết quả kiểm chứng inference của batch mới.
