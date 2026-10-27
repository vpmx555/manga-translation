# Iris Zero chapter 1 — dựng lại visual graph

Chạy PowerShell từ gốc repo. Cần bản sao batch đã đồng bộ đầy đủ và ảnh nguồn local đúng checksum:

```powershell
$batch = 'outputs/runpod-iris-zero-ch1-graph-v7-l40s-20261026-r4'
$run = "$batch/live/outputs/iris-zero/chapter-001/20261026T101458Z-e586087e"
venv/Scripts/python.exe -B scripts/review_story_graph.py --run-dir $run --output "$batch/local-graph-review" --source-folder 'D:/download/EN-data/Iris Zero/EN/Chapter 1'
Start-Process "$batch/local-graph-review/index.html"
```

Đây là dựng lại **visual từ kết quả model đã lưu**, không chạy lại model và không thuê pod. Xem thống kê/cảnh báo và checksum trong `local-graph-review/report.json`. Giữ cả thư mục `local-graph-review`, vì ảnh event nằm trong `evidence/images`.

Lượt này hoàn tất graph: 39 scene, 78 event, đủ 260 utterance, tối đa 8 câu/event và 4 nhóm tên. Hai lần dựng local cho 87 file có checksum giống nhau, không có cảnh báo cấu trúc. Số scene lớn là kết quả model cần con người đánh giá, không phải bằng chứng phân cảnh đã chính xác.

Trong HTML, bấm scene để xem giả thuyết nhân vật và tóm tắt; bấm event để xem từng câu nguồn, panel chính và vùng ảnh được khoanh/đánh số. Nút nhóm tên hiển thị alias và occurrence. Kết quả graph chỉ là ngữ cảnh sơ bộ, chưa phải kết luận của analyze/build_identity.

Tham số chung và hành vi kiểm tra checksum: [stage graph](../stages/05_graph.md#visual-local-có-thể-tái-lập). Code renderer: [story_graph.py](../../../src/manga_pipeline/review/story_graph.py); CLI: [review_story_graph.py](../../../scripts/review_story_graph.py).
