# Chapter 2. Related Work — Proposed Outline

## Phạm vi và định hướng

Dàn ý dành cho đồ án nghiên cứu pipeline xử lý manga và dịch sang tiếng Việt,
với trọng tâm là tính liên tục của nhân vật, gán người nói/người nghe, profile
nhân vật, quan hệ và xưng hô có hướng. Các nghiên cứu về dữ liệu, layout,
detection, OCR và xử lý ảnh đầu ra tạo nền tảng cho các vấn đề đó.

Giữ tám phần chính, đánh số liên tục từ 2.1 đến 2.8. Không mở phần riêng về
machine translation. Chỉ nhắc tác động tới bản dịch khi phân tích ý nghĩa của
identity, attribution, relationship và typesetting.

Dung lượng mục tiêu: **14 trang nội dung**, có thể điều chỉnh trong **10–18 trang**.
Phân bổ dưới đây mang tính định hướng, phụ thuộc mẫu luận văn, cỡ chữ và giãn dòng;
không tính danh mục tài liệu tham khảo. Bảng so sánh nằm trong dung lượng chương.

| Phần | Mục tiêu 14 trang | Bản gọn 10 trang | Bản mở rộng 18 trang |
|---|---:|---:|---:|
| 2.1. Datasets and Benchmark Foundations | 1.25 | 0.75 | 1.50 |
| 2.2. Manga Layout Analysis and Reading Order | 1.25 | 0.75 | 1.50 |
| 2.3. Manga Object Detection and Segmentation | 1.50 | 1.00 | 2.00 |
| 2.4. Manga Text Recognition | 1.50 | 1.00 | 2.00 |
| 2.5. Character Re-identification and Persistent Identity | 2.50 | 2.00 | 3.25 |
| 2.6. Speaker and Addressee Attribution | 2.00 | 1.50 | 2.50 |
| 2.7. Dialogue and Character Relationship Modeling | 2.25 | 2.00 | 3.00 |
| 2.8. Text Removal, Inpainting, and Vietnamese Typesetting | 1.75 | 1.00 | 2.25 |
| **Tổng** | **14.00** | **10.00** | **18.00** |

Nguyên tắc triển khai: tổ chức theo **bài toán và hướng phương pháp**, không theo
danh sách model. MAGI, Koharu, GLM-OCR và các thành phần dự án là điểm liên hệ
khi phù hợp, không phải tên tiểu mục. Phân biệt kết quả nghiên cứu đã công bố,
cơ chế đã triển khai và thiết kế mới còn đang đề xuất.

## 2.1. Datasets and Benchmark Foundations

**Câu hỏi dẫn dắt:** Dữ liệu và benchmark hiện có hỗ trợ đánh giá những thành
phần nào của một pipeline manga, và còn thiếu nhãn hoặc giao thức nào cho bài
toán identity–dialogue–relationship xuyên trang?

### 2.1.1. Manga and Comic Datasets: Coverage and Annotation Granularity

- Tổng quan các nhóm dữ liệu manga/comic theo ngôn ngữ, phong cách vẽ và đơn vị
  tổ chức: trang, panel, chapter hoặc tác phẩm.
- Đối chiếu nhãn panel, text region, balloon, character, mask và liên kết giữa
  các đối tượng; nêu giới hạn truy cập hoặc sử dụng khi ảnh hưởng việc đánh giá.
- Liên hệ dự án: phân biệt bộ dữ liệu huấn luyện/benchmark của công trình trước
  với các chapter dùng làm trường hợp nghiên cứu, như Iris Zero và Bocchi.
  Không mặc định chapter thực nghiệm là một benchmark đã chuẩn hóa.

### 2.1.2. Identity, Speaker, and Dialogue Annotations

- Xem xét dữ liệu có identity nhân vật lặp lại, liên kết text–speaker,
  addressee, tên/alias và quan hệ hội thoại.
- Phân biệt nhãn nhận diện trong một trang với identity xuyên trang/chapter;
  phân biệt người được nhắc tới với người đang được nói chuyện cùng.
- Nêu loại nhãn cần có để kiểm chứng bước gộp identity và graph thay vì chỉ
  quan sát một vài output tốt.

### 2.1.3. Evaluation Protocols and Cross-Stage Error Propagation

- Tổ chức các nhóm chỉ số theo nhiệm vụ: localization/segmentation, OCR,
  re-identification, attribution và chất lượng kết quả đầu ra.
- Bàn về chia dữ liệu theo trang/chapter/tác phẩm, nguy cơ rò rỉ nhân vật giữa
  train–test và việc phân biệt đánh giá từng thành phần với đánh giá toàn pipeline.
- Đặt cơ sở cho việc theo dõi lỗi từ detection/OCR tới identity, attribution và
  xưng hô. Chưa trình bày giao thức thực nghiệm riêng của đồ án tại đây.

**Sản phẩm nên có:** một bảng ngắn so sánh annotation và phạm vi benchmark;
không dành nhiều trang mô tả từng dataset riêng lẻ.

## 2.2. Manga Layout Analysis and Reading Order

**Câu hỏi dẫn dắt:** Làm thế nào biến trang manga thành cấu trúc và chuỗi đọc
có thể sử dụng làm ngữ cảnh hội thoại?

### 2.2.1. Panel Localization and Page Structure Analysis

- Các hướng nhận diện panel và phân tích cấu trúc trang: hình học, học có giám
  sát và biểu diễn layout bằng mô hình thị giác/ngôn ngữ.
- Các trường hợp khó cần so sánh: panel không viền, panel lồng nhau, hình vẽ
  vượt khung và trang có bố cục phi chuẩn.
- Liên hệ đầu ra layout của dự án với việc giữ ngữ cảnh trong cùng panel;
  không chuyển sang đánh giá character detection ở tiểu mục này.

### 2.2.2. Panel and Text Reading Order Estimation

- Phân biệt thứ tự panel, thứ tự text region trong panel và thứ tự đoạn hội thoại.
- So sánh quy tắc không gian, quan hệ hình học và các cách học thứ tự từ dữ liệu;
  xét hướng đọc và vùng text nằm ngoài panel.
- Phân tích hệ quả khi một liên kết thứ tự sai làm thay đổi câu trước–sau,
  lượt lời hoặc ngữ cảnh được cung cấp cho bước phân tích hội thoại.

### 2.2.3. Layout-Aware Context Construction

- Các cách biểu diễn trang/panel/text bằng cấu trúc phân cấp, tọa độ hoặc quan
  hệ không gian để cung cấp cho các tác vụ sau.
- So sánh ngữ cảnh dựa trên thứ tự gần nhau với ngữ cảnh dựa trên cấu trúc panel.
- Nối sang attribution và dialogue modeling: layout cung cấp bằng chứng tổ chức,
  chưa tự xác định người nói hay quan hệ giữa các nhân vật.

**Ranh giới:** phần này tập trung panel, cấu trúc và thứ tự; đối tượng bên trong
trang thuộc 2.3, nội dung chữ thuộc 2.4.

## 2.3. Manga Object Detection and Segmentation

**Câu hỏi dẫn dắt:** Những đối tượng và vùng hình học nào cần được định vị để
phục vụ OCR, nhận diện nhân vật, attribution và phục dựng ảnh?

### 2.3.1. Text Region and Speech Balloon Detection

- Các hướng phát hiện text box, speech balloon và phân biệt hai đối tượng này.
- Phân tích dialogue, narration, thought text và sound effects ở góc độ loại
  vùng văn bản; chưa giải thích toàn bộ ngữ nghĩa hội thoại.
- So sánh bounding box và mask, đặc biệt khi text nằm ngoài balloon, nhiều
  đoạn text cùng balloon hoặc chữ cách điệu có hình dạng không đều.

### 2.3.2. Character Localization and Instance Segmentation

- Phát hiện face/body/character instance và sự khác nhau giữa chúng khi tạo crop.
- Các khó khăn: che khuất, nhân vật nhỏ ở hậu cảnh, góc quay lưng, biểu cảm
  chibi và nhiều người nằm trong cùng crop.
- Phân tích chất lượng localization ảnh hưởng độ thuần của embedding và bank;
  không coi detection đúng là đã giải quyết được identity.

### 2.3.3. Balloon Geometry, Tail Analysis, and Object Association

- Biểu diễn thân balloon, phần đuôi và quan hệ text–balloon–character.
- Các cách sử dụng overlap, containment, khoảng cách và hướng hình học để tạo
  ứng viên liên kết; xét balloon dính nhau hoặc bị che khuất.
- Làm rõ một liên kết hình học là bằng chứng ứng viên, chưa phải kết luận
  speaker/addressee cuối cùng. Nối sang 2.6 và mask xóa chữ ở 2.8.

## 2.4. Manga Text Recognition

**Câu hỏi dẫn dắt:** Những đặc điểm nào khiến OCR manga khác OCR tài liệu thông
thường, và các hướng nhận dạng xử lý chúng ra sao?

### 2.4.1. Specialized OCR for Stylized and Multilingual Manga Text

- Các hướng OCR chuyên biệt cho truyện và những đặc điểm đầu vào: font cách
  điệu, chữ nghiêng, dấu câu, ngắt dòng, chữ nhỏ và hướng chữ khác nhau.
- Phân biệt phạm vi manga tiếng Nhật với manga đã được biên tập bằng tiếng Anh;
  không chuyển kết luận giữa hai miền dữ liệu mà thiếu bằng chứng.
- So sánh theo dữ liệu, đơn vị nhận dạng và độ bền trước biến dạng/chất lượng crop.

### 2.4.2. Vision–Language Models for Text Recognition

- OCR dựa trên mô hình thị giác–ngôn ngữ và sự khác nhau giữa nhận dạng crop,
  nhận dạng toàn trang và suy luận tài liệu có layout.
- Những trục cần đối chiếu với OCR chuyên dụng: độ chính xác, tốc độ, tài nguyên,
  khả năng giữ text gốc và nguy cơ sinh nội dung không có trong ảnh.
- Liên hệ việc dự án sử dụng GLM-OCR; chỉ kết luận về chất lượng tương đối khi
  có nguồn hoặc thực nghiệm hỗ trợ, không viết rằng VLM mặc nhiên tốt hơn.

### 2.4.3. OCR Error Handling and Downstream Reliability

- Các loại lỗi quan trọng đối với pipeline: thiếu chữ, sai thứ tự dòng, dấu nối,
  dấu câu và biến thể chính tả của tên nhân vật.
- So sánh giữ nguyên transcript, chuẩn hóa văn bản và sửa lỗi có tham chiếu
  tới vị trí nguồn; phân biệt sửa hình thức OCR với suy đoán nội dung.
- Phân tích tác động tới name linking và dialogue attribution. Nhóm alias và
  việc xác lập identity được thảo luận sâu ở 2.5.

## 2.5. Character Re-identification and Persistent Identity

**Câu hỏi dẫn dắt:** Làm thế nào duy trì một nhân vật xuyên nhiều lần xuất hiện
khi ngoại hình thay đổi và bằng chứng hình ảnh/tên có thể không nhất quán?

### 2.5.1. Visual Embeddings for Character Re-identification

- Biểu diễn đặc trưng và metric learning cho việc đối chiếu nhân vật được vẽ,
  cùng các hướng phân cụm hoặc truy hồi theo độ giống hình ảnh.
- Xem xét khoảng cách giữa crop, giữa crop và prototype, và giữa prototype;
  không trình bày chúng như những đại lượng có cùng ý nghĩa quyết định.
- Thảo luận góc nhìn, quần áo, phong cách vẽ, nhân vật trông giống nhau và
  biến dạng biểu cảm như các yếu tố cần kiểm chứng bằng thực nghiệm.

### 2.5.2. Prototype Selection and Persistent Character Banks

- Đại diện bằng một vector trung bình, một nhóm mẫu hoặc nhiều prototype;
  các hướng chọn mẫu điển hình, đa dạng và loại ngoại lệ.
- Bank có giới hạn bộ nhớ, cập nhật theo lần xuất hiện và nguy cơ prototype
  drift hoặc mẫu nhiễu làm sai việc nhận diện sau đó.
- Đặt việc chọn crop gần prototype và lưu cặp crop–vector của dự án trong
  nhóm bài toán này, không đưa ngưỡng hay số crop cụ thể vào Related Work.

### 2.5.3. Name Grounding, Alias Resolution, and Multimodal Identity Linking

- Phân biệt phát hiện một tên trong câu với xác định tên đó thuộc nhân vật nào.
- Các hướng sử dụng lời gọi tên, đồng tham chiếu, alias, hình ảnh và sự nhất quán
  qua ngữ cảnh để nối hoặc kiểm chứng identity.
- Các trường hợp cần phân tích: cùng họ, hậu tố xưng hô, tên viết sai do OCR,
  người được gọi và người đang nói bị nhầm nhau.

### 2.5.4. Open-Set Matching and Identity Consistency Across Chapters

- Quyết định tái sử dụng identity cũ, tạo identity mới hoặc giữ chưa xác định.
- Phân biệt lỗi tách một người thành nhiều ID với lỗi gộp nhiều người thành một;
  xem xét bằng chứng chống gộp và cơ chế sửa liên kết.
- Khoảng trống cần kiểm tra qua tài liệu: độ bền của identity theo lịch sử,
  khả năng truy nguyên từng lần gộp và kết hợp bank thị giác với registry ngữ nghĩa.

**Sản phẩm nên có:** bảng so sánh các hướng re-identification theo loại đặc trưng,
bộ nhớ mẫu, tín hiệu tên, phạm vi xuyên trang/chapter và cơ chế xử lý chưa chắc chắn.

## 2.6. Speaker and Addressee Attribution

**Câu hỏi dẫn dắt:** Ai đang nói, nói với ai, và làm thế nào kiểm chứng liên kết
đó khi hình ảnh, balloon và nội dung thoại chỉ cung cấp bằng chứng không đầy đủ?

### 2.6.1. Visual and Geometric Speaker Attribution

- Những hướng dựa trên đuôi balloon, vị trí text, khoảng cách tới nhân vật và
  cấu trúc panel để gán người nói.
- Đối chiếu tình huống không có đuôi, người nói ngoài khung, thought balloon,
  narration và nhiều nhân vật cạnh balloon.
- Nêu giới hạn của việc chọn nhân vật gần nhất hoặc coi tail association là
  nhãn chắc chắn; không lặp lại thuật toán dựng mask ở 2.3.

### 2.6.2. Contextual and Multimodal Speaker Attribution

- Những hướng kết hợp nội dung lời thoại, lượt lời, cảnh và identity thị giác.
- So sánh giải quyết từng balloon độc lập với sử dụng đoạn hội thoại hoặc
  ngữ cảnh xuyên panel/trang; xem xét người nói chưa quan sát được.
- Phân tích lợi ích và nguy cơ của suy luận ngữ nghĩa khi bằng chứng hình học
  và nội dung không thống nhất.

### 2.6.3. Addressee Identification and Directed Dialogue Grounding

- Người nghe, nhóm người nghe, độc thoại và các trường hợp không xác định được
  đích giao tiếp; phân biệt addressee với người chỉ được nhắc trong câu.
- Những tín hiệu có thể được khai thác: lời gọi, cấu trúc đối đáp, nội dung và
  bằng chứng không gian; khảo sát cả nghiên cứu hội thoại đa phương khi liên quan.
- Giải thích vì sao đúng speaker chưa đủ cho suy luận quan hệ và xưng hô có hướng.

## 2.7. Dialogue and Character Relationship Modeling

**Câu hỏi dẫn dắt:** Từ identity và các tương tác đã gắn nguồn, làm thế nào xây
được biểu diễn nhân vật và quan hệ phục vụ cách diễn đạt nhất quán?

### 2.7.1. Dialogue Context and Scene-Level Modeling

- Phân đoạn cảnh, nhận diện lượt giao tiếp và tổ chức ngữ cảnh cho hội thoại.
- Đối chiếu cửa sổ câu cố định với đơn vị cảnh hoặc tương tác giữa các nhân vật;
  phân biệt chuỗi đọc với đơn vị ngữ nghĩa hội thoại.
- Phân tích ảnh hưởng của ngữ cảnh thiếu, bị trộn cảnh hoặc attribution sai tới
  việc diễn giải quan hệ. Không dành tiểu mục riêng cho thread pool hay request batching.

### 2.7.2. Character Profiles from Appearance and Dialogue

- Các hướng biểu diễn nhân vật bằng thuộc tính, persona, mô tả hoặc bộ nhớ
  có cấu trúc từ lời thoại, hành vi và thông tin mô tả nhân vật.
- Vai trò của bằng chứng hình ảnh trong ngoại hình/tuổi biểu kiến, so với
  vai trò của lời thoại trong tính cách và vai trò xã hội.
- Phân biệt thuộc tính được xác nhận và giả thuyết; xem xét tổng hợp nhiều ảnh
  thay vì lấy một biểu cảm hoặc một crop làm tính cách cố định.

### 2.7.3. Evidence-Grounded and Evolving Relationship Graphs

- Graph nhân vật và tương tác: node identity, cạnh giao tiếp, quan hệ xã hội,
  nhiều loại quan hệ và sự thay đổi theo thời gian/chapter.
- Phân biệt đồng xuất hiện với giao tiếp trực tiếp và với quan hệ đã được xác
  nhận; bàn về bằng chứng, mâu thuẫn và lịch sử cập nhật.
- Nối profile nhân vật với suy luận quan hệ nhưng không đồng nhất ngoại hình
  với quan hệ gia đình, tình cảm hay quyền hạn.

### 2.7.4. Directed Forms of Address and Vietnamese Sociolinguistic Constraints

- Xưng hô phụ thuộc người nói, người nghe, tuổi/vai vế, mức thân thuộc và tình
  huống; phân biệt danh tính của người được gọi với hình thức gọi người đó.
- Bàn về quan hệ có thể đối xứng nhưng lựa chọn xưng hô hai chiều không nhất
  thiết đối xứng; tách tự xưng và cách gọi đích giao tiếp.
- Liên hệ việc dùng profile/quan hệ làm ngữ cảnh cho đầu ra tiếng Việt và xử lý
  trường hợp bằng chứng chưa đủ. Không mở một survey riêng về machine translation.

**Sản phẩm nên có:** bảng so sánh các hướng modeling theo nguồn đầu vào,
profile, loại graph, hướng cạnh, nguồn bằng chứng và khả năng cập nhật theo thời gian.
Nếu thiếu nghiên cứu trực tiếp trên manga, mở rộng có giải thích sang narrative
understanding, dialogue systems và nghiên cứu xưng hô; không đánh đồng các miền.

## 2.8. Text Removal, Inpainting, and Vietnamese Typesetting

**Câu hỏi dẫn dắt:** Sau khi có nội dung đích, làm thế nào thay chữ mà giữ
được hình ảnh và khả năng đọc trong không gian balloon có giới hạn?

### 2.8.1. Text Mask Generation and Background Reconstruction

- Tạo mask từ text box hoặc vùng chữ; phân biệt xóa nền trắng trong balloon
  với chữ chồng lên đường nét, screentone và hình minh họa.
- So sánh nhóm phương pháp điền nền hình học/truyền thống với learned inpainting
  theo loại nội dung bị che và thông tin ngữ cảnh cần sử dụng.
- Xem xét ranh giới mask, phần chữ còn sót và tổn hại hình vẽ; không lặp lại
  survey detection ở 2.3.

### 2.8.2. Balloon-Constrained Text Layout and Fitting

- Dàn dòng và chọn cỡ chữ trong vùng hình chữ nhật so với bên trong mask balloon.
- Các mục tiêu cần cân bằng: tránh overflow, giữ khả năng đọc, căn lề, khoảng
  cách dòng và sự cân đối của vùng chữ.
- So sánh điều chỉnh hình học với rút gọn câu; nhấn mạnh thay đổi nội dung là
  quyết định khác với tối ưu vị trí chữ và cần bảo toàn ý nghĩa.

### 2.8.3. Vietnamese Typography and End-to-End Visual Quality

- Font có dấu tiếng Việt, khoảng trống cho dấu, ngắt dòng và kiểm tra glyph thiếu.
- Liên hệ đặc trưng trình bày tiếng Việt với balloon gốc; tránh khẳng định văn
  bản luôn dài hơn nếu không có dữ liệu đối chiếu.
- Phân biệt chất lượng pixel/phục dựng với khả năng đọc và mức hoàn chỉnh của
  trang cuối. Đặt các tiêu chí visual review trong bối cảnh đánh giá đầu ra.

## Cách viết để chương có tính tổng hợp nghiên cứu

Mỗi tiểu mục nên có một mạch thống nhất, thường gồm 2–4 đoạn tùy dung lượng:

1. Xác định bài toán và vì sao khó trong manga.
2. Nhóm các công trình theo hướng tiếp cận; so sánh giả định, dữ liệu và đầu ra.
3. Chỉ ra giới hạn được nguồn hoặc thực nghiệm hỗ trợ.
4. Nối ngắn tới nhu cầu của đồ án, chưa trình bày giải pháp triển khai chi tiết.

Không bắt buộc mỗi công trình có một đoạn riêng. Một công trình xử lý nhiều nhiệm
vụ có thể xuất hiện ở nhiều phần, nhưng mỗi lần chỉ phân tích khía cạnh tương ứng.

Kết mỗi phần lớn bằng 2–3 câu tổng hợp. Cuối 2.8 thêm một đoạn chuyển sang
Methodology, tổng hợp nhu cầu kết nối identity bền vững, attribution có hướng,
profile/quan hệ có bằng chứng và đầu ra tiếng Việt. Không tự tuyên bố đây là
"hệ thống đầu tiên" hoặc "chưa có nghiên cứu nào" khi chưa hoàn thành khảo sát.

## Những nội dung dành cho Methodology hoặc Implementation

Không dùng các chi tiết sau làm mục nhỏ của Related Work:

- Ngưỡng min-crop 0.38, bank distance 0.55 hoặc các ngưỡng detection cụ thể.
- Chọn tối đa 10 crop/profile, 16 vector/bank, ngưỡng 8 lần xuất hiện.
- Tên stage `build_identity`, `relationship`, định dạng JSON và đường dẫn output.
- 8/64 request đồng thời, vLLM token budget, cơ chế retry/resume và Runpod L40S.
- Phiên bản package, script, dependency lock và quyết định vận hành của agent.

Related Work có thể thảo luận **nguyên lý** prototype selection, bounded memory,
context selection hoặc uncertainty; các tham số và cơ chế cụ thể của đồ án thuộc
Methodology/Implementation và được kiểm chứng trong Experiments.

## Bảng so sánh và định hướng tài liệu

Đề xuất ba bảng gọn, tránh lặp một bảng tổng hợp rất rộng:

| Vị trí | Cột nên có | Mục đích |
|---|---|---|
| Cuối 2.1 | Dataset; ngôn ngữ/miền; đơn vị dữ liệu; nhãn thị giác; identity; speaker/addressee; phạm vi đánh giá | Cho biết benchmark thực sự đánh giá được gì |
| Cuối 2.5 | Công trình/hướng; biểu diễn ảnh; prototype/bộ nhớ; tín hiệu tên; xuyên trang/chapter; xử lý identity chưa biết | So sánh nền tảng duy trì identity |
| Cuối 2.7 | Công trình/hướng; thoại/ảnh; profile; quan hệ; cạnh có hướng; evidence; cập nhật | So sánh nền tảng graph và xưng hô |

Ở quy mô 14 trang, nên hướng tới khoảng **20–30 nguồn khác nhau**, ưu tiên
công trình gốc, bài benchmark/dataset, nghiên cứu nền tảng và các công trình gần
đây trực tiếp liên quan. Đây là ngân sách viết, không phải hạn ngạch bắt buộc.
Model card/repository có thể xác nhận khả năng và nguồn phần mềm, nhưng không
thay thế bài nghiên cứu khi so sánh chất lượng phương pháp. Tất cả tài liệu cụ
thể và kết luận so sánh cần được kiểm chứng trước khi viết bản Related Work đầy đủ.

Bản 10 trang: thu gọn lịch sử phương pháp và ví dụ, giữ trọng tâm 2.5–2.7.
Bản 18 trang: mở rộng so sánh và phân tích giới hạn có dẫn chứng, không tăng
dung lượng bằng cách thêm tên model hoặc mô tả cấu hình dự án.

## Mục lục rút gọn

```text
2. Related Work
  2.1. Datasets and Benchmark Foundations
    2.1.1. Manga and Comic Datasets: Coverage and Annotation Granularity
    2.1.2. Identity, Speaker, and Dialogue Annotations
    2.1.3. Evaluation Protocols and Cross-Stage Error Propagation
  2.2. Manga Layout Analysis and Reading Order
    2.2.1. Panel Localization and Page Structure Analysis
    2.2.2. Panel and Text Reading Order Estimation
    2.2.3. Layout-Aware Context Construction
  2.3. Manga Object Detection and Segmentation
    2.3.1. Text Region and Speech Balloon Detection
    2.3.2. Character Localization and Instance Segmentation
    2.3.3. Balloon Geometry, Tail Analysis, and Object Association
  2.4. Manga Text Recognition
    2.4.1. Specialized OCR for Stylized and Multilingual Manga Text
    2.4.2. Vision–Language Models for Text Recognition
    2.4.3. OCR Error Handling and Downstream Reliability
  2.5. Character Re-identification and Persistent Identity
    2.5.1. Visual Embeddings for Character Re-identification
    2.5.2. Prototype Selection and Persistent Character Banks
    2.5.3. Name Grounding, Alias Resolution, and Multimodal Identity Linking
    2.5.4. Open-Set Matching and Identity Consistency Across Chapters
  2.6. Speaker and Addressee Attribution
    2.6.1. Visual and Geometric Speaker Attribution
    2.6.2. Contextual and Multimodal Speaker Attribution
    2.6.3. Addressee Identification and Directed Dialogue Grounding
  2.7. Dialogue and Character Relationship Modeling
    2.7.1. Dialogue Context and Scene-Level Modeling
    2.7.2. Character Profiles from Appearance and Dialogue
    2.7.3. Evidence-Grounded and Evolving Relationship Graphs
    2.7.4. Directed Forms of Address and Vietnamese Sociolinguistic Constraints
  2.8. Text Removal, Inpainting, and Vietnamese Typesetting
    2.8.1. Text Mask Generation and Background Reconstruction
    2.8.2. Balloon-Constrained Text Layout and Fitting
    2.8.3. Vietnamese Typography and End-to-End Visual Quality
```
