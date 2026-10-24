# Review cấp số và tái sử dụng bank MAGI — 2026-10-22

Phạm vi: đọc code và thống kê bản sao bank local của batch `runpod-iris-zero-ch1-v6-l40s-inference-first-20261021-r3`; không chạy model, không thay đổi bank.

## Kết luận

Số MAGI là khóa của một hồ sơ ngoại hình trong phạm vi truyện, không phải số thứ tự nhân vật theo cốt truyện và không bảo đảm một người chỉ có một số. Tái sử dụng nguyên bank giữ nguyên các số đã cấp. Chạy extraction lại chỉ cố gắng nhận diện lại bằng embedding, không bảo đảm kết quả số giống lần trước. Chạy lại analyze từ artifact extraction cũ giữ nguyên source bank IDs.

## Phát hiện

1. **P1 — Nhận diện sai có thể tự củng cố khi tái sử dụng.** `bank/assignment.py:_resolve_existing` nhận ứng viên nếu distance <= 0.65 và chênh ứng viên thứ hai >= 0.02; trường hợp chỉ có một ứng viên thì margin là vô hạn. Ngay khi nhận, `_add_prototypes` bổ sung ngoại hình vào hồ sơ đó, chưa có bước xác nhận độc lập. Một lần ghép nhầm có thể ảnh hưởng các trang/chương sau. Đây là rủi ro từ thuật toán, chưa phải kết luận bank thực tế đã gán sai. Nên lưu quyết định/điểm số và giữ prototype mới ở trạng thái chờ trước khi dùng cho nhận diện tiếp theo.

2. **P2 — Pending không hỗ trợ xác nhận xuyên chương.** `_match_pending` chỉ xét origin nằm trong danh sách trang của lượt hiện tại và cách 1–2 trang; origin ngoài lượt hiện tại bị đóng. Nhân vật chỉ xuất hiện một lần mỗi chương có thể tiếp tục không có số dù xuất hiện nhiều chương. Pending đóng vẫn nằm trong metadata và embeddings. Bank hiện có 124 pending: 123 closed, 1 active. Nên tách cửa sổ xác nhận gần với kho ứng viên lâu dài, có chính sách tái xét và dọn dữ liệu rõ ràng.

3. **P2 — Chạy extraction lại không có ánh xạ idempotent theo quan sát.** Gán số là phép nhận diện tuần tự bằng bank đang thay đổi. Pending mới dùng UUID; một pending được promote chỉ cập nhật các vị trí trong `_current_pending_occurrences` của lượt hiện tại, không sửa artifact cũ. Chạy lại có thể có thêm pending hoặc thêm số khi ngoại hình không đạt ngưỡng. Nên lưu khóa quan sát từ hash ảnh + geometry/model contract + detection và tái sử dụng quyết định đã commit. Khi chỉ cần analyze lại, tiếp tục dùng extraction đã đóng băng.

4. **P2 — Metadata và embeddings chưa commit thành một thế hệ nguyên tử.** `bank/identity.py:save` thay vectors trước, metadata sau. Khóa ngăn hai writer thông thường, nhưng dừng đột ngột giữa hai thao tác có thể để hai file khác thế hệ. `load` chưa xác nhận checksum/liên kết đầy đủ giữa character record và vectors. Nên dùng generation directory + manifest checksum và một con trỏ commit nguyên tử.

## Cách cấp số hiện tại

- Mỗi truyện có thư mục bank riêng; `next_character_id` khởi tạo 1, tăng khi tạo hoặc promote nhân vật. Đổi display name không đổi số. Merge giữ target, disable source và lưu `merged_into`; không cấp lại số source.
- MAGI gom detection thành cluster trong từng trang. Mỗi cluster tạo một quan sát embedding. Duyệt theo thứ tự trang và nhóm detection; vì vậy thứ tự cấp số phụ thuộc thứ tự xử lý và thời điểm đủ điều kiện.
- Nếu match bank đạt ngưỡng thì dùng số cũ. ID đã được nhóm khác dùng trong cùng panel bị loại khỏi ứng viên để hạn chế ghép hai người thành một. Ràng buộc này cũng có thể tách một người thành nhiều hồ sơ nếu cùng panel có nhiều hình của họ nhưng MAGI tách cluster.
- Nếu không match: nhóm có ít nhất hai detection được cấp số ngay; nhóm một detection vào pending. Pending match tại một trong hai trang sau được promote và cập nhật lại vị trí đã thấy trong cùng lượt chạy.
- Mỗi số giữ tối đa 8 prototypes và 4 crops. Prototypes chọn theo độ đa dạng; khoảng cách so khớp dùng embedding chuẩn hóa và tối đa 3 prototypes gần nhất. Đây là heuristic, không phải xác suất nhận diện đã hiệu chuẩn.

## Các lớp tái sử dụng

- **Cùng truyện, chương mới:** nạp bank metadata + vectors, match ngoại hình và cấp tiếp số mới. Khác truyện có thể có cùng số nhưng không cùng danh tính.
- **Chạy extraction lại:** model/revision và geometry contract phải tương thích; có khóa writer. Tuy nhiên ngưỡng extraction không nằm trọn trong geometry fingerprint hiện tại, nên qua kiểm tra tương thích chưa có nghĩa detection và số sẽ lặp lại y hệt.
- **Chỉ chạy analyze lại:** giữ extraction + bank/snapshot đã xác minh; không cần MAGI nhận diện lại. Import hiện có kiểm tra inventory và không ghi đè bank khác với bản frozen.
- **Semantic v6:** tạo person nội bộ từ story + bank ID; phần chưa có bank ID dựa trên cluster có scope. Semantic có thể hợp nhất nhiều số MAGI về một người nhưng không đổi bank MAGI. Review hiển thị `MAGI 5/6` có nghĩa một person semantic đang liên kết hai hồ sơ ngoại hình, không phải bank đã tự đổi số.
- **Merge bank thủ công:** số source bị disable, target được giữ; artifact extraction cũ vẫn chứa số lịch sử. Cần resolve chuỗi `merged_into` nhất quán khi dùng dữ liệu cũ; bootstrap semantic hiện tạo person trực tiếp từ bank ID trong artifact, không tự chuẩn hóa chuỗi merge này.

## Dữ liệu đã kiểm tra

`outputs/runpod-iris-zero-ch1-v6-l40s-inference-first-20261021-r3/live/banks/iris-zero/metadata.json`: 21 character records, số 1–21, next ID 22, không có character disabled; 124 pending. Không thể suy ra có đúng 21 người thực tế chỉ từ các con số này.

Code tham chiếu: `src/manga_pipeline/bank/{identity,assignment}.py`, `src/manga_pipeline/extraction/magi.py`, `src/manga_pipeline/pipeline.py:prepare_bank`, `src/manga_pipeline/v6/persons.py:bootstrap`, `deployment/runpod/preanalyze_import.py`, `src/manga_pipeline/review/v6.py:person`.

Ưu tiên: lưu provenance của match và cách ly prototype chưa xác nhận; bổ sung reuse theo observation; thống nhất resolve merge; sau đó cải thiện pending xuyên chương và commit bank theo generation. Chưa triển khai các đề xuất trong review này.
