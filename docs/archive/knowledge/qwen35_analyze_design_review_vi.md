# Rà soát thiết kế Qwen 3.5 27B và batch analyze

Ngày: 2026-10-15.

**Cập nhật sau review:** Người dùng đã yêu cầu tự chốt, triển khai và chuyển sang vLLM. Xem [plan triển khai cùng trạng thái R1–R8](../plans/qwen35-vllm-4090-implementation-vi.md). Các phát hiện bên dưới là căn cứ trước sửa; chúng không khẳng định code hiện tại vẫn giữ nguyên vấn đề, và các phần VRAM/hiệu năng vẫn chờ kiểm chứng riêng.

Đối tượng: [bản giải thích và các quyết định thiết kế hiện tại](analyze_batch_context_qwen35_vi.md), đối chiếu tĩnh với engine v6 và driver `batch_v6.py`. Không chạy test, model, benchmark hoặc truy cập hạ tầng Runpod. Không thay đổi source/config thực thi trong lần rà soát này.

## Kết luận

**Thiết kế có hướng hợp lý nhưng chưa đủ khép kín để triển khai và chạy batch thực.** Có tám vấn đề cần xử lý hoặc chốt rõ, trong đó bốn vấn đề ưu tiên cao liên quan trực tiếp tới thiếu bộ nhớ, lỗi context và thời gian kéo dài.

Không có bằng chứng để khẳng định Qwen 27B Q4 với context 32K chắc chắn không vừa 4090. Cũng chưa có bằng chứng để cam kết nó sẽ vừa hoàn toàn trên GPU và hoàn thành chapter trong 30 phút. Phải giữ hai kết luận này tách biệt.

## R1 — P1: Chưa có điều kiện kiểm chứng đủ mạnh cho 4090 + 32K + không CPU offload

**Vấn đề:** Người dùng đã chốt cả GPU, model, context và không offload, nhưng kiểm tra hiện tại mới chứng minh có dùng GPU, chưa chứng minh toàn bộ trọng số nằm trên GPU.

**Bằng chứng:** [`manage.py`](../../deployment/runpod/manage.py), khoảng dòng 594–606: preflight inference chỉ dùng prompt yêu cầu trả lời “OK”, rồi chấp nhận nếu bất kỳ model trong `/api/ps` có `size_vram > 0`. Điều kiện này không xác nhận đúng model/digest, toàn bộ trọng số trên GPU hoặc khả năng xử lý request analyze dài.

[Trang model chính thức](https://ollama.com/library/qwen3.5%3A27b-q4_K_M) liệt kê trọng số khoảng 17 GB và projector khoảng 931 MB. Đây là kích thước artifact, không phải VRAM đỉnh. [Tài liệu context của Ollama](https://docs.ollama.com/context-length) xác nhận tăng context làm tăng yêu cầu bộ nhớ.

**Hệ quả:** Một preflight “có GPU” có thể đạt trong khi workload vẫn không đáp ứng yêu cầu không offload hoặc không đủ bộ nhớ ở cấu hình thực. Không được dùng kích thước file để cam kết hiệu năng.

**Cần bổ sung thiết kế:** Trong kế hoạch kiểm chứng riêng, xác minh đúng model/digest, vị trí trọng số theo thông tin runner/runtime đáng tin cậy, context hiệu lực và bộ nhớ khi xử lý request đại diện. Phân biệt CPU làm điều phối/tokenization với CPU offload trọng số. Nếu không đạt thì báo không đáp ứng; không tự hạ context, đổi GPU hoặc bật offload.

Giữ cơ chế vòng đời model hiện tại không tự mâu thuẫn với yêu cầu này, nhưng khiến việc xác minh với các model khác và lúc chuyển stage càng cần thiết. Chưa có quyền thay đổi vòng đời model.

## R2 — P1: Chọn batch theo token nhưng hợp đồng tính token chưa thống nhất

**Vấn đề:** Tên chiến lược đã chốt, nhưng cách tính ngân sách chưa đủ nhất quán để bảo đảm các tầng cùng ra một quyết định.

**Bằng chứng:**

- [`resources.py`](../../src/manga_pipeline/runtime/resources.py), khoảng dòng 294–297: `input_estimate` là heuristic bytes/ký tự, không phải tokenizer của Qwen.
- [`ollama.py`](../../src/manga_pipeline/providers/ollama.py), khoảng dòng 83–95: provider chỉ tính schema khi có `MANGA_GPU_COORDINATOR`.
- [`dual_branch.py`](../../src/manga_pipeline/v6/dual_branch.py), khoảng dòng 459: tầng gộp batch có truyền schema khi ước lượng.
- [`ollama.py`](../../src/manga_pipeline/providers/ollama.py), khoảng dòng 123–132: một số retry lỗi nội dung có thể thêm hướng dẫn sửa vào prompt sau bước ước lượng ban đầu; kiểm tra thực tế bằng `prompt_eval_count` diễn ra sau inference.

**Hệ quả:** Cùng request có thể qua bước chia batch nhưng bị từ chối tại provider, hoặc mất thời gian inference rồi mới phát hiện vượt dự phòng. Tăng từ 16K lên 32K tăng sức chứa, không giải quyết sự thiếu thống nhất này.

**Cần bổ sung thiết kế:** Một hợp đồng ngân sách dùng chung cho scene plan, phân tích nhánh, repair và dịch: prompt cuối cùng, schema, dữ liệu, template, dự phòng đầu ra, biên an toàn. Mọi sửa đổi prompt/request phải được kiểm tra lại. Ghi rõ ngưỡng 85% tính trên tổng nào, tránh cộng trùng hoặc bỏ sót dự phòng. Nếu dùng heuristic, ghi mức bất định và so với token thực tế; không gọi nó là phép đếm chính xác.

**Giới hạn nhận định:** Cờ ảnh dự phòng 2.048 token là hạn chế của các đường đa phương thức, nhưng các lời gọi scene/chunk chính trong `dual-branch-v1` đã đối chiếu không truyền ảnh trực tiếp. Chưa thể quy lỗi context của engine này cho số ảnh.

## R3 — P1: Một inference slot chưa thực hiện ưu tiên hoàn thành chapter

**Vấn đề:** Thiết kế muốn có chapter hoàn chỉnh sớm, trong khi giới hạn một request chỉ kiểm soát số lượt inference cùng lúc. Nó không quyết định chapter nào được ưu tiên.

**Bằng chứng:**

- [`pipeline-v6-runpod.json`](../../configs/pipeline-v6-runpod.json), dòng 35: preset hiện còn là `rtx5090`.
- [`resources.py`](../../src/manga_pipeline/runtime/resources.py), khoảng dòng 18–27: các preset mặc định ba story workers. `resolved()` có ngoại lệ cho model 27B, đưa LLM slots về một trên preset RTX nếu không override; vì thế không được suy từ preset thô rằng cấu hình hiệu lực chắc chắn có hai slots.
- [`batch_v6.py`](../../deployment/runpod/batch_v6.py), khoảng dòng 155 và 286–288: driver có thể chạy nhiều truyện qua executor.
- [`resources.py`](../../src/manga_pipeline/runtime/resources.py), khoảng dòng 203–252: GPU lease xếp lượt visual/LLM; khi visual được nhận lượt, coordinator gỡ LLM trước khi chạy.

**Hệ quả:** Ba truyện vẫn có thể xen kẽ request dù LLM concurrency bằng một. Việc chuyển visual/LLM giữa truyện còn có thể phát sinh nạp lại model. Mỗi truyện có tiến độ nhưng chapter đầu tiên vẫn có thể chậm hoàn tất.

**Cần bổ sung thiết kế:** Phân biệt ba mức: số truyện hoạt động, số worker nhánh, số inference slot. Ứng viên đơn giản để đạt yêu cầu là `runtime.preset=rtx4090`, `story_workers=1`, `llm_slots=1`; đây là đề xuất cần duyệt, chưa sửa. Nếu muốn luân phiên truyện sau mỗi chapter thì phải xét lịch theo chapter; một worker chạy hết truyện rồi mới chuyển cũng không phải cùng chính sách.

Giữ vòng đời model như đã chốt; ưu tiên sửa lịch công việc trước khi đề xuất thay đổi load/unload. Hai worker nhánh trong một chapter có thể được giữ, nhưng không được mô tả như hai inference chạy đồng thời.

## R4 — P1: Quy tắc timeout/retry chưa nối được với trạng thái thực thi hiện tại

**Vấn đề:** “600 giây, retry nguyên request tối đa một lần sau khi lượt cũ kết thúc” còn thiếu cách xác định kết thúc, thời gian chờ và cách phục hồi khi đã khóa hàng đợi.

**Bằng chứng:**

- [`ollama.py`](../../src/manga_pipeline/providers/ollama.py), khoảng dòng 25–30: 600 giây được dùng làm timeout của HTTP client, không phải deadline bao trùm toàn bộ chapter/hàng đợi.
- [`resources.py`](../../src/manga_pipeline/runtime/resources.py), dòng 28: `gate_timeout` mặc định 3.600 giây, riêng với thời gian đợi lượt.
- [`ollama.py`](../../src/manga_pipeline/providers/ollama.py), khoảng dòng 44–70: lỗi `OSError` trong request sẽ quarantine coordinator; `infer_once` đặt semantic retry bằng 0. Hai resource attempts trong `_chat` dành cho `ResourceExhausted`, không tự bảo đảm một lần retry timeout như thiết kế.
- [`resources.py`](../../src/manga_pipeline/runtime/resources.py), khoảng dòng 166–176 và 223–227: quarantine đặt fault; lượt xin GPU tiếp theo bị từ chối khi fault còn tồn tại.

**Hệ quả:** Có thể không retry được ở đường analyze như người dùng kỳ vọng; hoặc thời gian đợi hàng đợi cộng xử lý vượt xa con số “10 phút”. Nếu chỉ thử gọi lại mà chưa xử lý fault đúng cách, retry không thành một lượt inference hợp lệ.

**Cần bổ sung thiết kế:** Mô tả trạng thái `queued → running → completed / timed_out_uncertain → paused`. Định nghĩa bằng chứng lượt cũ đã kết thúc, người/cơ chế được phép xóa fault, trần chờ và cách ghi attempt. Nếu không có bằng chứng thì giữ paused theo Q30; không tự restart Ollama. Đếm queue, load và inference riêng. Quy tắc này phải áp dụng cả khi không có coordinator, không chỉ trong một chế độ chạy.

## R5 — P2: Chưa chốt ngân sách đầu ra và tổng chi phí repair

**Vấn đề:** Batch nhánh tối đa ba đã duyệt, nhưng 2.048 token đầu ra chưa được chốt cuối. Scene plan vẫn có thể có tám mục; sửa kết quả có thể tạo thêm nhiều request.

**Bằng chứng:** [`dual_branch.py`](../../src/manga_pipeline/v6/dual_branch.py): `scene_plan` có cap riêng khoảng dòng 140; `infer_chunk` khoảng dòng 311–362 có repair theo target. Provider từ chối phản hồi bị cắt (`done_reason == length`), khoảng dòng 129. Runtime có `recovery_attempts=3`; giá trị này không đồng nghĩa retry timeout đúng một lần.

**Hệ quả:** Một request có thể vừa input nhưng không đủ output. Với nhiều targets lỗi, các repair đơn lẻ làm tổng thời gian tăng. “Một retry” không phải giới hạn tổng lời gọi cho một nhóm.

**Cần bổ sung thiết kế:** Chốt output reserve và giới hạn batch theo từng tác vụ; phân biệt cắt đầu ra, lỗi schema/nội dung, context, OOM và timeout. Giới hạn riêng số repair/target và số lần chia lại nhóm, đồng thời ghi tổng lời gọi/thời gian. Không tăng output reserve tự động nếu người dùng chưa duyệt. Đầu ra thiếu không được coi là thành công.

Giữ cả lượt xuôi/ngược là phương án đang bàn, chưa có benchmark chứng minh lợi ích/tổng thời gian trên Qwen mới. Không tự bỏ một lượt để đạt mục tiêu thời gian.

## R6 — P2: Rút ngữ cảnh chưa có hợp đồng bảo toàn bằng chứng

**Vấn đề:** Cho phép giảm cả dữ liệu trực tiếp nhưng giữ câu hiện tại/crop tối thiểu chưa đủ xác định dữ liệu nào được bỏ và kết quả phải ghi nhận sự suy giảm đó thế nào.

**Hệ quả:** Có thể bỏ chính dữ liệu xác định người nói hoặc quan hệ xưng hô, trong khi kết quả vẫn đủ trường và bị hiểu là có độ tin cậy như bình thường. Giữ nguyên một ID không đồng nghĩa model còn được cung cấp bằng chứng cho ID đó.

**Cần bổ sung thiết kế:** Danh sách trường bắt buộc/được rút, thứ tự và mức giảm; bảo toàn ID/anchor/schema; cập nhật đồng bộ các tập bằng chứng được cung cấp và validator; ghi dấu vết mức giảm. Thay đổi ngân sách không được biến một lỗi hệ thống thành semantic `unknown` hợp lệ. Nếu input tối thiểu không vừa thì ghi lỗi theo giới hạn đã chốt.

Với đường analyze văn bản hiện tại, ưu tiên rút phải được mô tả bằng các trường thật (`history`, `context`, participants, names, anchors…), không chỉ bằng “giảm ảnh”. Không tự thêm ảnh vào request hiện không có ảnh.

## R7 — P2: Cấu hình model chưa có bản chốt tái lập đầy đủ

**Vấn đề:** Đã chọn tag và tắt thinking nhưng chưa chốt sampling hiệu lực, digest, cùng phạm vi task/config được áp dụng.

**Bằng chứng:** [`ollama.py`](../../src/manga_pipeline/providers/ollama.py) hiện override `temperature=0`, `think=false`, nhưng không đặt rõ mọi tham số sampling. [Trang model](https://ollama.com/library/qwen3.5%3A27b-q4_K_M) công bố default params có `presence_penalty=1.5`, `top_k=20`, `top_p=0.95`, `temperature=1`; không nên mặc định mọi tham số khác bằng 0 khi chỉ override temperature. [`manage.py`](../../deployment/runpod/manage.py) đã pin phiên bản Ollama, nhưng phiên bản được pin vẫn cần được ghi cùng digest và effective config của kế hoạch.

**Cần bổ sung thiết kế:** Ghi sampling hiệu lực có chủ đích cho analyze/dịch và quyết định giữ hay override từng tham số có tác động. Chốt tag/digest được kiểm chứng và prompt/schema version. Liệt kê các task Ollama chịu thay đổi, kể cả sửa/shorten, tránh cấu hình danh nghĩa khác cấu hình provider thực dùng. Chưa thay sampling trước khi người dùng trả lời Q29.

**Resume:** Driver v6 thực sự là [`batch_v6.py`](../../deployment/runpod/batch_v6.py). Khoảng dòng 218–220, nó so config lưu với config hiện tại và từ chối resume khác cấu hình. Vì thế không kết luận rằng đổi model mà giữ context sẽ âm thầm dùng lại analysis cũ ở driver này. Cần lập revision/reanalyze rõ ràng, giữ checkpoints cũ. Logic đơn giản hơn trong `batch.py` thuộc driver khác, không nên dùng nó làm căn cứ đánh giá sai v6.

## R8 — P2: Mục tiêu 30 phút/chapter chưa có tập đại diện và điều kiện đo

**Vấn đề:** Chưa chốt chapter điển hình có bao nhiêu trang, targets, cảnh và nhân vật; chưa phân định thời gian nào nằm trong mục tiêu. Một chapter ít thoại và một chapter nhiều caption không có cùng lượng công việc.

**Hệ quả:** Khó biết cấu hình đạt yêu cầu hay chỉ đạt trên mẫu nhẹ. Chọn batch dựa vào thời gian đơn lẻ có thể che mất repair, thiếu coverage hoặc giảm bằng chứng quá mức.

**Cần bổ sung vào kế hoạch Runpod riêng:** Chọn dữ liệu đại diện; đo từ mốc bắt đầu rõ ràng tới chapter đủ artifact/coverage, đồng thời tách startup, visual, scene plan, hai hướng nhánh, dịch, typeset, review/validation và truyền dữ liệu. Ghi số request, queue/load/eval time, repair, retry, cắt đầu ra, mức rút ngữ cảnh và lỗi. Giữ 30 phút là mục tiêu đo, không dùng làm lý do tự bỏ dữ liệu hoặc lượt phân tích.

Chưa đọc log/journal của lần chạy gần ba giờ nên chưa kết luận nguyên nhân lần đó. Kiểm tra log lưu sẵn sẽ là bước điều tra độc lập; không cần chạy lại model để có bằng chứng này.

## Những phần hợp lý nên giữ

- Một inference slot, batch nhánh tối đa ba, cùng ranh giới mạch/caption.
- Giảm số targets trước khi giảm bằng chứng của target riêng lẻ.
- Tách input budget và output reserve; từ chối JSON bị cắt.
- Tạm dừng khi timeout mà trạng thái server chưa rõ; không gửi việc trùng.
- Không offload, không đổi GPU hay vòng đời model ngoài quyết định đã duyệt.
- Giữ đủ trường đầu ra, checkpoints, provenance và lỗi thật.
- Lập kế hoạch kiểm chứng riêng; không dùng yêu cầu review này làm quyền triển khai hoặc chạy Runpod.

## Thứ tự hoàn thiện đề xuất

1. Chốt hợp đồng ngân sách, output và các loại retry/repair.
2. Chốt lịch chapter và config hiệu lực thay vì chỉ tên preset.
3. Hoàn thiện chính sách rút dữ liệu và trạng thái timeout/phục hồi.
4. Lập kế hoạch kiểm chứng 4090/32K/không offload trên tập đại diện để người dùng duyệt riêng.

Các bước trên là đề xuất sau rà soát. Không có thay đổi triển khai nào đã được thực hiện hoặc tự coi là được duyệt.
