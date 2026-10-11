# Thứ tự triển khai và vùng mã

> Luồng đã được nối vào code v6; [trạng thái triển khai/kiểm chứng](implementation-status.md) ghi kết quả thực tế. Chạy local; pilot After School chapter 1.

| Đợt | Mốc | Vùng mã / kết quả review |
|---|---|---|
| 1 | M0–M3; schema N2/I1 | Khóa fixtures/manifest/cache version; `visual/koharu.py` giữ class onomatopoeia; layout/candidate, text-role evidence, name-index schema và fusion/topology. Review raw/merged masks, nguồn SFX và group xuyên panel. |
| 2 | M4–M5 | `visual/geometry.py`, `visual/bubbles.py`, các module body/bridge/repair mới, `visual/stages.py`. Tách M5a mapping và M5b classifier ảnh/mask; khóa input whitelist/cache độc lập OCR. Review contour và visual hint trên từng ca; không thêm inference style. |
| 3 | M6–M7, N2/N3, I1–I3 | `normalization/dialogue.py`, scan/analyze context/contract/validator, name/honorific index, evidence receipt, person registry/link store riêng và person journal; bank MAGI read-only. Chốt speech style bằng text + visual hints trong response analyze hiện có. Review thoại ghép, role, tên trùng, speaker chung và cluster–person linking trước ngữ cảnh dịch. |
| 4 | M8; effects route | Translation source/contract, review/export adapters. Dịch một chuỗi cho utterance; SFX/trạng thái giữ context và dịch riêng vào JSON/Markdown. Review glossary chữ thường và honorific validator. |
| 5 | M9–M10 | Module typeset mới, font receipt, cache/overflow contract và vòng dịch lại hữu hạn. Review safe mask/glyph overlay trước render trang sạch. |
| 6 | M11 | End-to-end local After School; đối chiếu baseline cùng config, tests lỗi cụ thể và review Markdown/PNG; kiểm tra identity collision, one-call evidence và SFX không ghi lên ảnh. |

Storage/manifest versioning đi cùng schema; cập nhật `storage/runs.py`,
fingerprint/dependencies và `pipeline.py`. Person store/resolver mới giữ cluster
ID/raw association/source boxes. Không merge/migrate/disable/ghi đè bank MAGI;
legacy được đọc qua adapter để tạo person records riêng với receipt/hash.

N1 không triển khai. Không có mốc render SFX lên ảnh. Mỗi đợt có artifact kiểm tra
được, không tạo gate approval cho từng trang/stage. Người dùng đã xác nhận thiết kế và yêu cầu triển khai; không tạo thêm bước xin
duyệt cho những phần đã được ủy quyền. Inpaint runtime v6 chờ export để bảo vệ
effects và content role chưa rõ, rồi typeset dùng clean page nếu có.

### Phụ thuộc và bàn giao

- M0 khóa baseline và fixtures trước khi đo thay đổi; M1 và M2 độc lập về inference.
- M3 cần candidate/mask M1; M4 cần topology M3; M5a mapping cần contour M4 và text boxes M2; M5b hints chỉ cần ảnh/mask/geometry M4, không phụ thuộc text M2.
- N2 lấy evidence M1/M2/M5; M6 giữ text nguồn và role rồi tạo utterance.
- I1 cung cấp index ứng viên; M7 xác nhận tên/người nói theo context và role.
- I2 tổng hợp calling events theo caller-first; I3 ghi links/person journal ở
  tầng riêng trước M8/export. Repeated calls rõ đủ dù visual xa; giữ nhánh một
  call rõ + ảnh phù hợp.
- M8 có hai loại target: utterance theo group và effects theo role riêng.
- M9 chỉ nhận utterance translation; M10 phản hồi M8 để rút gọn hữu hạn.
- M11 kiểm tra toàn chuỗi với model receipts, config và manifest cố định.

---

[← Tổng quan v6](../README.md)
