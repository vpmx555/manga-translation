# Manga pipeline v7

Luồng hiện hành: **layout → extract → bubble_geometry → normalize → graph → analyze → reanalyze → build_identity → relationship → export → translate/typeset**, với inpaint tùy chọn.

Bắt đầu tại **[docs/v7/README.md](docs/v7/README.md)**. Docs được tổ chức theo stage; config, vận hành và migration có trang riêng.

- Graph đọc text/metadata toàn chapter, dựng scene/event và nhóm tên.
- Analyze xem ảnh vùng trang, chạy song song giữa scene và tuần tự giữa event; mỗi event tối đa 8 utterance.
- Reanalyze đối chiếu một lượt toàn chapter, trả patch theo ID và confidence gate.
- Tất cả request LLM dùng **64K, thinking tắt**.
- GLM-OCR, MAGI/Koharu, bank và pipeline natural-only tiếp tục được dùng.

[Configuration](docs/v7/configuration.md) · [Operations](docs/v7/operations.md) · [Design](docs/v7/design.md) · [Migration và validation](docs/v7/migration.md)

Code nền/controller/storage vẫn giữ namespace v6. Contract semantic hiện tại là `chapter-story-graph-v7`; không resume run cũ bằng cách sửa manifest. Runpod dùng L40S Secure và chỉ được truy cập khi người dùng cấp quyền cho tác vụ hiện tại.

Kiểm tra model-free cho luồng mới:

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest discover -s tests -p test_chapter_story_graph.py -v
```

Tài liệu và test cho các contract cũ được giữ trong archive/historical; xem [migration](docs/v7/migration.md). Kiểm tra fixture không thay thế đánh giá chất lượng bằng model thật.
