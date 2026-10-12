"""Verify reconstruction artifacts and build a focused comparison/review."""
import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))

import numpy as np
from PIL import Image, ImageDraw
from manga_pipeline.storage.io import atomic_json, file_hash, read_json
from manga_pipeline.storage.runs import RunStore
from manga_pipeline.visual.stages import verify_files
from typeset_trace import finalize_page_trace


def review(directory, baseline, focus_page, focus_utterance):
    directory = directory.resolve()
    result = read_json(directory / "ocr-typeset-result.json")
    inpaint = read_json(directory / "result.json")
    store = RunStore(inpaint["run_dir"])
    geometry = store.read("bubble_geometry")
    config = store.manifest["config"]["v6"]
    assert config["min_body_area"] == 2500 and config["body_margin_px"] == 10 and config["body_margin_ratio"] == 0
    assert result["completed_pages"] == result["expected_pages"] and not result["failures"]
    assert not result["translation_performed"] and not result["analyze_performed"]
    artifact_count, stage_count, trial_count, outside = 0, 0, 0, 0
    for page, erased in zip(result["pages"], inpaint["pages"]):
        assert page["page"] == erased["page"]
        verify_files(directory, erased)
        verify_files(directory, page)
        base = directory / "pages" / f"page-{page['page']}" / "ocr-restored"
        original = Image.open(directory / "pages" / f"page-{page['page']}" / "original.png").convert("RGB")
        clean = Image.open(directory / "pages" / f"page-{page['page']}" / "clean.png").convert("RGB")
        final = Image.open(base / "rendered.png").convert("RGB")
        plans = read_json(base / "typeset-plan.json")["plans"]
        glyph_union = np.zeros((original.height,original.width),dtype=bool)
        for row in plans:
            tokens = []
            for part in row["parts"]:
                assert part["margin_px"] == 10
                if row["status"] != "fitted":
                    continue
                safe = np.asarray(Image.open(directory / part["safe_mask"]["mask_path"]).convert("L")) > 0
                glyph = np.asarray(Image.open(directory / part["glyph_mask"]["mask_path"]).convert("L")) > 0
                assert not np.any(glyph & ~safe)
                glyph_union |= glyph
                tokens.extend(word for line in part["lines"] for word in line["text"].split())
            if row["status"] == "fitted":
                assert tokens == row["ocr_text"].split()
        changed = np.any(np.asarray(final)!=np.asarray(clean),axis=2)
        assert not np.any(changed & ~glyph_union)
        outside += erased["outside_mask_changed_pixels"]
        for trace in page["traces"]:
            trace_directory = base / trace["directory"]
            refreshed = finalize_page_trace(trace_directory, original, final)
            prefix = base.relative_to(directory).as_posix()
            page["files"].update({prefix+"/"+relative:sha for relative,sha in refreshed.items()})
            trace_json = read_json(trace_directory / "trace.json")
            stage_count += len(trace_json["stages"])
            trial_count += len(trace_json["font_trials"])
        verify_files(directory, page)
        artifact_count += len(page["files"]) + len(erased["files"])
    result["final_page_comparisons"] = True
    atomic_json(directory / "ocr-typeset-result.json", result)
    focused = directory / "pages" / f"page-{focus_page}" / "ocr-restored"
    plan = read_json(focused / "typeset-plan.json")["plans"][focus_utterance-1]
    trace_path = focused / f"utterance-{focus_utterance}-trace" / "trace.json"
    trace = read_json(trace_path)
    boxes = [part["bbox"] for part in plan["parts"]]
    image = Image.open(directory / "pages" / f"page-{focus_page}" / "original.png").convert("RGB")
    crop = (max(0,min(b[0] for b in boxes)-24), max(0,min(b[1] for b in boxes)-24),
        min(image.width,max(b[2] for b in boxes)+24),min(image.height,max(b[3] for b in boxes)+24))
    images = [("Original", image.crop(crop))]
    if baseline:
        images.append(("Previous: Arial / old margin", Image.open(baseline / "pages" / f"page-{focus_page}" / "ocr-restored/rendered.png").convert("RGB").crop(crop)))
    images.append(("Current: AstroCity / margin 10", Image.open(focused / "rendered.png").convert("RGB").crop(crop)))
    comparison = Image.new("RGB", (images[0][1].width*len(images),images[0][1].height+32), "white")
    draw = ImageDraw.Draw(comparison)
    for index, (title, value) in enumerate(images):
        comparison.paste(value,(index*value.width,32))
        draw.text((index*value.width+8,8),title,fill="black")
    comparison_path = directory / f"page-{focus_page}-utterance-{focus_utterance}-original-old-new.png"
    comparison.save(comparison_path)
    findings = {"verified_artifacts": artifact_count, "stage_images":stage_count, "font_trials":trial_count,
        "restored_source_texts":result["restored_source_texts"], "selected_source_texts":result["selected_source_texts"],
        "outside_erase_mask_changed_pixels":outside, "outside_safe_glyph_pixels":0,
        "outside_glyph_composite_changed_pixels":0, "config":config,
        "focused": {"page":focus_page,"utterance":focus_utterance,"group_id":plan["group_id"],
            "opening_radius_px":trace["opening_radius_px"],"opening_components":trace["opening_components"],
            "bridge_pixels":trace["bridge_pixels"],"parts":trace["parts"],"plan":trace["selected_plan"]}}
    atomic_json(directory / "verification-and-findings.json",findings)
    lines = ["# Chẩn đoán tái tạo text — 2500 px² / 10 px / AstroCity", "",
        f"Local, 11 trang After School chapter 1. Đặt lại **{result['restored_source_texts']}/{result['selected_source_texts']}** vùng OCR; không dịch, không analyze, không sửa OCR, không rút gọn LLM.", "",
        "[Gallery toàn bộ group](typeset-gallery.html) · [Mục lục Markdown](ocr-typeset-review.md) · [Kết quả kiểm tra](verification-and-findings.json)", "",
        "## Cấu hình và nguồn", "", "- `min_body_area=2500`: chọn lõi body sau opening và phân loại component; không phải bộ lọc xóa mọi bubble nhỏ. Khi không có lõi đạt ngưỡng, code vẫn giữ mask làm body dự phòng.",
        "- Margin cố định `10 px`, `body_margin_ratio=0.0`; pixel ảnh gốc, không scale theo ảnh preview.",
        "- Font `assets/fonts/astro-city-teddy-bear.ttf`, bản sao byte-identical của font người dùng; đủ ký tự tiếng Việt trong kiểm tra cmap.",
        "- Bubble threshold `0.45`, MAGI text `0.45`, association `0.25`, dialogue logit `-0.5` trong source cache. Chỉ geometry/normalize/inpaint/typeset được tính lại; detection/OCR không chạy lại trên CPU.", "",
        "## Từng bước nhỏ", "",
        "Mỗi group có: OCR bbox/thứ tự trước group → từng candidate → OR observed → inferred/repair → refined → opening cores → body/bridge/tail → mapping OCR/group/part → clean → margin/safe/anchor → slots ở mọi cỡ font đã thử → phân bổ từ thực tế → line boxes → glyph từng part → composite từng part → so sánh cuối trang.", "",
        "JSON giữ text trước/sau group, word tokens/ranges, capacity, tọa độ từng dòng, trạng thái của mọi font trial. HTML mở riêng phần font trial để dễ xem; Markdown chứa toàn bộ ảnh.", "",
        "## Bubble đôi được yêu cầu", "",
        f"[Chi tiết từng bước](pages/page-{focus_page}/ocr-restored/utterance-{focus_utterance}-trace/README.vi.md) · [So sánh gốc / cũ / mới]({comparison_path.name})", "",
        f"![Gốc, cấu hình cũ, cấu hình mới]({comparison_path.name})", "",
        f"Opening bán kính **{trace['opening_radius_px']} px** vẫn giữ một lõi **{trace['opening_components'][0]['area_px']} px²**. Group có **{len(trace['parts'])} body**, bridge mask **{trace['bridge_pixels']} px**; hai OCR cùng map vào part đó. Ngưỡng 2500 không làm cầu nối rộng tự tách ra.", "",
        "`normalize()` nối hai OCR thành một chuỗi. `safe_parts()` chỉ dùng điểm neo trung bình của các bbox OCR trong part. `allocate()` chia từ giữa các part, nên với một part nó chỉ có một vùng để phân bổ. `segment_layout()` ưu tiên ít dòng trước, sau đó mới độ gần điểm neo. Ranh giới hai đoạn OCR không là ràng buộc dàn chữ.", "",
        f"Cỡ được chọn: **{plan['font_size_px']} px**. Dòng `ENOUGH... EVEN` nối hai đoạn nguồn trong cùng dòng; chữ dàn qua vùng nối được coi là body và phần dưới bị để trống. Glyph nằm trong safe mask nhưng bố cục không giữ hai khối như ảnh gốc. Thay font/margin chưa giải quyết lỗi topology này.", "",
        "## Hướng cải thiện, chưa triển khai", "",
        "1. Giữ group chung cho analyze/dịch, nhưng tạo nhiều vùng dàn vật lý. Dùng distance transform, watershed và bằng chứng chỗ thắt/độ lõm contour để đề xuất các body; giữ đường nối trong topology của group và loại nó khỏi vùng chữ. Không cắt rời group chỉ vì khác panel.",
        "2. Giữ từng OCR bbox làm neo cho từng vùng dàn thay vì lấy một trung bình chung. Bbox phục vụ layout; nhãn gợi ý loại thoại vẫn chỉ dùng ảnh/mask.",
        "3. Khi phục hồi OCR gốc, có thể giữ ranh giới đoạn theo OCR nguồn; khi dịch, phân bổ chuỗi chung vào nhiều vùng theo capacity và thứ tự, có common font và penalty cân bằng/độ lệch neo. Không tự thêm ngắt đoạn này vào pipeline hiện tại trong lượt chẩn đoán.", "",
        "## Kiểm tra", "",
        f"Đã kiểm tra {artifact_count} artifact có checksum, {stage_count} visual trung gian và {trial_count} lần thử font. Glyph ngoài safe mask: 0; composite ngoài glyph: 0; sửa ngoài erase mask: {outside}. Token OCR không bị mất, nhân đôi hoặc đổi thứ tự.", "",
        "Body giả trang 3 bị loại khỏi tập lõi bởi ngưỡng 2500; câu trước đây không fit nay được vẽ. Geometry toàn chapter đổi từ 50 group/51 part sang 39 group/39 part; vẫn chọn 44 OCR. Đây là kết quả chẩn đoán của chapter này, không phải bảo đảm chất lượng cho mọi truyện.", ""]
    (directory / "README.vi.md").write_text("\n".join(lines),encoding="utf-8")
    print(f"verified {artifact_count} artifacts, {stage_count} visuals, {trial_count} font trials; restored {result['restored_source_texts']}/{result['selected_source_texts']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir",required=True,type=Path)
    parser.add_argument("--baseline-dir",type=Path)
    parser.add_argument("--page",type=int,default=4)
    parser.add_argument("--utterance",type=int,default=3)
    args=parser.parse_args()
    review(args.probe_dir,args.baseline_dir,args.page,args.utterance)
