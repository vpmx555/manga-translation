# Runpod RTX 5090 Community: setup dùng lại, sửa code và chạy từ PC

**Cập nhật 05/10/2026:** người dùng đã chọn **RTX 4090 Secure** vì 5090 Community hết máy. Pod `8zvngth4tjdrz2` đã setup, chạy toàn bộ Bocchi chap 1, tải kết quả và bank về PC, rồi **Stop**. Khi dùng tiếp, Start đúng Pod này; không tạo thêm Pod chỉ để chạy lại. `deployment/runpod/pod.json` đã chuyển sang 4090 Secure. Xem [kết quả và chi phí](runpod_bocchi_chapter1_4090_result_vi.md). Các chỉ dẫn Community bên dưới là phương án ban đầu, không phải cloud của Pod hiện có.

Template riêng đã được tạo trong tài khoản: [Manga Translate - RTX 5090 reusable](https://console.runpod.io/hub/template/t3of29w78l), ID `t3of29w78l`.

Khi triển khai, chọn **1 RTX 5090, Community Cloud, On-Demand**. Template có container disk 30 GB, **Volume Disk 50 GB** tại `/workspace`, SSH qua TCP và image PyTorch/CUDA được ghim. Template chưa thuê GPU; phí chỉ bắt đầu khi tạo Pod. Lần kiểm tra triển khai ngày 05/10/2026 báo hết máy 5090 Community, nên chưa có Pod và chưa có xác nhận chạy MAGI/Ollama trên GPU thật. Giá catalog khi chuẩn bị là 0,69 USD/giờ, chưa tính lưu trữ; kiểm tra giá Deploy hiện tại.

## Giới hạn chi phí

Theo yêu cầu của chủ dự án: ưu tiên tiết kiệm tối đa, không tự chuyển cloud hoặc GPU đắt hơn. Người dùng đã cho phép 4090 Secure cho lần chạy này. **Stop Pod ngay khi công việc và tải kết quả hoàn tất hoặc khi gặp lỗi**; không để GPU chạy chờ. Chuẩn bị và kiểm tra code trên PC trước khi thuê máy.

**Stop trong Runpod Console mới ngừng tính phí GPU.** Lệnh `manga stop` chỉ dừng Ollama; job hoàn tất hoặc đóng SSH cũng không dừng Pod. Volume Disk vẫn tính phí khi Pod đã Stop. Sao lưu dữ liệu trước khi cân nhắc xóa Pod; xóa Pod sẽ mất Volume Disk.

Kiểm tra trên Pod 4090 Secure đã hoàn tất: SSH, CUDA, suy luận Ollama và 13 kiểm thử runtime trên Linux đều đạt; toàn bộ pipeline Bocchi chap 1 cũng đã chạy xong. Chưa kiểm tra một chu kỳ Stop/Start bằng cách thuê GPU lại, để tránh chi phí không cần thiết trong lần này.

## Dùng hằng ngày

Sau lần setup đầu, bật **Start** cho đúng Pod trong Console, rồi chạy từ PowerShell ở `D:\AIP491\manga-translate-v2`:

```powershell
.\runpod.ps1 run 'D:\Manga\chapter-001' --story 'After School We Do' --chapter chapter-001
```

Thay folder/story/chapter bằng dữ liệu thực tế. Lệnh tự đồng bộ code nếu có thay đổi, tải folder ảnh nếu chưa có, rồi tạo job chạy nền trên Pod. Có thể đóng PC/SSH sau khi nhận job ID. Không cài lại Python hoặc thư viện khi chỉ sửa mã nguồn. Nếu chỉ muốn cập nhật code mà chưa chạy:

```powershell
.\runpod.ps1 sync
```

Xem trạng thái và log:

```powershell
.\runpod.ps1 jobs
.\runpod.ps1 logs <JOB_ID>
.\runpod.ps1 status
```

Log có dòng `Run directory:` của pipeline. Nếu job bị ngắt hoặc có mục chưa hoàn tất, tiếp tục đúng run đó:

```powershell
.\runpod.ps1 resume --run-dir '/workspace/manga-runtime/data/outputs/after-school-we-do/chapter-001/<RUN_ID>'
```

Job mới không phải resume: chạy lại `run` sẽ tạo run mới. `jobs` hiển thị `completed`, `partial`, `failed` hoặc `interrupted`; dùng log để xem nguyên nhân. Trạng thái `partial` của dịch được retry bằng resume theo checkpoint.

## Lần đầu kết nối và setup

1. Deploy từ template trên, đúng **5090 Community**. Nếu hết máy, giữ template và chờ có chỗ; không tự đổi sang Secure hoặc GPU khác.
2. Trong mục Connect của Pod, lấy **SSH over exposed TCP**: IP và port bên ngoài. SSH key trên PC đã được đăng ký trong tài khoản Runpod.
3. Lưu kết nối bằng lệnh dưới đây, thay các giá trị placeholder:

```powershell
.\runpod.ps1 connect --host <POD_IP> --port <SSH_TCP_PORT> --key 'C:\Users\pxv23\.ssh\id_ed25519' --pod-id <POD_ID>
.\runpod.ps1 sync
.\runpod.ps1 setup
.\runpod.ps1 prefetch
.\runpod.ps1 doctor --inference
```

`setup` cài Python và thư viện Linux trong volume riêng; lần đầu mất thời gian tải package. `prefetch` tải model Ollama, MAGI và GLiNER vào cache. `doctor --inference` kiểm tra một phép tính CUDA thật và một request Ollama có sử dụng GPU. Không coi Pod `Running` là đã kiểm tra xong pipeline; nên chạy một trang thử trước khi chạy cả chương.

Có thể chạy lại `setup`, `start`, `prefetch` khi dùng lại Pod. Môi trường không đổi sẽ được dùng lại; Ollama đang chạy được nhận diện theo đúng executable, PID và thời điểm bắt đầu, không tạo thêm server thứ hai. Cổng Ollama riêng của dự án là localhost **11534**.

Nếu IP/port TCP thay đổi sau stop/start, chạy lại `connect` theo thông tin Connect mới. Template giữ SSH host key trong volume để tránh đổi fingerprint chỉ vì container khởi động lại. Kết nối và host keys của PC nằm trong `.runpod/`, được Git bỏ qua.

## Giữ thư viện và dữ liệu qua nhiều lần mở

| Thành phần | Nơi lưu |
| --- | --- |
| Code hiện tại | `/workspace/manga-translate-v2` |
| Python riêng | `/workspace/manga-runtime/python` |
| Môi trường thư viện theo bộ dependency | `/workspace/manga-runtime/envs` |
| Cache package/model Hugging Face | `/workspace/manga-runtime/cache` |
| Model Ollama | `/workspace/manga-runtime/models/ollama` |
| Folder ảnh | `/workspace/manga-runtime/data/input` |
| Character bank | `/workspace/manga-runtime/data/banks` |
| Output/checkpoint/review | `/workspace/manga-runtime/data/outputs` |
| Log từng job | `/workspace/manga-runtime/jobs/<JOB_ID>/output.log` |

Code không được cài thành package vào environment; Python chạy trực tiếp từ `src/`. Cập nhật code không chạm model, bank, output hoặc môi trường. Bundle được kiểm tra checksum và cú pháp Python trước khi đổi code. Bản code cũ giữ trong `code-backups/` để phục hồi khi cần. Cập nhật bị chặn khi pipeline đang chạy.

Khi thay thư viện, chỉnh `deployment/runpod/requirements.in`, rồi:

```powershell
.\runpod.ps1 sync
.\runpod.ps1 setup
.\runpod.ps1 doctor --inference
```

Bộ dependency khác tạo environment khác; environment cũ vẫn được giữ. Mỗi environment có dependency lock với hash. Dùng lại cache để giảm tải xuống. Không cài nguyên `requirements.txt` Windows hoặc nâng Transformers tùy ý vào environment này.

Runtime ghim Python 3.12.11, PyTorch 2.8.0/cu128, torchvision 0.23.0/cu128, Transformers 4.44.2, GLiNER 0.2.22 và Ollama 0.35.1. [PyTorch hỗ trợ Blackwell với CUDA 12.8](https://pytorch.org/blog/pytorch-2-7/); [Ollama hỗ trợ RTX 5090](https://docs.ollama.com/gpu). Bộ package Linux đã được resolve trước trên PC; đây chưa thay thế kiểm tra GPU thực tế.

Ollama chỉ nạp một model, một request song song. Một khóa dùng chung bảo vệ các job pipeline. Trước extraction, model Ollama được giải phóng khỏi VRAM; worker MAGI của pipeline kết thúc trước các bước LLM. GLiNER giữ cấu hình CPU hiện tại.

## Đọc và sao lưu kết quả

Download sang folder backup mới trên PC, không ghi đè output/bank hiện tại:

```powershell
.\runpod.ps1 fetch outputs 'D:\AIP491\manga-translate-v2\outputs\runpod-backup-01'
.\runpod.ps1 fetch banks 'D:\AIP491\manga-translate-v2\outputs\runpod-banks-backup-01'
```

Không resume trực tiếp run Windows đã sao chép sang Pod: manifest chứa đường dẫn ảnh và bank Windows. Tạo run mới trên Pod. Bank ban đầu trên Pod riêng với PC; chuyển bank hiện tại sang Linux cần kiểm tra đường dẫn crop và không ghi từ hai máy cùng lúc.

## Stop/start và đổi GPU

Khi chưa dùng, **Stop Pod trong Console**. Tắt Ollama không ngừng tính phí GPU. Volume Disk trong Community giữ `/workspace` khi stop/start, nhưng bị xóa khi **Terminate/Delete Pod**. Network Volume độc lập chỉ hỗ trợ Secure Cloud. Xem [lưu trữ Runpod](https://docs.runpod.io/pods/storage/types) và [giới hạn Network Volume](https://docs.runpod.io/pods/storage/create-network-volumes).

Template khởi động lại Ollama từ volume khi bật Pod. Python, thư viện và model không phải tải lại nếu image và volume vẫn được giữ. Nếu chuyển sang Pod khác, sao lưu/copy dữ liệu cần giữ trước khi xóa Pod cũ; environment portable vẫn cần cùng nền Linux/Python và driver GPU tương thích. Bộ Torch/cu128 này hỗ trợ cả 4090 và 5090, nhưng phải kiểm tra CUDA trên máy mới.

## Community, Secure và Serverless

Community và Secure là hai loại hạ tầng cho Pod. Community thường rẻ hơn; Secure có thêm lựa chọn lưu trữ độc lập và hạ tầng trung tâm dữ liệu. Cùng tên GPU không có nghĩa Secure tính toán nhanh hơn. Serverless là cách chạy job/API bằng worker tự mở rộng và có thể về 0 worker khi không dùng; dự án hiện tại cần đóng gói handler trước khi dùng mô hình đó. Với nhu cầu sửa code và chạy CLI thường xuyên, Community Pod là lựa chọn đã chọn. [Pod overview](https://docs.runpod.io/pods/overview), [Serverless overview](https://docs.runpod.io/serverless/overview).
