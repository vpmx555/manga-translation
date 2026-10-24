# Dịch natural, xóa chữ và dàn chữ Việt

[← Chỉ mục](../README.md)

## Translation contract

Pipeline hiện chỉ xuất style **`natural`**, policy `vi-natural-v2`. Không có lượt
dịch localized song song. Mỗi target là utterance hoàn chỉnh; source positions
được dùng sau để chia chữ lên vùng render, không để dịch từng mảnh.

Input gồm export semantic, scene summary, source context, frozen address table,
profile attributes/version, glossary confirmed, manual overrides và instructions.
Model không được đổi identity/relationship hoặc tự thêm quan hệ mới để chọn
đại từ. Unknown giữ unknown trong evidence và tạo cờ review phù hợp.

## Partition và concurrency

`translation_parallel.partition` tái sử dụng thứ tự target trong các batch
analyze đã export. Target chưa có batch được gom theo reading order thành các
batch bổ sung tối đa **8**; không bắt buộc cùng scene/gần nhau về nội dung.
Validator từ chối duplicate hoặc overlapping target batches.

Mỗi request đọc cùng graph/profile snapshot. Kết quả batch dịch trước không
được đưa vào batch sau; completion order không làm thay bảng xưng hô.
Concurrency là `min(v6.translation_concurrency, runtime.llm_slots)`, mặc định
trên config Runpod chính đều64. Admission/VRAM có thể hạn chế thực tế thấp hơn.

`translation.batch_size=8` vẫn có trong schema nhưng **không repartition** các
batch analyze trên đường parallel hiện tại. Sửa key này không có nghĩa đổi được
cỡ batch natural đã được planner chọn. Batch bổ sung8 là hằng số code của policy.

Reserve thực thi ban đầu được tính bằng `min(8192, max(1536, 384 * N))`, với N là
số target chưa có manual override. Vì vậy các key num_predict chung/stage không
trực tiếp ghi đè reserve của request parallel này. Budget/shape recovery có thể
chia request thành cây child cố định; không xóa target hay frozen address table.
Đường recovery translation khác với no-retry của profile/relationship.

## Glossary và manual text

`translation.glossary_path` nhận JSON có `entries`, mỗi entry gồm `source`, `vi`,
`confirmed`. Chỉ entry confirmed được dùng; duplicate source confirmed bị chặn.

`translation.overrides_path` nhận map theo style và target ID, ví dụ:

```json
{
  "natural": {
    "<target-id-thuc-te-trong-export>": "Câu tiếng Việt đã sửa."
  }
}
```

ID phải tồn tại và text không rỗng. Đây là format minh họa; phải thay placeholder
bằng ID thật. Không sửa khóa ID trong export để khớp một override tự đặt.
Glossary/overrides được snapshot cho run; thay đổi cần revision/run mới, không
sửa file ngoài rồi kỳ vọng resume thay đổi run đã freeze.

## Inpaint

Khi `inpaint.enabled=true`, stage xóa chữ tạo clean pages trước render. White
fill được dùng khi vùng phù hợp theo các điều kiện màu/trắng; AOT xử lý trường
hợp cần inpainting. `dilation` ảnh hưởng mask xóa; `max_side` giới hạn kích thước
xử lý. Chỉ bật inpaint khi đã chuẩn bị model/asset receipts.

Tắt inpaint vẫn cho phép thực hiện những stage không cần ảnh sạch. Báo cáo
render phải phản ánh `clean_pages_available`; không gọi ảnh còn chữ gốc là trang
dịch đã xóa chữ hoàn chỉnh.

## Typeset mặc định

`v6.typeset_mode=ocr-box` là mode được validator chấp nhận. Các tài liệu v6 về
mask/contour hoặc experimental OCR-area là lịch sử/thiết kế, không phải mode
mặc định tự chọn khi vùng OCR lỗi.

Typesetter lấy natural text, chọn font hỗ trợ tiếng Việt, thử cỡ chữ trong
`font_min_px..font_max_px`, phân bổ/line wrap trong vùng hợp lệ của OCR source
positions và ghi layout plan/provenance. Một utterance có thể được phân bố qua
nhiều part mà vẫn dùng bản dịch chung.

Nếu không fit, số lượt rút gọn bị chặn bởi `shorten_attempts` (mặc định2, max3).
Đây là lượt rút ngắn phục vụ render, không phải một style localized. Vẫn không
fit ở font tối thiểu thì ghi overflow/empty safe region để review; không phóng
chữ tràn hoặc dùng vùng khác âm thầm. Font path/hash và kích thước được giữ.

Effects được dịch trong dữ liệu nhưng `effects_image_edits=false`: không tự
inpaint/typeset SFX như bubble thoại. Cần phân biệt có bản dịch effect trong
JSON với đã chỉnh effect lên trang.

## Kiểm tra đầu ra

Xem text natural và address-table lineage trước, rồi xem ảnh trang/plan font,
overflow, clean-page availability và effect flags. Đúng schema không chứng minh
tiếng Việt hay, xưng hô đúng hoặc không che artwork; chất lượng cần đối chiếu
ngữ cảnh và hình ảnh. Giữ output đầy đủ để đánh giá thay vì chỉ export ảnh cuối.

Nguồn mã: `v6/translation_parallel.py`, `v6/translation.py`,
`translation/contract.py`, `v6/typeset.py`, `visual/inpaint.py`.
