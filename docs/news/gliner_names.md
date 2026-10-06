# GLiNER: nhận diện tên viết Latinh

Mặc định mới là gliner-community/gliner_small-v2.5, revision f227d3cd637bd4e6757ae143935316d062393341, nhãn person name, threshold 0.85, device cpu. Confidence gốc của GLiNER được lưu trong candidates của result.json/targets và hiển thị 4 chữ số thập phân ở review scan. Tên tìm bằng đối chiếu bank hoặc kết quả cũ không có score hiển thị dấu —; không tự gán confidence 1.0. Đây là score NER, không phải confidence speaker/listener hay xác suất tên được nối đúng ID. Thư viện gliner==0.2.22 giữ tương thích với Transformers 4.44.2 đang dùng cho MAGI. Không có reasoning hay gọi Ollama trong scan.

Model và tokenizer lưu trong models/ner, không đưa vào Git. Chỉ tải một bộ trọng số, không tải thêm các bản fp16/bf16. GLiNER được load một lần trong scan, rồi giải phóng trước bước classify. Có thể đổi ner.device thành auto/cuda; yêu cầu cuda khi không khả dụng sẽ báo lỗi. Không thay cấu hình của run đã tạo.

Tên được lấy bằng offset trên text nguồn. Không tự sửa MAD01 thành Madoi hay đổi Ayase thành Arase. Có xử lý nhẹ loại đại từ tiếng Anh, honorific đứng riêng, span không có chữ; giữ honorific đi sau tên như arase- San. Đây là xử lý ứng viên tên, không loại câu essential hoặc thêm pass LLM. Tên/alias đã có trong bank vẫn được đối chiếu thêm bằng chuỗi.

## Kết quả trên chapter hiện có ở ngưỡng 0.5

### Thử lại ở ngưỡng 0.7

[Review scan ngưỡng 0.7](../../outputs/after-school-we-do/chapter-001/20261004T105519Z-7340a5df/03_scan/review.md): 52 câu essential, 11 ứng viên (giảm từ 13), 0 lỗi, khoảng 11.5 giây khi model đã cache. Loại được nenenero 5 và Ah; vẫn giữ Arase Mahoru, Madoi Ayame, MAD01 AYAME, arase- San, Madol-san và Ayase-San. Nhiễu Clench, CONTRRI/BUTE và username goage04469 vẫn còn. Run thử chỉ hoàn thành đến scan, không thay tên trong bank hoặc kết quả run cũ.

### Ngưỡng 0.85 và lưu confidence

[Review scan ngưỡng 0.85](../../outputs/after-school-we-do/chapter-001/20261004T105833Z-a2124d39/03_scan/review.md): 52 câu essential, 8 ứng viên, 0 lỗi. So với 0.7, loại CONTRRI, BUTE và cả arase- San. Arase Mahoru đạt 0.9415, Madoi Ayame 0.9561, Madol-san 0.9767. Clench vẫn đạt 0.9693 dù là ứng viên nhiễu: score cao không xác nhận đây là tên nhân vật. Confidence lưu đầy đủ trong JSON và trình bày 4 chữ số thập phân trong review. Không sửa run cũ, không ghi bank.

### Bản đối chiếu ở ngưỡng 0.5

[Mở review scan mới](../../outputs/after-school-we-do/chapter-001/20261004T104555Z-f77ddcdd/03_scan/review.md).

52 câu essential, 13 ứng viên tên, 0 lỗi. Bước scan khoảng 11.4 giây khi trọng số đã có trong cache, gồm load model và inference. Kết quả tiêu biểu:

| Vị trí | Tên tìm thấy |
| --- | --- |
| P02/T23 | Arase Mahoru |
| P03/T06 | Madoi Ayame |
| P02/T02 | MAD01 AYAME — giữ nguyên OCR |
| P06/T06 | arase- San |
| P08/T04 | Madol-san |
| P04/T03 | Ayase-San — giữ nguyên OCR |

SpaCy trước đây không nhận hai tên đầy đủ trong các caption này. GLiNER còn có false positives như Clench, CONTRRI/BUTE và username do MAGI đánh dấu các text đó là essential. Ứng viên scan chưa phải mapping nhân vật; LLM phải xác nhận mention_type và kết luận nối ID trước khi ghi bank. Không tự merge các tên chỉ vì chung họ hoặc giống chữ.

Run thử dừng ở scan; dùng lại MAGI, chưa chạy classify/link/export, không ghi tên vào bank. Các run cũ giữ nguyên. 62 test offline đạt; thử model thật đã xác nhận hai tên đầy đủ xuất hiện trong scan. Chưa có bộ nhãn chuẩn để tính precision/recall toàn bộ tên.

```powershell
# Cài NER cho run mới.
.\venv\Scripts\python.exe -m pip install -r requirements-names.txt

# Chạy thử scan, dùng lại extraction.
.\venv\Scripts\python.exe run.py reanalyze --run-dir "outputs/after-school-we-do/chapter-001/20261004T041854Z-0eb4b189" --until scan

# Tiếp tục từ run thử đã tạo.
.\venv\Scripts\python.exe run.py resume --run-dir "outputs/after-school-we-do/chapter-001/20261004T104555Z-f77ddcdd"
```

Nguồn model và API: [model card GLiNER small v2.5](https://huggingface.co/gliner-community/gliner_small-v2.5), [GLiNER chính thức](https://github.com/urchade/GLiNER).
