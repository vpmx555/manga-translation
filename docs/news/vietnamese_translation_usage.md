# Dịch tiếng Việt và lọc text ở normalize

Run v4 mới chạy `extract → normalize → scan → analyze → export → translate`. Muốn dừng trước dịch, thêm `--until export`. `resume` dùng luồng đã lưu: run v2/v3 không tự thêm normalize policy hoặc stage dịch.

## Dịch export đã có

```powershell
.\venv\Scripts\python.exe -B run.py translate --run-dir "outputs/<story>/<chapter>/<run-id>"
```

Lệnh yêu cầu export đã commit và Ollama có model của run nguồn. Nó đọc cả `utterances` và `translation_only`, không lọc lại text, sửa manifest nguồn hay ghi bank nhân vật. Dùng `--config configs/pipeline.json` khi muốn đổi model/cấu hình dịch; không sửa snapshot config của run nguồn.

Hai lượt inference độc lập tạo hai phong cách: `natural` giữ nghĩa với tiếng Việt tự nhiên; `localized` dùng khẩu ngữ/tiếng lóng linh hoạt nhưng vẫn giữ nội dung. Proper names và hậu tố `-san/-kun/-chan` được giữ. Speaker/listener nguồn là dữ liệu bắt buộc; model không được sửa các ID này. OCR không rõ có thể được dịch phỏng đoán và phải đánh dấu review.

Kết quả nằm ở `<run>/translations/vi/<revision>/`:

- `review_natural.md`, `review_localized.md`: hai bảng song ngữ riêng, cảnh báo và đề xuất glossary chưa xác nhận.
- `natural.json`, `localized.json`: mỗi mục giữ ID, bucket, toàn bộ row nguồn và bản dịch.
- `result.json`: tổng hợp trạng thái hai phong cách; `latest.json` ở thư mục cha chỉ tới phiên bản vừa chạy.
- `snapshots/`, `plans/`, `attempts/`, `targets/`: dữ liệu đóng băng và checkpoint phục vụ resume.

Chạy lại cùng lệnh để retry câu thiếu/lỗi. Câu thành công và phản hồi đã journal không gọi model lại. Mỗi batch có tối đa một lượt repair cho target lỗi; nếu vẫn lỗi, mục ở trạng thái failed/partial và lượt chạy sau có thể retry. Timeout không được thay bằng tiếng Anh để coi là hoàn tất. Khi dùng stage trong pipeline, `resume` tiếp tục stage partial; stage đã commit được dùng lại.

## Glossary theo truyện

Mặc định đọc `<bank-directory>/translation_glossary.json`, ví dụ `banks/<story-id>/translation_glossary.json`. Có thể đặt đường dẫn khác trong `translation.glossary_path` của config:

```json
{
  "entries": [
    {"source": "student council", "vi": "hội học sinh", "confirmed": true},
    {"source": "nickname", "vi": "biệt danh đề xuất", "confirmed": false}
  ]
}
```

Chỉ mục `confirmed: true` vào prompt và được kiểm tra khi dịch. Đề xuất của model chỉ hiện trong JSON/review, không tự sửa glossary. Thay nội dung glossary đã xác nhận, model, chỉ dẫn hoặc ngữ cảnh mở rộng tạo revision mới, giữ các receipt model cũ.

## Sửa và khóa từng câu

Mặc định dùng `<run>/translation_overrides.json`; đặt đường dẫn khác bằng `translation.overrides_path`. Dùng đúng source ID trong review:

```json
{
  "natural": {"chapter:p001:t003:u": "Bản dịch đã sửa cho câu này."},
  "localized": {"chapter:p001:t003:u": "Bản khẩu ngữ đã sửa cho câu này."}
}
```

Chạy lại `translate` để áp dụng. Câu có override được đánh dấu `manual` và không gọi model cho phong cách đó, kể cả khi glossary/chỉ dẫn đổi. Xóa mục để mở khóa; không chỉnh trực tiếp target receipt, source export hay snapshot. Override chỉ định bản dịch nên không đổi speaker/listener. Cần sửa nghĩa/xưng hô bằng tay khi metadata nguồn sai. Pipeline resume của stage đã commit giữ artifact cũ; dùng lệnh translate riêng để tạo view cập nhật sau chỉnh tay.

Có thể thêm yêu cầu dịch vào `translation.instructions` trong file config; hai phong cách đều nhận chỉ dẫn này. `translation.batch_size` mặc định 3, `context_radius` 2, `num_predict` 1536. Thay cấu hình bằng file mới và `translate --config <file>`.

## Rule lọc ở normalize

Run mới lưu `whole-box-rules-v1` và snapshot rule. Chỉ loại cả box trùng nhãn SFX/âm thanh/hành động như `Clench`, `Whoosh`, `Hahhh`, watermark hoặc UI rõ ràng. Không xóa từ bên trong câu: `I clench my fists.` và bình luận có nghĩa vẫn được giữ. Text chưa chắc, dấu câu đơn lẻ hoặc chuỗi lạ được giữ và đánh dấu review. `excluded` giữ text gốc, source ID, vị trí và lý do.

Rule chung có bổ sung/ngoại lệ ở `<bank-directory>/normalization_rules.json`, hoặc `noise_filter.rules_path` trong config:

```json
{
  "keep": ["Clench"],
  "drop": {
    "sfx": ["Rustle"],
    "watermark": ["Read at Example Scans"],
    "ui": ["Tap to refresh"]
  }
}
```

Đối chiếu không phân biệt hoa thường và bỏ dấu câu ở cuối box; `keep` ưu tiên hơn `drop`. Override normalize theo ID cũng có ưu tiên. Rule được đóng băng khi tạo run: sửa file cho run tiếp theo, không đổi kết quả/plan của run đang dở. Dùng `reanalyze` để tái dùng extraction và áp dụng normalize mới; dịch riêng export cũ không áp dụng bộ lọc. Bản này chưa dùng detector nhẹ hay LLM tại normalize.

## Điểm mở rộng hồ sơ và đồ thị

`src/manga_pipeline/translation/context.py` định nghĩa `TranslationContextProvider.get_context(ContextQuery)`. Hiện chưa có provider mặc định và không đọc hồ sơ/đồ thị nhân vật. Provider tương lai nhận story/chapter, source hash, các target có ID/vị trí nguồn và stable character IDs; trả facts keyed theo source target ID. Node là nhân vật có stable ID, edge là quan hệ hoặc sự kiện quan trọng giữa nhân vật.

`ContextQuery.targets[].source_position` giữ page và text index của box gốc, độc lập với reading_order đã được đánh lại sau normalize. Provider phải giới hạn facts theo thời điểm câu, tránh dùng sự kiện xảy ra sau đó như dữ kiện đã biết. Prompt yêu cầu chỉ dùng facts của đúng target, không chuyển sự kiện/quan hệ từ target diễn ra sau. Context được snapshot và đưa vào hash revision/plan/request; thay context tạo lượt dịch mới. Có thể inject provider qua `Pipeline(..., context_provider=...)` hoặc `translate_store(..., context_provider=...)`, không cần thay adapter export hay logic checkpoint. Các token `others`, `narrator`, `groups` không trở thành node nhân vật.
