# Cây thư mục mẫu sau refactor

Bản mẫu trước khi triển khai. Chỉ thể hiện thư mục và vai trò, không liệt kê các file bên trong.

```text
manga-translate-v2/
├── src/
│   └── manga_pipeline/
│       ├── cli/
│       ├── extraction/
│       ├── normalization/
│       ├── bank/
│       ├── names/
│       ├── providers/
│       └── storage/
│
├── configs/
│
├── banks/
│   └── <story-id>/
│       ├── embeddings/
│       └── crops/
│
├── outputs/
│   └── <story-id>/
│       └── <chapter-id>/
│           └── <run-id>/
│               ├── 01_extract/
│               ├── 02_normalize/
│               ├── 03_scan/
│               ├── 04_classify/
│               ├── 05_link/
│               ├── 06_export/
│               ├── cache/
│               ├── logs/
│               └── snapshots/
│
├── data/
│   ├── samples/
│   └── annotations/
│
├── tests/
│   ├── cli/
│   ├── extraction/
│   ├── normalization/
│   ├── bank/
│   ├── names/
│   ├── providers/
│   └── storage/
│
├── scripts/
│
└── docs/
    ├── news/
    └── archive/
```

## Mã nguồn

| Thư mục | Vai trò |
| --- | --- |
| `src/manga_pipeline/` | Package chính; điều phối toàn pipeline và dùng chung kiểu dữ liệu/contract cần thiết. |
| `cli/` | Nhận lệnh chạy từ folder ảnh, chạy từng bước, chọn config và resume. Logic xử lý được gọi từ các phần bên dưới. |
| `extraction/` | Chạy MAGI, đọc và sắp ảnh, thu OCR/panel/nhân vật/tail, tạo raw output và transcript có ID. |
| `normalization/` | Chuẩn hóa text, giữ liên kết nguồn, phân loại nội dung bằng quy tắc và ghi audit phần bị loại. |
| `bank/` | Quản lý character ID, embedding, crop, pending trong cửa sổ ba trang liên tiếp, tên và bảo vệ chỉnh sửa thủ công. Đây là code quản lý bank. |
| `names/` | Luồng tìm tên, tra tên đã biết, tạo cửa sổ năm câu, phân loại mention/narration và ghép tên–ID theo điều kiện panel. Ban đầu giữ chung một thư mục này, chưa cần chia thêm nhiều tầng. |
| `providers/` | Tích hợp spaCy/Ollama: nạp model, tạo request, kiểm tra response, giới hạn tài nguyên và xử lý lỗi gọi model. Quyết định khi nào được gọi model thuộc `names/`. |
| `storage/` | Đọc/ghi artifact, cập nhật JSON atomically, manifest, fingerprint, cache và trạng thái resume. |

Không có package scene segmentation hoặc sửa thứ tự đọc bằng reasoning trong cấu trúc mới.

## Cấu hình và bank dùng chung

`configs/` chứa cấu hình MAGI, bộ tìm tên, Ollama, đường dẫn và giới hạn chạy. Ban đầu giữ cấu hình trong một thư mục để dễ tìm.

`banks/<story-id>/` chứa dữ liệu bank dùng chung cho các chapter của một truyện. Metadata tại đây quản lý quan hệ ID–tên, nguồn tên và trạng thái pending; `embeddings/` và `crops/` chứa dữ liệu nhận diện. Bước gắn tên chỉ cập nhật metadata, không đổi ID, embedding hoặc crop của nhân vật đã hoàn chỉnh.

`src/manga_pipeline/bank/` là mã nguồn; `banks/` là dữ liệu được mã nguồn đó quản lý.

## Kết quả theo từng lượt chạy

Mỗi `<run-id>` chứa kết quả của một lượt chạy thuộc một chapter. Từng lệnh chạy bước riêng ghi vào thư mục tương ứng, để có thể kiểm tra hoặc chạy tiếp từ kết quả đã lưu.

| Thư mục trong run | Nội dung |
| --- | --- |
| `01_extract/` | Raw MAGI, transcript có ID và visualization của extraction. |
| `02_normalize/` | Utterance đã chuẩn hóa và audit nội dung. |
| `03_scan/` | Các tên phát hiện được và kết quả tra mapping đã có trong bank. |
| `04_classify/` | Phân loại câu mục tiêu/mention của các tên chưa có mapping, cùng trạng thái chưa rõ hoặc lỗi. |
| `05_link/` | Mapping tên–ID tự gán hoặc do VLM chọn, bằng chứng và các mục bị bỏ qua/thất bại. |
| `06_export/` | Dialogue cuối giữ thứ tự nguồn và báo cáo hoàn thành/chưa hoàn thành. |
| `cache/` | Response model hợp lệ để tái sử dụng khi input, config và schema phù hợp. |
| `logs/` | Log thực thi, thời gian và lỗi. |
| `snapshots/` | Bản chụp config/bank cần thiết để đối chiếu trạng thái tại thời điểm xử lý. |

Manifest và thông tin điều phối được lưu tại gốc run; không cần tạo thêm thư mục chỉ để chứa chúng.

Lệnh resume tiếp tục từ run directory của pipeline mới: đọc lại artifact của các bước đã hoàn thành và bỏ qua xử lý/model của những bước đó. Bước đang dở tiếp tục từ từng mục đã commit; kết quả hợp lệ chưa xác định cũng được giữ, còn mục lỗi có thể retry riêng. Cập nhật bank có journal để tránh ghi lặp khi bị ngắt. Không hỗ trợ resume run của pipeline cũ.

Luồng `names/` tuân thủ các điều kiện đã bàn: tên đã có mapping bỏ qua LLM/VLM; pending không được nhận tên; panel không có ID ổn định thì bỏ qua; một ID và một tên mới đủ điều kiện thì tự gán; một ID nhưng nhiều tên mới thì giữ chưa rõ; từ hai ID trở lên mới xét VLM cho narration.

## Dữ liệu mẫu, kiểm chứng và tài liệu

| Thư mục | Vai trò |
| --- | --- |
| `data/samples/` | Ảnh/JSON mẫu dùng để kiểm tra pipeline; lệnh chạy thực tế vẫn nhận folder ảnh bất kỳ, không buộc copy chapter vào đây. |
| `data/annotations/` | Nhãn đối chiếu đã kiểm tra cho sample/pilot. |
| `tests/` | Test được nhóm theo phần code tương ứng, gồm contract giữa các bước và các quy tắc mới về pending/tên. |
| `scripts/` | Tiện ích kiểm tra dataset, migration, smoke test hoặc thí nghiệm riêng. Lệnh chạy pipeline chính đi qua CLI. |
| `docs/news/` | Hướng dẫn, sơ đồ và báo cáo mới sau khi code được refactor. |
| `docs/archive/` | Tài liệu lịch sử của scene/reasoning và cấu trúc cũ để đối chiếu. |

Đây là sơ đồ tổ chức đề xuất. Việc migration bank và sắp xếp tài liệu/output cũ sẽ giữ dữ liệu hiện có; sơ đồ này chưa thực hiện di chuyển thư mục.
