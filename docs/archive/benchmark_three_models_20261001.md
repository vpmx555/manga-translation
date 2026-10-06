> Historical: describes the pre-refactor pipeline. Current usage is in ../news/usage.md.

# Benchmark thực tế ba embedding trên 36 câu thoại

Đã chạy thành công cả **MPNet**, **EmbeddingGemma 300M** và **Qwen3 Embedding 0.6B** bằng weights thật, trên CPU. Không dùng vector giả lập.

**Kết quả chính: cấu hình phân cụm baseline hiện tại chưa đủ tốt để dùng làm scene chắc chắn cho bước dịch.** Không có model production hoặc ngưỡng production nào được chọn từ phép thử này.

## Dữ liệu và cách chạy

- Nguồn: `D:\download\utterances_selected.json`, 36 utterances theo thứ tự đọc.
- Nhãn đã được người dùng duyệt: scene mới trước câu **10** và **15**. Trước câu **34** là chưa rõ, bị bỏ khỏi chấm điểm.
- Chấm 34 gaps đã xác định: 2 ranh giới thật, 32 gaps liên tục. Gaps trong WindowDiff đi qua vị trí chưa rõ cũng bị bỏ; k=6, 27 cửa sổ hợp lệ.
- Embedding từng utterance riêng, không gộp mất text box; prompt theo backend như cấu hình pipeline.
- Cùng thuật toán adjacent average-linkage, cohesion threshold 0.8; thử các distance threshold cố định 0.25/0.35/0.45/0.55. Không chỉnh chúng theo kết quả quan sát.
- CPU 4 threads, batch size 8. Python 3.12.0, torch 2.14.0+cpu, transformers 4.57.6, sentence-transformers 5.7.0, numpy 2.5.3.
- Một document duy nhất; đây là **so sánh mô tả trên mẫu đã duyệt**, không phải đánh giá trên tập kiểm tra độc lập. Các scores phản ánh cả embedding và cấu hình clustering, không riêng model.

## Cùng distance threshold 0.45

F1 cao hơn tốt hơn; WindowDiff thấp hơn tốt hơn. Thời gian encode chỉ tính tạo embedding cho 36 câu, không gồm tải/khởi tạo model; đo một lượt, không phải trung bình nhiều lượt.

| Model | Scene đề xuất | Boundary F1 | WindowDiff | Encode 36 câu |
|---|---:|---:|---:|---:|
| MPNet | 35 | 0.114 | 1.000 | 1.05 s |
| EmbeddingGemma 300M | 1 | 0.000 | 0.407 | 1.62 s |
| Qwen3 Embedding 0.6B | 1 | 0.000 | 0.407 | 7.63 s |

MPNet phát hiện hai ranh giới thật nhưng đồng thời tạo **31 ranh giới sai** trên gaps đã xác định: chia quá nhỏ.
EmbeddingGemma và Qwen3 ở 0.45 gộp toàn bộ thành một scene, bỏ sót cả hai ranh giới thật. WindowDiff thấp hơn MPNet không có nghĩa cách gộp này đúng: boundary recall bằng 0.

## Toàn bộ ngưỡng đã thử

| Model | Distance threshold | Scene đề xuất | Boundary F1 | WindowDiff |
|---|---:|---:|---:|---:|
| MPNet | 0.25 | 36 | 0.111 | 1.000 |
| MPNet | 0.35 | 35 | 0.114 | 1.000 |
| MPNet | 0.45 | 35 | 0.114 | 1.000 |
| MPNet | 0.55 | 32 | 0.125 | 1.000 |
| EmbeddingGemma | 0.25 | 3 | 0.000 | 0.444 |
| EmbeddingGemma | 0.35 | 1 | 0.000 | 0.407 |
| EmbeddingGemma | 0.45 | 1 | 0.000 | 0.407 |
| EmbeddingGemma | 0.55 | 1 | 0.000 | 0.407 |
| Qwen3 | 0.25 | 32 | 0.125 | 1.000 |
| Qwen3 | 0.35 | 22 | 0.182 | 1.000 |
| Qwen3 | 0.45 | 1 | 0.000 | 0.407 |
| Qwen3 | 0.55 | 1 | 0.000 | 0.407 |

EmbeddingGemma ở 0.25 tạo đúng số lượng 3 cụm nhưng cắt **trước câu 7 và 9**, thay vì câu 10 và 15. Số cụm không đủ để đánh giá đúng scene.
Qwen3 ở 0.35 có F1 cao nhất trong lưới này (0.182) nhưng vẫn tạo **18 ranh giới sai**, nên chưa phải kết quả tốt hoặc căn cứ chọn production.

## Chẩn đoán bổ sung: vector có phân biệt ranh giới không?

Sau benchmark, đo thêm khả năng xếp hạng khoảng cách ở ranh giới thật cao hơn gaps liên tục. Đây là phân tích hậu nghiệm, không thay thế F1 clustering hoặc kết quả held-out. Chỉ có 2 ranh giới dương nên không được suy rộng.

AUROC 0.5 gần mức xếp hạng ngẫu nhiên; càng cao càng tốt. Cửa sổ 1 so sánh hai câu cạnh nhau; cửa sổ 2 dùng mean vector của tối đa hai câu mỗi phía, không chồng lấn.

| Model | AUROC cửa sổ 1 | AUROC cửa sổ 2 | Median cosine distance giữa các câu liên tục |
|---|---:|---:|---:|
| MPNet | 0.813 | 0.859 | 0.790 |
| EmbeddingGemma | 0.641 | 0.641 | 0.143 |
| Qwen3 | 0.578 | 0.563 | 0.369 |

MPNet có tín hiệu ranh giới hứa hẹn hơn trên **mẫu này**, nhưng clustering/ngưỡng hiện tại tận dụng chưa tốt. Distance scale giữa ba model rất khác nhau; cùng một giá trị distance/cohesion threshold không có cùng ý nghĩa chất lượng trên các model.
Các vị trí có distance lớn cũng có thể chỉ là đổi chủ đề hoặc một phản ứng ngắn trong cùng scene. Ví dụ, top gaps của các model thường rơi vào giữa một cuộc trò chuyện liên tục, không phải scene mới.

## Kết luận sử dụng

1. Giữ `scene_status=proposed` và context lân cận trong bước dịch; chưa dùng các cụm hiện tại làm ranh giới cứng.
2. Không chọn EmbeddingGemma chỉ vì hỗ trợ prompt clustering, không chọn Qwen3 chỉ vì model lớn hơn, và không chọn MPNet production chỉ từ AUROC của một chương.
3. Bước cải thiện cần đánh giá là biểu diễn nhóm hội thoại/ngữ cảnh hai phía và hiệu chỉnh distance/cohesion threshold **riêng cho từng backend** trên development, rồi kiểm tra chương độc lập. Chưa triển khai/tuyên bố chất lượng cho phương án cải thiện đó trong benchmark này.
4. Cần thêm document có nhãn đã duyệt để đánh giá tổng quát và chọn model/ngưỡng production theo quy trình đã thống nhất.

## Artifacts và tái chạy

- Report đầy đủ: `runs/benchmark-three-embeddings-20261001.json` (mọi ngưỡng, scene ranges, metrics, timing và revision).
- Weights: `data/scene_models/`; vectors: `data/scene_embeddings/`.
- Script: `src/benchmark_sample.py`.
- Môi trường riêng: `venv-scenes/`; venv MAGI không bị nâng phiên bản.

```powershell
.\venv-scenes\Scripts\python.exe src/benchmark_sample.py "D:\download\utterances_selected.json" --labels data/scene_review/utterances_selected.confirmed.json --output runs/benchmark-three-embeddings-20261001.json --resume
```

`--resume` bỏ qua backend đã thành công. Nếu muốn đo inference một lượt mới, dùng output mới và cache vector mới; không coi thời gian đọc cache là tốc độ inference.
