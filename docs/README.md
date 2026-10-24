# Tài liệu dự án manga translation

## Pipeline hiện hành — v7

Bắt đầu tại **[v7/README.md](v7/README.md)**. Bộ này mô tả code/defaults ngày
2026-10-23, chia theo cấu trúc v6 nhưng cập nhật GLM-OCR, LLM scene/name scan,
independent analyze, identity profiles, directed relationship, natural-only
translation và Runpod L40S Secure.

- [Tóm tắt kiến trúc/stages](v7/foundation/current-state.md).
- [Toàn bộ config và ràng buộc](v7/foundation/configuration.md).
- [Hướng dẫn chạy](v7/deployment/runbook.md), [resume/reuse/lỗi](v7/deployment/recovery.md).
- [Identity và relationship](v7/identity/identity-relationship.md).

## Chính sách vận hành đang áp dụng

Các chính sách này vẫn có hiệu lực dù tên đường dẫn chứa v6:

- `AGENTS.md` tại root: quyền chạy, giới hạn validation, GPU preference.
- [Runpod fast policy](news/runpod_agent_fast_policy_vi.md).
- [Local working-set operational plan](plans/runpod-v6-agent-operational-plan.md).

## Tài liệu nền và lịch sử

| Khu vực | Vai trò |
|---|---|
| [v6](v6/README.md) | Thiết kế nền, báo cáo chuyên sâu và lịch sử triển khai; đọc nhãn ngày/status |
| `plan/`, `plans/` | Kế hoạch, decision, triển khai từng đợt; không tự xem mọi proposal là đã có code |
| `news/` | Điều tra, diagnostic và chính sách theo thời điểm |
| [summary VI](pipeline_summary_vi.md), [summary EN](pipeline_summary_en.md) | Baseline v4 ngày2026-10-06; giữ làm lịch sử |
| [workflow cũ](workflow_overview.md) | Sơ đồ factory cũ; hướng dẫn hiện hành ở v7 |
| [export field guide cũ](export_result_json_field_guide_vi.md) | Schema v4; không dùng để đọc export v7 như cùng schema |

Không xóa/move hàng loạt các báo cáo lịch sử vì nhiều decision và liên kết còn
trỏ tới chúng. Các entry cũ được gắn nhãn và dẫn về v7; nội dung lịch sử không
được sửa thành lời khẳng định về code mới.

## Tài liệu nghiên cứu

[Related-work outline](related-work-outline-vi.md) và các bản introduction/paper
section trong root docs là tài liệu viết đồ án. Chúng cần trích nguồn/đối chiếu
riêng; không dùng thay runbook hoặc bằng chứng quality evaluation của pipeline.
