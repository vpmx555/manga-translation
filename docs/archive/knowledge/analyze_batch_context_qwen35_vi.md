# Batch analyze, context và cấu hình Qwen 3.5 27B

Ngày đối chiếu mã: 2026-10-15. Tài liệu dành cho pipeline v6, đặc biệt engine `dual-branch-v1`.

**Cập nhật cùng ngày:** Người dùng đã đổi engine đích sang **vLLM** và giao tự chốt/triển khai các phần còn mở. Cấu hình và thay đổi hiện hành nằm trong [plan vLLM/4090](../plans/qwen35-vllm-4090-implementation-vi.md). Các đoạn mô tả “hiện tại” bên dưới ghi nhận mã trước lượt triển khai này; bảng quyết định tại mục 11 là lịch sử thảo luận, không dùng để xác định backend mới. Kiến thức về batch, hai lượt phân tích và phân biệt input/output vẫn áp dụng.

Tài liệu này giải thích hành vi hiện tại và các quyết định thiết kế trong buổi thảo luận. Nó không xác nhận các đề xuất đã được triển khai, không phải báo cáo benchmark và không cấp quyền chạy Runpod.

## 1. Batch analyze là gì?

Batch là nhóm mục tiêu được yêu cầu phân tích trong **một request** gửi model. Mỗi mục có ID riêng và cần kết quả riêng. Một mục không mặc định là một trang, một ảnh hay một chapter.

Ví dụ sáu mục thoại U1–U6, cùng mạch hội thoại, được xử lý với giới hạn batch bằng 3:

```text
Request 1: targets = [U1, U2, U3] → kết luận cho từng ID
Request 2: targets = [U4, U5, U6] → kết luận cho từng ID
```

Đây là ví dụ đơn giản; ranh giới mạch hội thoại, liên kết caption, ngân sách context và phục hồi lỗi có thể khiến batch nhỏ hơn.

### Targets khác context

`targets` là các mục model phải trả lời trong lượt này. `context` cùng các trường bổ sung là dữ liệu hỗ trợ suy luận. Request có ba targets không có nghĩa chỉ chứa ba câu.

Request có thể chứa:

- Prompt hướng dẫn và chính sách phân tích.
- Schema JSON quy định hình dạng kết quả.
- Văn bản, ID, anchor và thông tin nguồn của targets.
- Câu trước/sau, lịch sử kết quả, nhân vật trong cảnh và tên/biệt danh.
- Các tham chiếu bằng chứng, ứng viên được nhắc tới, kết quả lượt xuôi khi chạy lượt ngược.

Vì thế, batch 1 vẫn có thể quá dài nếu phần thông tin hỗ trợ lớn. Ngược lại, ba câu ngắn dùng chung nhiều ngữ cảnh có thể vừa dễ dàng.

## 2. Các khái niệm cần phân biệt

| Khái niệm | Ý nghĩa | Không suy ra được điều gì? |
| --- | --- | --- |
| Batch size | Số mục tiêu tối đa trong một request | Không phải số request đồng thời |
| Concurrency | Số request inference được xử lý cùng lúc | Không phải số targets trong request |
| Worker | Luồng/process chuẩn bị hoặc thực hiện công việc | Nhiều worker không bảo đảm GPU chạy nhiều inference |
| Context window / `num_ctx` | Sức chứa token của một lượt suy luận | Không phải dung lượng VRAM và không phải giới hạn riêng cho đầu vào |
| Output limit / `num_predict` | Trần token model được sinh cho cả request | Không cấp từng ấy token cho mỗi target |
| Chapter batch | Nhóm chapter trong kế hoạch chạy | Khác batch analyze bên trong chapter |
| Retry | Gửi lại sau lỗi theo quy tắc | Khác chia nhỏ batch hoặc sửa một target |

Batch 3, concurrency 1 nghĩa là mỗi lượt model phân tích tối đa ba mục trong một request; request kế tiếp phải đợi lượt xử lý hiện tại kết thúc.

## 3. Hai cách gộp trong analyze hiện tại

Mã chính: [`src/manga_pipeline/v6/dual_branch.py`](../../src/manga_pipeline/v6/dual_branch.py).

### 3.1. Lập kế hoạch cảnh và mạch hội thoại — `scene_plan`

Bước này dùng các mục thoại để suy ra quan hệ như bắt đầu, tiếp nối, trả lời, mở rộng, chuyển cảnh, nhánh phụ và quay lại mạch chính.

- Giới hạn ban đầu là `min(8, runtime.analyze_max_batch)`.
- Bộ đếm giới hạn dựa trên mục không phải filler. Các mục filler được giữ trong quá trình duyệt nhưng không đồng nghĩa mỗi mục đều trở thành target phân loại chủ đề.
- Request có targets, một số lượt chính/phụ trước, tóm tắt mạch chính và thông tin mạch hiện tại.
- Mỗi target có quan hệ, lý do, tóm tắt và tham chiếu bằng chứng theo schema.
- Code có kiểm tra ngân sách và cơ chế giảm nhóm khi đáp ứng điều kiện phục hồi.

**`v6.analyze_batch_size = 3` không tự đặt batch của bước này thành 3.** Hai giới hạn phải được xem riêng khi lập cấu hình.

### 3.2. Phân tích các nhánh — `run_branch`

Giới hạn trên là:

```text
min(v6.analyze_batch_size, runtime.analyze_max_batch)
```

Mặc định `analyze_batch_size` đang là 3. Code lấy nhóm từ chuỗi mục của nhánh, rồi cắt ở ranh giới `thread_id`. Nhánh caption còn phải tuân thủ giới hạn khoảng cách liên kết.

Các trường request đáng chú ý:

| Trường | Vai trò |
| --- | --- |
| `targets` | Mục phải có kết luận trong lượt này |
| `context` | Văn bản lân cận thuộc cùng mạch |
| `history` | Một số kết quả trước trong mạch |
| `source_anchors` | Anchor nguồn hỗ trợ liên kết |
| `scene_participants` | Ứng viên nhân vật và bằng chứng liên quan |
| `known_names` | Tên và biệt danh của nhân vật liên quan |
| `mentions` | Các lượt nhắc tới gắn với targets |
| `forward_results` | Kết quả lượt xuôi dùng khi chạy lượt ngược |

Batch thực tế có thể nhỏ hơn 3 vì hết dữ liệu, đổi mạch, giới hạn caption, kiểm tra context hoặc phục hồi lỗi.

### 3.3. Có lượt xuôi và lượt ngược

Mỗi nhánh được duyệt theo `forward` và `backward`. Lượt ngược nhận thêm kết quả lượt xuôi cho các mục của batch. Chúng không phải hai request đồng thời cho cùng nhóm.

Với sáu mục cùng mạch, không cache, không lỗi và luôn gộp đủ:

| Giới hạn batch | Request lượt xuôi | Request lượt ngược | Tổng request phân tích nhánh |
| --- | ---: | ---: | ---: |
| 1 | 6 | 6 | 12 |
| 2 | 3 | 3 | 6 |
| 3 | 2 | 2 | 4 |

Đây không phải tổng số request của toàn bộ analyze. Còn bước lập kế hoạch cảnh, các nhánh/cảnh khác và khả năng sửa kết quả hoặc retry. Checkpoint/cache hợp lệ cũng có thể tránh gọi lại model.

### 3.4. Nhánh hiện tại không phải batch ảnh trực tiếp

`_model_call` có tham số ảnh tùy chọn, nhưng các lời gọi lập kế hoạch cảnh và phân tích chunk đã đối chiếu ở engine này không truyền ảnh trực tiếp. Chúng truyền dữ liệu có cấu trúc và tham chiếu bằng chứng.

Không được suy luận rằng “batch 3” nghĩa là ba ảnh, hay giảm độ phân giải ảnh chắc chắn giúp request của nhánh này. Cấu hình ảnh có thể ảnh hưởng các đường gọi khác; phải truy vết từng đường gọi trước khi sửa.

Quyết định giữ ít nhất một crop khi giảm đầu vào chỉ áp dụng cho request thực sự dùng ảnh. Nó không phải yêu cầu tự thêm ảnh vào engine văn bản hiện tại.

## 4. Token, context và ngân sách đầu ra

Token là đơn vị model xử lý; không tương ứng cố định với một từ hay một ký tự. Văn bản tiếng Việt, JSON, ID và tên riêng có cách phân tách khác nhau. Byte hoặc ký tự chỉ là cơ sở ước lượng, không phải phép đếm token chính xác.

Đặt `num_ctx = 32768` là đặt sức chứa 32K token. Không có nghĩa được dùng toàn bộ 32K cho đầu vào rồi sinh thêm ngoài giới hạn.

Ngân sách khái niệm:

```text
prompt + dữ liệu + schema + chat template + token ảnh (nếu có)
+ dự phòng đầu ra + biên an toàn
<= context đã cấu hình
```

Với đề xuất ngưỡng đóng gói 85%, tổng ngân sách được ước lượng phải không vượt khoảng 27.853 token khi context là 32.768. Con số này đã bao gồm dự phòng đầu ra và biên an toàn theo cách tính thống nhất; không phải cho phép đầu vào chiếm riêng 85% rồi cộng thêm đầu ra.

Ngưỡng 85% là chính sách tạo khoảng dự phòng, không bảo đảm model vừa VRAM hay hoàn toàn không gặp lỗi context.

### Ước lượng hiện tại và các điểm cần rà soát

Trong [`runtime/resources.py`](../../src/manga_pipeline/runtime/resources.py), `input_estimate` dùng heuristic gần dạng:

```text
bytes(JSON dữ liệu) / 2
+ ký tự prompt / 2
+ bytes(schema JSON) / 2 nếu được truyền
+ 2048 nếu cờ images bật
+ num_predict
+ 512
```

Các hạn chế:

- Đây không phải tokenizer chính xác của Qwen.
- Một cờ có ảnh không biểu diễn được đầy đủ số ảnh/kích thước ảnh của các đường gọi đa phương thức.
- Chi phí template và token đặc biệt cần dự phòng.
- Provider hiện chỉ truyền schema vào ước lượng khi có biến `MANGA_GPU_COORDINATOR`; điều này có thể tạo cách tính khác nhau giữa chế độ chạy.
- Provider còn kiểm tra `prompt_eval_count` sau phản hồi. Kiểm tra muộn có thể phát hiện vượt dự phòng nhưng thời gian inference đã phát sinh.

Các điểm trên là rủi ro từ rà soát tĩnh, chưa chứng minh nguyên nhân của lần chạy ba truyện trước đây.

### `num_predict` là của cả batch

Giả sử giới hạn đầu ra là 2.048 token và request có ba targets. Cả JSON kết quả, khóa, ID, lý do và bằng chứng của ba targets cùng dùng giới hạn đó. Không có 2.048 token riêng cho từng target; model cũng không chia đều cứng thành ba phần.

Context 32K không tự tăng `num_predict`. JSON bị cắt vì hết đầu ra là vấn đề khác với prompt vượt context.

Provider hiện dùng ít nhất 2.048 token cho tác vụ analyze và dịch dù config chung đang ghi 768. Khi cấu hình lại phải xét giá trị hiệu lực, không chỉ nhìn file JSON.

## 5. Batch ảnh hưởng tốc độ và chất lượng thế nào?

### Khi batch lớn hơn có thể hữu ích

- Giảm số lần gửi prompt/schema và ngữ cảnh chung.
- Giảm số lượt gọi cùng một model.
- Cho model nhìn các targets liên quan trong cùng request.

Không được mặc định lợi ích tăng tuyến tính. Hai request chứa ba mục không nhất thiết nhanh gấp ba sáu request một mục.

### Khi batch lớn hơn có thể bất lợi

- Input và schema lớn hơn; thời gian đọc prompt có thể tăng.
- Model phải sinh nhiều kết quả trong cùng trần đầu ra.
- Có thêm cơ hội nhầm ID, người nói hoặc bằng chứng giữa các mục.
- Một lỗi request có thể khiến phải làm lại nhiều mục.
- Ngữ cảnh khác nhau giữa các targets khiến lợi ích dùng chung giảm.

### Vì sao không đặt batch 1 cho mọi thứ?

Batch 1 dễ giới hạn kích thước và khoanh vùng lỗi, nhưng lặp nhiều prompt/ngữ cảnh, làm tăng lượt gọi. Khi còn hai hướng phân tích, số request có thể tăng đáng kể. Nếu phần ngữ cảnh nền đã quá dài, batch 1 vẫn không giải quyết được nguyên nhân.

### Vì sao không tăng batch chỉ vì context tăng lên 32K?

Context lớn hơn chỉ tăng sức chứa, không tăng tốc độ sinh token hay trần đầu ra. Cần xét cả dung lượng kết quả, ranh giới mạch, độ chính xác và bộ nhớ GPU. Quyết định đã duyệt là giữ tối đa 3 mục cho phân tích nhánh, không tự tăng lên 8.

## 6. Concurrency, worker và chạy ba truyện

Config Runpod hiện có hai worker nhánh analyze; cấu hình khởi động Ollama mặc định `OLLAMA_NUM_PARALLEL=1`. Hai worker có thể chuẩn bị/gửi việc, nhưng không chứng minh hai inference cùng tiến triển trên GPU.

Ba pipeline độc lập còn có thể tranh GPU với các model visual, CPU, RAM và lưu trữ. Tăng concurrency có thể tăng thông lượng trong một số điều kiện, nhưng cũng tăng áp lực bộ nhớ và thời gian chờ. Không có bảo đảm chạy ba truyện cùng lúc hoàn thành sớm hơn.

Hướng đã thống nhất là một hàng đợi inference dùng chung, ưu tiên hoàn thành chapter để có đầu ra dùng được sớm. Có thể luân phiên truyện tại ranh giới chapter. Đây là thiết kế mục tiêu, không phải tuyên bố bộ điều phối hiện tại đã thực hiện đầy đủ.

## 7. VRAM, lượng tử hóa và context 32K

4-bit giảm bộ nhớ trọng số so với các định dạng chính xác cao hơn, nhưng không có nghĩa toàn bộ bộ nhớ runtime chỉ bằng số tham số nhân bốn bit.

Ngân sách GPU còn có cache trạng thái suy luận, buffers, phần phụ trợ đa phương thức nếu sử dụng, CUDA/runtime và model khác đang cư trú. Kích thước file model không phải mức VRAM đỉnh của request.

Context lớn thường làm tăng nhu cầu bộ nhớ dành cho suy luận; mức tăng cụ thể phụ thuộc kiến trúc model, engine và kiểu cache. Không dùng một công thức cache của model khác để cam kết Qwen vừa 4090.

Ràng buộc đã chốt:

- Chỉ RTX 4090; không tự chuyển 5090/A40.
- Không CPU offload.
- Context cố định 32.768 token.
- Giữ cơ chế quản lý model hiện tại.

Các ràng buộc này có thể không cùng đạt trong cấu hình thực tế. Nếu kiểm chứng không đạt, phải báo và thảo luận lại; không âm thầm đổi model, context, GPU hoặc cho chạy trên CPU.

## 8. Khi thiếu context: chia batch trước, rút dữ liệu sau

Trình tự thiết kế đã thống nhất:

1. Tạo request đầy đủ theo nhóm hợp lệ.
2. Kiểm tra ngân sách thống nhất, gồm cả schema và dự phòng đầu ra.
3. Giảm số targets nếu nhóm không vừa.
4. Nếu một target vẫn không vừa, bỏ thông tin lặp/không liên quan và giảm lịch sử xa trước.
5. Khi vẫn thiếu, được giảm thông tin trực tiếp theo quyền người dùng, nhưng giữ lời thoại đang xử lý; request có ảnh phải giữ ít nhất một crop liên quan.
6. Nếu bộ tối thiểu vẫn không vừa, ghi lỗi thay vì bỏ luôn phần bắt buộc.

Đây là hướng cần thiết kế/triển khai, không phải khẳng định code hiện có đã thực hiện mọi bước rút dữ liệu. Mã hiện tại có các nhánh giảm batch và phục hồi riêng.

Rút dữ liệu phải giữ tính nhất quán: không giữ một tham chiếu bằng chứng trong kết quả trong khi đã loại nội dung cần thiết để hiểu nó. Cần lưu dấu vết mục nào bị giảm và mức giảm để đánh giá chất lượng sau này.

## 9. Timeout, retry, repair và checkpoint

| Tình huống | Ý nghĩa | Hướng xử lý đã thảo luận |
| --- | --- | --- |
| Vượt ngân sách trước gọi | Ước lượng request không vừa | Chia nhỏ rồi rút dữ liệu theo giới hạn |
| Hết token đầu ra | Model có thể chưa hoàn tất JSON | Không nhận JSON thiếu là hoàn thành; cần chính sách chia nhỏ/đầu ra |
| Timeout client | Client hết thời gian đợi | Không tự coi server đã dừng |
| Retry | Thực hiện lại request sau lỗi | Tối đa một lần cho timeout theo quyết định |
| Repair | Sửa kết quả không hợp lệ của mục cụ thể | Là lời gọi bổ sung, phải được tính vào thống kê |
| Checkpoint/cache | Tái sử dụng công việc hợp lệ đã lưu | Không sửa manifest để giả lập hoàn thành |

Timeout đã chốt là 600 giây/request. Sau timeout, chỉ thử lại nguyên request khi xác nhận lượt cũ đã kết thúc. Nếu không xác nhận được, tạm dừng hàng đợi inference và ghi trạng thái chưa rõ; không gửi request trùng.

Một lượt 600 giây cộng một retry 600 giây có thể mất khoảng 20 phút, chưa tính thời gian chờ hàng đợi và phần xử lý khác. Vì thế mục tiêu 30 phút/chapter có thể không đạt khi nhiều request timeout.

Mã hiện có cả retry provider, giảm batch theo `recovery_attempts` và đường sửa từng target. Không nên cộng tất cả thành một khái niệm “retry một lần”. Khi triển khai cần định nghĩa trần từng loại và số lượt tổng để tránh khuếch đại công việc ngoài dự kiến.

## 10. Temperature và thinking

Temperature điều chỉnh độ ngẫu nhiên khi chọn token. Nó không tăng context, không bật thinking và không phải nút tăng tốc đáng tin cậy.

- Temperature thấp thường giúp kết quả ổn định hơn, nhưng model vẫn có thể sai nhất quán.
- Temperature cao hơn tạo nhiều cách diễn đạt, có thể hữu ích cho dịch nhưng tăng biến động.
- Schema ràng buộc cấu trúc; không chứng minh nội dung đúng.
- Temperature 0 không bảo đảm kết quả giống tuyệt đối giữa mọi phần cứng/runtime.

Code provider hiện đặt `temperature=0`, `think=false`. Tắt thinking đã được duyệt. Lựa chọn giữ temperature 0 cho cấu hình mới vẫn đang chờ chốt, không được ghi như quyết định đã duyệt.

## 11. Quyết định đã chốt và phần còn mở

| Hạng mục | Trạng thái |
| --- | --- |
| Model | Qwen 3.5 27B 4-bit cho toàn bộ bước gọi Ollama |
| Engine | Giữ Ollama |
| Tag model ứng viên | `qwen3.5:27b-q4_K_M`; có trên thư viện Ollama |
| GPU | Chỉ RTX 4090; không chuyển loại GPU |
| CPU offload | Không cho phép |
| Context | Cố định 32.768 token; chưa chứng minh vừa bộ nhớ |
| Thinking | Tắt |
| Inference concurrency | Một request tại một thời điểm |
| Thứ tự công việc | Ưu tiên hoàn thành từng chapter |
| Batch phân tích nhánh | Tối đa 3 mục, giảm theo ngân sách — Q32 đã duyệt |
| Batch lập kế hoạch cảnh | Chưa chốt giá trị mới; hiện có giới hạn riêng |
| Giải thích analyze | Giữ đủ trường, viết ngắn và bỏ lặp |
| Rút ngữ cảnh | Giảm lịch sử xa trước; được giảm dữ liệu trực tiếp trong giới hạn tối thiểu |
| Timeout / retry timeout | 600 giây; tối đa một lần, nguyên request sau khi lượt cũ kết thúc |
| Không rõ trạng thái lượt cũ | Tạm dừng hàng đợi, không gửi trùng |
| Vòng đời model | Giữ cơ chế hiện tại |
| Mục tiêu thời gian | 30 phút tới chapter đầu tiên; mục tiêu kiểm chứng, không phải cam kết |
| Ngân sách đầu ra | Chưa chốt cuối; 2.048 token/request là ứng viên đang bàn |
| Temperature | Chưa chốt; code hiện dùng 0 |
| Batch dịch / ngưỡng 85% | Chưa chốt toàn bộ gói config đề xuất |
| Kiểm chứng | Lập kế hoạch Runpod riêng để người dùng duyệt sau; chưa được phép chạy |

Tên `qwen3.8:27b-q4_K_M` đang xuất hiện trong `configs/pipeline-v6-runpod.json` tại thời điểm rà soát; tài liệu này không sửa file đó. Không coi tên này là bằng chứng về model đã được tải hoặc chạy trong phiên cũ.

Việc người dùng yêu cầu tài liệu này không kết thúc phỏng vấn thiết kế và không phải xác nhận triển khai toàn bộ phương án.

## 12. Kế hoạch kiểm chứng cần chứng minh điều gì?

Phần này là nội dung cần đưa vào kế hoạch riêng, không phải lệnh thực thi hay quyền mở tài nguyên.

1. Ghi đúng tag/digest model, phiên bản Ollama và effective config.
2. Xác nhận model thực sự chạy hoàn toàn trên GPU, đủ bộ nhớ cả lúc nạp và lúc request đại diện xử lý.
3. Dùng cả nhóm thoại ngắn và mục có nhiều nhân vật/lịch sử; không chỉ thử prompt đơn giản.
4. Đo số targets/batch thực tế, token đầu vào/đầu ra, thời gian chờ, đọc prompt, sinh kết quả và nạp model.
5. Đếm số lần giảm batch, rút ngữ cảnh, cắt đầu ra, retry, repair và lỗi còn lại.
6. Đo thời gian tới chapter hoàn chỉnh đầu tiên, tổng thời gian và chất lượng người nói/ngữ cảnh/dịch.
7. Giữ checkpoint, provenance, visualization và review theo hợp đồng pipeline; không dùng review Markdown làm cổng phê duyệt stage.

Không suy ra nguyên nhân chỉ từ GPU utilization thấp, PID còn sống hay log vẫn cập nhật. Cần tiến độ thực tế như target đã hoàn thành hoặc checkpoint mới.

## 13. Nguồn đối chiếu

- [`dual_branch.py`](../../src/manga_pipeline/v6/dual_branch.py): lập kế hoạch cảnh, gộp targets, ranh giới mạch, hai hướng, phục hồi và checkpoint.
- [`v6/config.py`](../../src/manga_pipeline/v6/config.py): mặc định cấu hình v6.
- [`runtime/resources.py`](../../src/manga_pipeline/runtime/resources.py): runtime limits, ước lượng ngân sách và điều phối tài nguyên.
- [`providers/ollama.py`](../../src/manga_pipeline/providers/ollama.py): request hiệu lực, đầu ra, sampling, kiểm tra và retry.
- [`pipeline-v6-runpod.json`](../../configs/pipeline-v6-runpod.json): cấu hình Runpod tại thời điểm rà soát.
- [`deployment/runpod/manage.py`](../../deployment/runpod/manage.py): môi trường Ollama và quản lý runtime.
- [Tag Qwen 3.5 27B Q4_K_M trên Ollama](https://ollama.com/library/qwen3.5%3A27b-q4_K_M): xác minh tên bản model, không phải bằng chứng hiệu năng trên 4090.
- [`runpod_agent_fast_policy_vi.md`](../news/runpod_agent_fast_policy_vi.md): chính sách dự án; mọi kế hoạch thực thi sau này phải đọc lại và tuân thủ quyền người dùng hiện hành.
