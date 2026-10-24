# Bubble geometry trên GPU

Yêu cầu 12/10/2026: chuyển phần tính toán bubble geometry từ CPU sang GPU.

## Thay đổi

Pipeline v6 lấy backend từ `config.device`. Với `cuda`, stage dùng
`cupy-cuda12x==14.2.0` và `cupyx.scipy.ndimage` cho xử lý mask, morphology,
connected components, fill holes và exact Euclidean distance transform.
Các threshold, cấu trúc disk, connectivity, quy tắc bridge/tail và float64 EDT
được giữ. Multi-iteration dilation dùng `brute_force=True` vì CuPy chưa hỗ trợ
nhánh incremental tương ứng.

Boundary mask và tọa độ cạnh được tính trên GPU; chỉ tọa độ cần để trace polygon
được đưa về CPU. Python/Shapely trace và simplify polygon, PIL/PNG, checksum,
visual style classifier và mapping ROI nhỏ vẫn dùng CPU. Vì thế không nên suy
ra stage thất bại chỉ từ một mẫu GPU utilization 0% khi nó đang ghi artifact.

CUDA được yêu cầu mà CuPy/runtime thiếu thì báo lỗi rõ ràng, không tự chạy CPU.
CPU backend vẫn dùng cho cấu hình CPU. Mỗi page có allocator riêng được giải
phóng sau xử lý để không giữ buffer lớn trước analyze/inpaint. Kernel cache nằm
ở execution root local qua `CUPY_CACHE_DIR`.

Target receipt và kết quả page ghi `compute_backend`/`geometry_compute_backend`.
Request policy đổi thành `visual-groups-v6.2`; cache key gồm backend và version.
Không gán receipt GPU cho checkpoint CPU cũ. Dependency signature Linux thay
đổi; phải chuẩn bị lock mới trước lần deploy sau, không dùng lock MAGI cũ của
batch Bocchi 12/10 để cài môi trường mới.

## Kiểm chứng và giới hạn hiện tại

Đã chuẩn bị fixture parity CPU/CUDA cho mask có hole, bridge, candidate lồng
nhau và lobe nhỏ; so sánh topology, mask hashes và native JSON payload. Có guard
test cho trường hợp thiếu CuPy. Doctor remote đã có preflight kernel 5×5, ghi
receipt CUDA thực trước inference trong lần chạy được cho phép tiếp theo.

Chưa chạy tests, chưa cài dependency, chưa chạy GPU hoặc benchmark corpus trong
lượt sửa này. Chỉ kiểm tra cú pháp/static integration và whitespace. Chưa thể
kết luận tốc độ cải thiện bao nhiêu hoặc bảo đảm parity trên corpus thật.

Tài liệu dùng để kiểm tra API:

- [CuPy ndimage](https://docs.cupy.dev/en/stable/reference/scipy_ndimage.html).
- [Exact GPU EDT và float64](https://docs.cupy.dev/en/stable/reference/generated/cupyx.scipy.ndimage.distance_transform_edt.html).
- [CuPy 14.2 morphology source](https://github.com/cupy/cupy/blob/v14.2.0/cupyx/scipy/ndimage/_morphology.py).

Không provision, kết nối, upload hoặc thực thi trên Runpod trong lượt này.
