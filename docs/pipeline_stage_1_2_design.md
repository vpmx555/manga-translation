# Thiết kế preliminary text → speaker cluster và dialogue normalization

Trạng thái: người dùng đã yêu cầu triển khai. Baseline và công cụ đánh giá được triển khai; chọn embedding/ngưỡng production sau khi có nhãn đã duyệt và benchmark. Hướng dẫn: `docs/pipeline_usage.md`.

## Mục tiêu và phạm vi

Triển khai hai phần đầu của pipeline manga:

1. Bảo toàn kết quả visual extraction của MAGI và liên kết sơ bộ text → character detection → visual cluster → character bank ID nếu đã xác nhận.
2. Chuẩn hóa hội thoại: thứ tự đọc, nhóm các mảnh utterance, lọc nội dung không dùng để dịch và phân scene theo nội dung.

Đọc phải sang trái. Không xử lý trang đôi trong bản đầu.

Chưa triển khai semantic speaker correction, suy luận tên nhân vật, addressee, quan hệ nhân vật, dịch hoặc inpaint. Các trường người nghe trong JSON dịch hiện có không phải kết quả được suy luận trong giai đoạn này.

## Những quyết định đã được người dùng chọn

- Lưu cả raw extraction và normalized dialogue, có liên kết truy ngược về nguồn.
- Giữ các trường hợp mơ hồ để kiểm tra; không cưỡng ép gán speaker hoặc gộp câu.
- Scene là đoạn liên tục về nội dung truyện, gom từ những câu thoại liền nhau và có thể đi qua nhiều trang.
- Phân scene bằng embedding và clustering trong bản đầu. Không dùng Gemma để kiểm tra ranh giới scene.
- Phân scene chủ yếu từ nội dung hội thoại, bao gồm lời dẫn/độc thoại nội tâm được giữ lại. Không suy luận ranh giới scene từ ảnh trong bản đầu.
- Giữ lời dẫn và độc thoại nội tâm. Loại UI, watermark và SFX khỏi luồng dùng để dịch/phân scene sau khi xác định được loại nội dung.
- Chỉ gộp các box có bằng chứng là mảnh của cùng utterance. Không gộp chỉ vì cùng speaker hoặc đứng cạnh nhau. Không tự gộp qua panel/trang trong bản đầu.
- Hạn chế gộp để bảo toàn khả năng đặt bản dịch trở lại vị trí trên ảnh.
- Chưa chốt embedding cố định. So sánh MPNet, EmbeddingGemma và Qwen3 trên dữ liệu manga có nhãn scene tham chiếu trước khi chọn model/ngưỡng cho pipeline.
- Trợ lý đề xuất ranh giới scene từ việc đọc hội thoại; người dùng duyệt và sửa. Chỉ nhãn đã được người dùng duyệt mới dùng để đánh giá.
- Nhãn đề xuất được tạo độc lập với kết quả clustering. Tách dữ liệu hiệu chỉnh và dữ liệu kiểm tra.
- Scene là tín hiệu bổ sung cho context dịch về sau. Khi ranh giới mơ hồ, vẫn giữ context hội thoại lân cận thay vì cắt cứng theo scene dự đoán.

## Cơ sở từ mã hiện tại

- `src/main.py` đã sử dụng MAGI chapter prediction, rồi ánh xạ `text_character_associations` sang `character_names` để xuất transcript `.txt`.
- MAGI trả về panel/text/character/tail boxes, hard associations, visual cluster labels, OCR và essential-text flags.
- Affinity scores được tính bên trong MAGI nhưng chưa có trong output tiêu chuẩn. Không được tự gọi hard association hoặc embedding distance là speaker confidence.
- `Other` hiện gộp ít nhất hai tình huống: không có text→character association và có association nhưng nhân vật còn ở trạng thái pending trong character bank.
- Việc xuất `.txt` hiện bỏ mất geometry/provenance và lọc theo essential-text flag. Flag này chưa đủ để xác định riêng dialogue, narration, thought, UI, watermark hoặc SFX.

## Thiết kế dữ liệu đề xuất để xác nhận

### Raw extraction

Giữ toàn bộ vùng text kể cả nội dung sẽ bị loại khỏi luồng chuẩn hóa. Mỗi vùng có ID ổn định trong một bản extraction và các thông tin sẵn có:

- Trang, đường dẫn ảnh, kích thước ảnh, text index, OCR gốc, tọa độ box và panel liên quan.
- Character detection được MAGI liên kết, visual cluster gốc và phạm vi của cluster.
- Character bank ID nếu đã được xác nhận; không đồng nhất visual cluster ID với bank ID hoặc tên hiển thị.
- Trạng thái speaker riêng: resolved, pending identity hoặc unlinked. Narration/unknown được biểu diễn rõ khi có đủ bằng chứng.
- Essential-text flag và các scores thực sự thu được, kèm nguồn. Không chế tạo scores khi nguồn không cung cấp.

Không thay đổi character bank hoặc dùng suy luận ngữ nghĩa để sửa speaker ở giai đoạn này. Điểm affinity sơ bộ không được coi là xác suất đúng đã hiệu chỉnh.

### Normalized dialogue

Mỗi record chứa text gốc, text chuẩn hóa, tham chiếu source text IDs, thông tin speaker sơ bộ, thứ tự đọc, loại nội dung nếu xác định được, trạng thái cần kiểm tra, scene ID và dữ kiện hỗ trợ ranh giới scene.

Chuẩn hóa text chỉ xử lý các vấn đề định dạng có thể kiểm tra được; không viết lại nội dung OCR bằng suy đoán.

Một utterance có thể tham chiếu nhiều text box, nhưng không xóa hoặc thay thế box gốc bằng một box lớn. Luôn giữ mapping giữa utterance và từng box nguồn để các bước dịch/inpaint về sau còn phân bổ bản dịch đúng vị trí. Cách phân bổ một bản dịch vào nhiều box chưa nằm trong phạm vi này.

Nội dung được nhận diện là UI/watermark/SFX không đi vào luồng dialogue dùng để dịch/phân scene. Vẫn lưu raw record và lý do loại để có thể khôi phục/kiểm tra. Trường hợp phân loại chưa chắc được giữ và đánh dấu, theo quyết định xử lý thận trọng.

Lời dẫn và độc thoại nội tâm không bị loại chỉ vì MAGI đánh dấu nonessential. Không ép chúng thành dialogue có người nghe.

## Phân scene: phương án kỹ thuật đề xuất để xác nhận

- Cung cấp backend embedding có thể thay đổi và cache theo model/revision/prompt/preprocessing. Không trộn vector giữa các model hay cấu hình.
- So sánh ba ứng viên: `sentence-transformers/all-mpnet-base-v2` (baseline tiếng Anh), `google/embeddinggemma-300M` (ứng viên chính để thử, dùng prompt `Clustering`) và `Qwen/Qwen3-Embedding-0.6B` (ứng viên đối chứng dùng instruction phù hợp và nhất quán). Chưa có bằng chứng model nào tốt nhất cho scene manga của người dùng.
- Nếu nguồn chuyển sang ngôn ngữ khác, cần kiểm tra lại embedding phù hợp. Việc model hỗ trợ clustering/MTEB không chứng minh nó nhận ra ranh giới scene manga.
- Lấy chuỗi utterance đã được sắp thứ tự làm đầu vào. Câu ngắn/thiếu nội dung được xem cùng một cửa sổ ngữ cảnh nhỏ; cửa sổ phải được giới hạn để tránh làm nhòe ranh giới.
- Khi so sánh hai phía ranh giới, tránh cửa sổ chồng lấn quá nhiều: text dùng chung có thể làm tương đồng cao giả tạo.
- Phân cụm phân cấp với điều kiện chỉ gộp các nhóm kề nhau. Mỗi scene bắt buộc là một đoạn liên tục trong chuỗi.
- Kiểm tra độ nhất quán của cả cụm để tránh các câu trung gian nối hai đoạn nội dung khác nhau.
- Không buộc biết trước số scene. Ngưỡng gộp được cấu hình và cần hiệu chỉnh trên các ranh giới gán thủ công; chưa có một ngưỡng cosine được coi là đúng cho mọi truyện.
- Trang hoặc việc đổi speaker không tự tạo ranh giới scene. Topic xuất hiện lại ở xa không làm hai scene bị gộp thành một.
- Không đưa ID speaker như thể đó là tên hoặc ngữ nghĩa vào embedding. Liên kết speaker là thông tin cấu trúc và có thể còn sai.
- Xuất các ranh giới mơ hồ/thiếu bằng chứng để kiểm tra. Không gọi điểm tương đồng hoặc điểm ranh giới là xác suất đúng nếu chưa hiệu chỉnh.
- Semantic TextTiling có thể dùng làm phương pháp đối chứng sau khi baseline hoạt động; không phải thành phần bắt buộc của bản đầu.

## Quy trình nhãn tham chiếu và benchmark

1. Xuất hội thoại chuẩn hóa theo đúng thứ tự với source IDs ổn định để người dùng kiểm tra.
2. Trợ lý đọc hội thoại để đề xuất ranh giới scene, kèm lý do và trường hợp chưa chắc. Không xem kết quả clustering để tạo các nhãn này.
3. Người dùng duyệt/sửa. Nhãn chưa được duyệt không được coi là ground truth. Những ranh giới không thể xác định chỉ từ hội thoại được lưu riêng là ambiguous, không ép thành đáp án chắc chắn.
4. Chọn các chương/đoạn đại diện và tách phần hiệu chỉnh với phần kiểm tra. Ưu tiên tách theo chương khi có đủ dữ liệu; không gọi kết quả trên tập nhỏ một chương là đánh giá tổng quát.
5. So sánh ba embedding bằng cùng họ thuật toán phân cụm và cùng quy trình đánh giá. Ngưỡng có thể được hiệu chỉnh riêng cho từng embedding trên phần hiệu chỉnh vì thang điểm tương đồng khác nhau; không chỉnh theo kết quả của phần kiểm tra.
6. Báo cáo precision/recall/F1 ranh giới, WindowDiff, lỗi gộp quá mức/chia quá nhỏ và thời gian chạy. Nêu rõ cách xử lý ranh giới ambiguous và thiếu dữ liệu; không chọn model chỉ dựa trên silhouette hoặc điểm benchmark tổng của nhà cung cấp.
7. Chọn embedding/cấu hình dựa trên kết quả đã được kiểm tra và xem xét các ví dụ sai. Nếu chưa đủ tốt, output vẫn là scene đề xuất cùng trạng thái mơ hồ, không đóng vai trò ranh giới cứng.

Không dùng Gemma sinh văn bản để tự xác nhận nhãn tham chiếu hoặc quyết định scene trong clustering baseline. EmbeddingGemma là model embedding riêng, không phải model Gemma 4 đang dùng trong script dịch.

Việc đánh giá ảnh hưởng tới bản dịch là bước tiếp theo: so sánh context theo scene dự đoán, scene tham chiếu và context lân cận. Chưa tuyên bố scene clustering cải thiện bản dịch khi chưa đánh giá bước đó.

## Tổ chức triển khai đề xuất

- Tách phần xuất raw và chuẩn hóa thành các module để có thể chạy lại normalization từ raw JSON mà không phải chạy MAGI lại.
- Tích hợp với entry point hiện tại và tiếp tục cho phép xuất transcript dễ đọc.
- Tách công cụ tạo/duyệt nhãn tham chiếu và chạy benchmark khỏi luồng production; triển khai baseline và công cụ đánh giá trước, chọn model production sau khi có nhãn đã duyệt.
- Giữ mọi thay đổi có sẵn trong workspace; không ghi đè ảnh/transcript/dữ liệu người dùng ngoài các output được yêu cầu.

## Kiểm tra và giới hạn

Kiểm tra mapping text→detection→cluster→bank ID, phân biệt pending/unlinked, thứ tự phải→trái, giữ narration/thought, audit nội dung bị lọc, gộp thận trọng và bảo toàn source boxes.

Kiểm tra scene là các đoạn liên tục, không tự cắt theo trang và không gộp các đoạn ở xa. Đánh giá bằng ranh giới scene gán thủ công, tập trung vào lỗi chia quá nhỏ/gộp quá mức. Silhouette score không đủ để kết luận chất lượng scene.

Clustering ngữ nghĩa có thể chia một scene khi nhân vật đổi chủ đề hoặc bỏ sót một scene mới vẫn bàn cùng chủ đề. Đổi cảnh không có lời thoại có thể không được phát hiện. UI/SFX và lời dẫn có thể bị OCR/classifier nhầm. Các trường hợp đó cần được thể hiện như giới hạn hoặc mục cần kiểm tra, không suy diễn thành dữ kiện chắc chắn. Vì ảnh hưởng này, downstream không được coi scene chưa xác nhận là chân lý để loại bỏ toàn bộ context lân cận.

Việc chọn ngưỡng và đánh giá định lượng trên chương mẫu là công việc hiệu chỉnh sau triển khai baseline; chưa có cam kết chất lượng khi chưa có nhãn tham chiếu.

## Nguồn phương pháp

- Agglomerative clustering và connectivity constraints: https://scikit-learn.org/stable/modules/generated/sklearn.cluster.AgglomerativeClustering.html
- English embedding baseline: https://huggingface.co/sentence-transformers/all-mpnet-base-v2
- TextTiling: https://aclanthology.org/J97-1003/
- EmbeddingGemma clustering prompt: https://ai.google.dev/gemma/docs/embeddinggemma/inference-embeddinggemma-with-sentence-transformers
- Qwen3 embedding: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- WindowDiff: https://aclanthology.org/J02-1002/

## Xác nhận trước triển khai

Skill `.codex/skills/grill-me/SKILL.md` yêu cầu: “Do not implement the plan until they confirm or explicitly ask you to proceed.”

Người dùng đã xác nhận bằng yêu cầu “triển khai”. Không cần xin lại quyền triển khai phạm vi đã thống nhất. Việc duyệt nhãn scene tham chiếu vẫn là bước riêng của người dùng; không tự coi nhãn đề xuất là ground truth.
