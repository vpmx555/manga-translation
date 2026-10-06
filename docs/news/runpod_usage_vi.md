# Chạy pipeline manga trên Runpod

Để chạy **RTX 5090 Community với setup dùng lại và đồng bộ code tự động**, dùng [bộ setup Runpod mới](runpod_reusable_setup_vi.md). Các lệnh bên dưới là phương án cài thủ công; Network Volume trong phương án đó dành cho Secure Cloud.

Hướng dẫn này dành cho phiên bản hiện tại của repository: MAGI/PyTorch trích xuất ảnh, GLiNER nhận diện tên trên CPU, Ollama chạy `gemma4:e4b-it-q4_K_M` để phân tích thoại, ghép tên và dịch tiếng Việt. Chạy cả repository và Ollama trong cùng một Pod; cấu hình `ollama.host: http://127.0.0.1:11434` được dùng trực tiếp.

Đã kiểm tra mã nguồn, CLI, template và catalog Runpod ngày 05/10/2026. Chưa tạo Pod hoặc chạy thử pipeline trên GPU Runpod.

## 1. Tạo máy GPU và nơi lưu dữ liệu

Cài plugin Runpod giúp điều khiển tài khoản; để chạy code cần thuê thêm một Pod. Tại thời điểm kiểm tra, tài khoản chưa có Pod.

Cấu hình khởi đầu đề xuất, chưa phải số đo VRAM của pipeline trên Runpod:

| Mục | Giá trị |
| --- | --- |
| Loại compute | GPU Pod, On-Demand |
| GPU | 1 NVIDIA GeForce RTX 4090, 24 GB VRAM |
| Template | [Runpod PyTorch 2.8.0](https://console.runpod.io/hub/template/runpod-torch-v280) |
| Docker image trong template đã kiểm tra | `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` |
| Container disk | 30 GB |
| Network Volume | 50 GB, mount `/workspace`, cùng data center với Pod |
| Kết nối | SSH qua TCP, port `22/tcp`; Jupyter `8888/http` nếu cần |

Catalog Secure Cloud vừa trả RTX 4090 giá **0,74 USD/giờ**, availability **LOW** với CUDA từ 12.8. Hai giờ GPU tương ứng 1,48 USD, chưa tính lưu trữ. Kiểm tra giá và máy còn trống ở màn hình Deploy trước khi thuê; đây không phải giữ chỗ hoặc giá cố định.

Trong [Runpod Console](https://console.runpod.io/), tạo Network Volume trước, sau đó triển khai Pod từ template trên với volume đó. Đăng ký SSH public key trong phần Settings trước khi tạo Pod; public key thường nằm ở `C:\Users\<user>\.ssh\id_ed25519.pub`. Nếu cần tạo key mới, chạy trên PowerShell:

```powershell
ssh-keygen -t ed25519
```

Giữ private key trên PC. Trong mục Connect của Pod, lấy địa chỉ IP, cổng TCP bên ngoài và đường dẫn key để thay các placeholder bên dưới. Dùng mục **SSH over exposed TCP** cho `scp`; SSH gateway cơ bản không hỗ trợ chuyển file bằng SCP/SFTP. Xem [hướng dẫn SSH chính thức](https://docs.runpod.io/pods/configuration/use-ssh).

Network Volume giữ dữ liệu độc lập với Pod. Volume disk thông thường ở `/workspace` bị xóa khi xóa Pod; container disk bị mất khi stop/restart. Xem [các loại lưu trữ](https://docs.runpod.io/pods/storage/types).

## 2. Chuyển bản code hiện tại và ảnh từ Windows

Repository hiện có nhiều thay đổi local; archive lấy đúng các file đang có trên PC. Không mang `venv` Windows lên Linux.

Trên PowerShell tại PC:

```powershell
Set-Location 'D:\AIP491\manga-translate-v2'
tar --exclude='__pycache__' -czf 'outputs/runpod-project.tar.gz' src configs banks run.py requirements.txt requirements-names.txt
scp -P <SSH_TCP_PORT> -i '<PRIVATE_KEY_PATH>' 'outputs/runpod-project.tar.gz' root@<POD_IP>:/workspace/
```

Kết nối Pod bằng lệnh SSH hiển thị trong Console. Trong terminal Linux của Pod:

```bash
mkdir -p /workspace/manga-translate-v2 /workspace/input
tar -xzf /workspace/runpod-project.tar.gz -C /workspace/manga-translate-v2
```

Quay lại PowerShell PC, upload folder ảnh của một chương. Thay `D:\Manga\chapter-001` bằng folder ảnh thực tế:

```powershell
scp -P <SSH_TCP_PORT> -i '<PRIVATE_KEY_PATH>' -r 'D:\Manga\chapter-001' root@<POD_IP>:/workspace/input/
```

Sau upload, folder ảnh tương ứng là `/workspace/input/chapter-001`.

Archive có `banks/` để giữ ID/tên nhân vật hiện tại. Chỉ chạy một máy ghi vào cùng bank tại một thời điểm. Không sao chép run cũ rồi `resume` trực tiếp trên Linux: manifest và artifact có đường dẫn tuyệt đối Windows cho ảnh, bank và một số dữ liệu. Hướng dẫn này tạo run mới; chuyển checkpoint cũ cần xử lý đường dẫn riêng.

## 3. Cài môi trường Python trên Pod

Các lệnh từ đây chạy trong terminal Linux của Pod, với user mặc định `root`:

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv zstd tmux
cd /workspace/manga-translate-v2
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
export HF_HOME=/workspace/hf-cache
export PIP_CACHE_DIR=/workspace/pip-cache
export MPLBACKEND=Agg
```

`--system-site-packages` dùng lại PyTorch/CUDA của template. `requirements.txt` hiện ghim `torch==2.14.0` và `torchvision==0.29.0`; cài nguyên file sẽ thay bộ torch của template. Tạo bản cài cho Pod, giữ các pin còn lại và bỏ hai dòng này. File hiện tại là UTF-16, đoạn sau xử lý cả UTF-16 và UTF-8:

```bash
python - <<'PY'
from pathlib import Path

raw = Path('requirements.txt').read_bytes()
encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
lines = raw.decode(encoding).splitlines()
kept = [line for line in lines
        if not line.strip().lower().startswith(('torch==', 'torchvision=='))]
Path('/workspace/requirements-runpod.txt').write_text('\n'.join(kept) + '\n', encoding='utf-8')
PY
python -m pip install -r /workspace/requirements-runpod.txt 'gliner==0.2.22'
```

Giữ `transformers==4.44.2` như môi trường MAGI hiện tại. Run mới dùng GLiNER nên chưa cần spaCy/model `en_core_web_sm`; các run legacy dùng spaCy mới cần `requirements-names.txt` và model đó.

Kiểm tra trước khi chạy model:

```bash
nvidia-smi
python -m pip check
python -c "import torch, torchvision, transformers, gliner; print('torch:', torch.__version__, 'CUDA:', torch.cuda.is_available()); assert torch.cuda.is_available(), 'PyTorch does not see CUDA'; print('GPU:', torch.cuda.get_device_name(0))"
python run.py --help
```

Nếu cài thư viện hoặc kiểm tra lỗi, xử lý lỗi đó trước khi chạy chương. Bộ pin được lấy từ môi trường Windows hiện tại; chưa xác nhận cài thành công trên Pod Linux.

## 4. Cài và chạy Ollama ngay trong Pod

Dùng Ollama **0.35.1**, cùng phiên bản đang cài trên PC. Asset Linux amd64 của release này đã được kiểm tra tồn tại. Cài bản binary theo [hướng dẫn Linux của Ollama](https://docs.ollama.com/linux), không phụ thuộc systemd:

```bash
python - <<'PY'
from urllib.request import urlretrieve
urlretrieve(
    'https://github.com/ollama/ollama/releases/download/v0.35.1/ollama-linux-amd64.tar.zst',
    '/workspace/ollama-linux-amd64-0.35.1.tar.zst',
)
PY
tar --zstd -xf /workspace/ollama-linux-amd64-0.35.1.tar.zst -C /usr
ollama --version
```

Khởi động một Ollama server trên localhost; model cache nằm trong volume:

```bash
export OLLAMA_MODELS=/workspace/ollama-models
export OLLAMA_HOST=127.0.0.1:11434
setsid ollama serve > /workspace/ollama.log 2>&1 < /dev/null &
```

Đợi server sẵn sàng rồi tải đúng model trong `configs/pipeline.json`:

```bash
python - <<'PY'
import time
from urllib.request import urlopen

for attempt in range(30):
    try:
        with urlopen('http://127.0.0.1:11434/api/version', timeout=2) as response:
            print(response.read().decode())
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit('Ollama chưa sẵn sàng; kiểm tra /workspace/ollama.log')
PY
ollama pull gemma4:e4b-it-q4_K_M
ollama list
```

[Model này](https://ollama.com/library/gemma4:e4b-it-q4_K_M) có tổng file tải khoảng 6,6 GB; đây là dung lượng tải, không phải số đo VRAM. Pipeline vẫn dùng `num_ctx: 8192` và GLiNER trên CPU theo cấu hình hiện tại. Ollama và pipeline cùng Pod nên dùng host localhost, không cần đưa port Ollama ra internet.

Thử một request với `think:false` để xác nhận server/model trả lời:

```bash
python - <<'PY'
import json
from urllib.request import Request, urlopen

payload = {
    'model': 'gemma4:e4b-it-q4_K_M',
    'messages': [{'role': 'user', 'content': 'Reply with the word OK.'}],
    'think': False,
    'stream': False,
    'options': {'num_ctx': 8192, 'num_predict': 32},
}
request = Request('http://127.0.0.1:11434/api/chat',
                  data=json.dumps(payload).encode(),
                  headers={'Content-Type': 'application/json'})
with urlopen(request, timeout=600) as response:
    result = json.load(response)
content = result.get('message', {}).get('content', '')
if not content.strip():
    raise SystemExit('Ollama trả nội dung rỗng')
print(content)
PY
ollama ps
ollama stop gemma4:e4b-it-q4_K_M
```

`ollama ps` cho biết model đang chạy trên GPU/CPU. Lệnh `ollama stop` giải phóng model trước bước MAGI; server vẫn chạy, pipeline sẽ tự nạp lại model khi đến bước phân tích. Thực hiện lệnh này trước mỗi chương mới nếu model còn nằm trong VRAM từ chương trước.

## 5. Chạy một chương và lấy kết quả

Dùng tmux để tiến trình tiếp tục khi ngắt kết nối SSH:

```bash
tmux new -s manga
cd /workspace/manga-translate-v2
source .venv/bin/activate
export HF_HOME=/workspace/hf-cache
export MPLBACKEND=Agg
python run.py run /workspace/input/chapter-001 \
  --story 'After School We Do' --chapter chapter-001 \
  --config configs/pipeline.json --device cuda
```

Thay story/chapter/folder bằng dữ liệu của bạn. Run mới chạy `extract → normalize → scan → analyze → export → translate`. MAGI và GLiNER tải model ở lần chạy đầu; MAGI kết thúc worker trước các bước LLM. Output mặc định nằm trong `/workspace/manga-translate-v2/outputs/<story-id>/<chapter-id>/<run-id>/`.

Tách khỏi tmux bằng `Ctrl+B`, rồi `D`; quay lại bằng `tmux attach -t manga`.

CLI in `Run directory:` ngay sau khi tạo. Nếu pipeline bị ngắt hoặc có mục dịch cần retry, dùng đúng đường dẫn đó trên Pod:

```bash
python run.py resume --run-dir /workspace/manga-translate-v2/outputs/after-school-we-do/chapter-001/<RUN_ID>
```

Mở `review.md` ở thư mục run để kiểm tra các bước. Review dịch nằm dưới `translations/vi/<revision>/review_natural.md` và `review_localized.md`. Log extraction ở `logs/magi.log`; log Ollama ở `/workspace/ollama.log`.

Để tải kết quả về PC, chạy trên PowerShell sau khi thay `<RUN_ID>`:

```powershell
scp -P <SSH_TCP_PORT> -i '<PRIVATE_KEY_PATH>' -r 'root@<POD_IP>:/workspace/manga-translate-v2/outputs/after-school-we-do/chapter-001/<RUN_ID>' 'D:\AIP491\manga-translate-v2\outputs\'
```

Sau khi hoàn tất, tải cả `banks/` về nếu cần dùng các tên/ID mới trên PC; giữ bản backup bank cũ trước khi thay bằng bản từ Pod.

## 6. Khi dùng lại Pod và khi chạy xong

Sau stop/start, dữ liệu trong Network Volume còn nhưng phần cài trong `/usr` của container có thể mất. Cài lại binary Ollama từ archive đã lưu, khởi động lại `ollama serve`, kích hoạt `.venv` và đặt lại các biến cache như trên. Các model ở `/workspace` không cần tải lại. Nếu đổi template/Python, tạo lại môi trường Python phù hợp rồi kiểm tra CUDA.

Khi không xử lý nữa, **Stop Pod** để ngừng tính phí GPU. Storage vẫn có thể tính phí khi Pod dừng. Nếu xóa Pod, Network Volume tồn tại riêng và vẫn tính phí đến khi xóa volume. Chỉ xóa storage sau khi đã sao lưu kết quả và bank cần giữ. Xem [lưu trữ Runpod](https://docs.runpod.io/pods/storage/types).

Serverless phù hợp khi sau này muốn cung cấp API nhận job dịch. Với CLI nhiều bước, checkpoint và bank hiện tại, Pod là cách triển khai khởi đầu trực tiếp hơn; chưa cần viết serverless handler.
