> Historical: describes the pre-refactor pipeline. Current usage is in ../news/usage.md.

# Kết quả triển khai bước 1–2

Đã triển khai baseline và công cụ đánh giá theo thiết kế đã xác nhận. Chưa chọn embedding/ngưỡng production.

## Đã thay đổi

- `src/main.py`: tiếp tục xuất transcript cũ; bổ sung raw/normalized JSON, tùy chọn content overrides và logical merges, guard tránh ghi đè JSON và tùy chọn bỏ visualizations. Pin MAGI revision theo fingerprint đang dùng.
- `src/character_assignment.py`: xuất bank IDs độc lập với tên hiển thị, bao gồm cập nhật ID khi pending được promote trong cùng chapter.
- `src/dialogue_data.py`: lossless export, adapter JSON hiện có, normalization thận trọng, content overrides, audit text bị lọc, logical merging và giữ từng source box cho inpaint. Helper context giữ các lượt thoại gần nhau qua ranh giới scene chưa chắc.
- `src/scene_clustering.py`: ba embedding backend có prompt/revision/cache riêng; chống tokenizer truncation; adjacent average-linkage với cohesion guard; scene liên tục và ranh giới cần duyệt.
- `src/scene_evaluation.py`: nhãn tham chiếu độc lập có fingerprint, xác nhận nhãn, masking gaps ambiguous, boundary metrics/WindowDiff, manifest chống trùng dữ liệu và chọn ngưỡng trên development.
- `src/dialogue_pipeline.py`: CLI `import-utterances`, `normalize`, `scenes`, `review`, `confirm-labels`, `benchmark` chạy độc lập với MAGI/Ollama.
- `requirements-scenes.txt`: dependency tùy chọn cho môi trường embedding riêng, tránh nâng Transformers trong venv MAGI.
- README, thiết kế và `.gitignore` được bổ sung để mô tả workflow/cache/output.

## Kiểm tra đã chạy

`python -B -m unittest discover -s tests -p test_*.py`: **35 tests passed**.

Bao gồm các test character bank có sẵn; tests mới kiểm tra mapping speaker, giữ narration/thought, audit noise, bảo toàn box khi gộp, context lân cận, scene liên tục/cross-page, chống chaining, nhãn stale/chưa duyệt, ambiguity masking, cache isolation, input truncation và chống leakage.

Đã chạy smoke test entry point MAGI với prediction giả lập để kiểm tra cả raw/normalized export và transcript tương thích; không chạy lại model MAGI trên ảnh người dùng.
`dialogue_pipeline.py --help` và `git diff --check` đã qua.

Đã kiểm tra adapter trên JSON 36 câu thực tế. Nhãn scene được tạo từ việc đọc hội thoại, độc lập với clustering: trước câu 10 và 15; trước câu 34 để ambiguous. Người dùng đã duyệt các nhóm scene và chọn giữ các gap thiếu bằng chứng ở trạng thái chưa rõ; file nhãn đã duyệt là `data/scene_review/utterances_selected.confirmed.json`.

## Giới hạn và việc tiếp theo

- Đã tạo venv-scenes và chạy ba embedding bằng weights thật trên mẫu 36 câu: xem `docs/benchmark_three_models_20261001.md`. Cấu hình clustering hiện tại cho kết quả kém; chưa có development/test độc lập và chưa chọn model production.
- Model adapters được kiểm tra bằng backend giả lập; tests không chứng minh chất lượng embedding/scene manga.
- Phân loại UI/watermark/SFX bằng các mẫu rõ và overrides đã duyệt, chưa phải classifier đầy đủ. Nội dung mơ hồ được giữ để kiểm tra.
- Affinity score không được MAGI prediction tiêu chuẩn cung cấp nên raw lưu `null` và ghi rõ nguồn; không gọi embedding distance hay hard association là speaker confidence.
- Scene predictions luôn là proposed, các ngưỡng mặc định chưa hiệu chỉnh. Không tự chọn model production, không dùng scene mơ hồ để cắt cứng context dịch.
- Chưa triển khai semantic speaker correction, addressee inference, translation integration hay inpaint. Hình/toạ độ nguồn được bảo toàn cho các bước đó.

## Bắt đầu sử dụng

Xem [hướng dẫn pipeline](pipeline_usage.md) để chạy từ ảnh MAGI hoặc từ JSON hiện có, dựng môi trường embedding riêng và tạo manifest benchmark.

Xem [giải thích nhãn scene](scene_reference_utterances_selected.md) và [file nhãn đã duyệt](../data/scene_review/utterances_selected.confirmed.json). Lệnh `confirm-labels` dành cho các bộ nhãn mới chưa được duyệt; không cần duyệt lại document này.

Các thay đổi ảnh, transcript và file không liên quan đã tồn tại trước lượt triển khai được giữ nguyên.
