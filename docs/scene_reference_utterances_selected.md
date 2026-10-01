# Nhãn scene đề xuất cho 36 utterances hiện tại

Nhãn được tạo từ việc đọc `D:\download\utterances_selected.json`, không xem output clustering. Người dùng đã duyệt cách nhóm bên dưới, không yêu cầu tách thêm và chọn giữ những ranh giới thiếu bằng chứng ở trạng thái chưa rõ.
File đã duyệt: `data/scene_review/utterances_selected.confirmed.json`. File `.proposed.json` được giữ làm bản đề xuất gốc.

| Vùng câu (đánh số từ 1) | Đề xuất | Lý do |
|---|---|---|
| 1–9 | Scene 1 | Đoạn giới thiệu/vlog và lời kể nối việc thử động cơ thất bại với việc Ayame nổi tiếng. Chuyển chủ đề ở câu 5 vẫn có liên kết kể chuyện rõ, nên chưa cắt riêng. |
| 10–14 | Scene 2 | Cuộc phỏng vấn của câu lạc bộ báo chí, có lượt hỏi/đáp và lời cảm ơn kết thúc. |
| 15–33 | Scene 3 | Hội thoại sau phỏng vấn giữa Mamoru và Ayame, từ chuyện quảng bá/funding sang nổi tiếng, sự dễ thương và động viên. Những đổi chủ đề này vẫn nằm trong cùng cuộc trò chuyện liên tục. |
| Trước câu 34 | Ambiguous | “Please subscribe to our channel~!” có thể là tập lời quảng bá ngay trong cuộc trò chuyện, hoặc chuyển sang một màn ghi hình khác. Chỉ thoại chưa đủ kết luận. |
| 34–36 | Giữ liên tục cục bộ | Tiếp nối lời quảng bá/ý tưởng trông dễ thương và phản ứng của Mamoru. Không tự xác nhận chúng cùng scene 3 vì gap trước câu 34 còn ambiguous. |

Không cắt chỉ vì thay trang. Câu 26 là tiếng phản ứng của người nói được JSON gán rõ; không tự loại nó như SFX chỉ dựa vào ký tự kéo dài.
Không dùng reset của `order_in_scene` làm nhãn tham chiếu: đó là metadata nguồn có thể đã qua suy luận, chưa phải quyết định được người dùng duyệt.

Các ranh giới scene đề xuất rõ: **trước câu 10** và **trước câu 15**.
Gap trước câu 34 được giữ là ambiguous theo lựa chọn của người dùng, không tính là đúng/sai trong benchmark.

Xem `docs/pipeline_usage.md`, bước 2 và 4, để dựng normalized JSON khớp fingerprint rồi xác nhận nhãn sau khi duyệt.
