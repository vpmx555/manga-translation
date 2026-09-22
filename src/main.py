import os
import glob
from PIL import Image
import numpy as np
from transformers import AutoModel
import torch

# Load mô hình MAGI v2
model = AutoModel.from_pretrained("ragavsachdeva/magiv2", trust_remote_code=True).eval()

def read_image(path_to_image):
    with open(path_to_image, "rb") as file:
        image = Image.open(file).convert("L").convert("RGB")
        image = np.array(image)
    return image

# 1. Đường dẫn tới thư mục chứa ảnh chapter
image_folder = r"C:\Users\pxv23\Downloads\Manga\Manga\VN\Chapter 214"

# 2. Lấy danh sách tất cả các file ảnh (.png, .jpg, .jpeg) và sắp xếp theo thứ tự
image_extensions = ("*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG", "*.JPEG")
chapter_page_paths = []

for ext in image_extensions:
    chapter_page_paths.extend(glob.glob(os.path.join(image_folder, ext)))

# Sắp xếp các đường dẫn file ảnh theo thứ tự tên file
chapter_page_paths.sort()

if not chapter_page_paths:
    print(f"Không tìm thấy ảnh nào trong thư mục: {image_folder}")
    exit()

print(f"Đã tìm thấy {len(chapter_page_paths)} trang ảnh. Đang đọc dữ liệu...")
chapter_pages = [read_image(x) for x in chapter_page_paths]

# 3. Để trống ngân hàng nhân vật vì không nhận diện tên nhân vật
character_bank = {
    "images": [],
    "names": []
}

# 4. Chạy dự đoán OCR và trích xuất hội thoại cho cả Chapter
with torch.no_grad():
    per_page_results = model.do_chapter_wide_prediction(chapter_pages, character_bank, use_tqdm=True, do_ocr=True)

# 5. Xuất kết quả trực quan hóa và ghi file transcript.txt
transcript = []

for i, (image, page_result) in enumerate(zip(chapter_pages, per_page_results)):
    # Lưu ảnh đã vẽ bounding box vị trí bóng thoại
    model.visualise_single_image_prediction(image, page_result, f"page_{i+1}.png")
    
    transcript.append(f"--- Trang {i+1} ---")
    
    for j in range(len(page_result["ocr"])):
        # Bỏ qua các văn bản không thuộc bóng thoại/lời thoại chính
        if not page_result["is_essential_text"][j]:
            continue
        
        # Vì không dùng character_bank, tên người nói sẽ luôn gán là 'speaker'
        transcript.append(f"<speaker>: {page_result['ocr'][j]}")

# 6. Ghi transcript ra file văn bản (định dạng UTF-8 hỗ trợ tiếng Việt)
output_txt_path = "transcript.txt"
with open(output_txt_path, "w", encoding="utf-8") as fh:
    for line in transcript:
        fh.write(line + "\n")

print(f"Hoàn thành! Đã lưu kết quả đọc thoại vào file '{output_txt_path}'.")