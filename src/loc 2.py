from pathlib import Path
import re
import shutil


# ============================================================
# CẤU HÌNH
# ============================================================

# Thư mục chứa page_1.png, page_2.png, ... và transcript.txt
BASE_DIR = Path(__file__).resolve().parent

TRANSCRIPT = BASE_DIR / "transcript.txt"
BACKUP = BASE_DIR / "transcript.txt.bak"

# Tên ảnh dạng page_1.png, page_2.png, ...
IMAGE_PATTERN = re.compile(r"^page_(\d+)\.png$", re.IGNORECASE)


# ============================================================
# 1. XÓA ẢNH SỐ CHẴN + ĐỔI TÊN ẢNH LẺ
# ============================================================

def process_images():
    images = []

    for path in BASE_DIR.iterdir():
        if not path.is_file():
            continue

        match = IMAGE_PATTERN.match(path.name)

        if match:
            number = int(match.group(1))
            images.append((number, path))

    images.sort(key=lambda x: x[0])

    if not images:
        print("Không tìm thấy ảnh page_*.png")
        return

    print(f"Tìm thấy {len(images)} ảnh.")

    # --------------------------------------------------------
    # Xóa ảnh số chẵn
    # --------------------------------------------------------

    even_images = [(n, p) for n, p in images if n % 2 == 0]

    print("\n=== XÓA ẢNH CHẴN ===")

    for number, path in even_images:
        print(f"Xóa: {path.name}")
        path.unlink()

    # --------------------------------------------------------
    # Ảnh lẻ
    # page_1 -> page_1
    # page_3 -> page_2
    # page_5 -> page_3
    # ...
    # --------------------------------------------------------

    odd_images = [(n, p) for n, p in images if n % 2 == 1]

    print("\n=== ĐỔI TÊN ẢNH LẺ ===")

    # Dùng tên tạm để tránh đụng tên.
    # Ví dụ page_3 -> page_2 trong khi page_2 vừa bị xóa
    # thì vẫn an toàn; nhưng dùng tên tạm sẽ chắc chắn hơn.
    temp_files = []

    for index, (old_number, old_path) in enumerate(odd_images, start=1):
        temp_path = BASE_DIR / f"__tmp_page_{index}.png"

        print(
            f"{old_path.name} -> {temp_path.name}"
        )

        old_path.rename(temp_path)
        temp_files.append((index, temp_path))

    # Đổi từ tên tạm -> tên cuối
    for index, temp_path in temp_files:
        new_path = BASE_DIR / f"page_{index}.png"

        print(
            f"{temp_path.name} -> {new_path.name}"
        )

        temp_path.rename(new_path)

    print(f"\nĐã giữ lại {len(odd_images)} ảnh.")
    print(f"Ảnh cuối cùng: page_1.png -> page_{len(odd_images)}.png")


# ============================================================
# 2. SỬA TRANSCRIPT.TXT
# ============================================================

def process_transcript():
    if not TRANSCRIPT.exists():
        print("\nKhông tìm thấy transcript.txt")
        return

    print("\n=== XỬ LÝ TRANSCRIPT ===")

    # Backup
    shutil.copy2(TRANSCRIPT, BACKUP)

    print(f"Backup: {BACKUP.name}")

    text = TRANSCRIPT.read_text(encoding="utf-8")

    # --------------------------------------------------------
    # Tách transcript thành từng trang.
    #
    # Ví dụ:
    #
    # --- Trang 1 ---
    # nội dung...
    #
    # --- Trang 2 ---
    # nội dung...
    # --------------------------------------------------------

    page_pattern = re.compile(
        r"--- Trang (\d+) ---"
    )

    matches = list(page_pattern.finditer(text))

    if not matches:
        print("Không tìm thấy các dòng --- Trang X ---")
        return

    pages = []

    for i, match in enumerate(matches):
        page_number = int(match.group(1))

        start = match.start()

        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(text)

        content = text[start:end]

        pages.append((page_number, content))

    # --------------------------------------------------------
    # Chỉ giữ trang lẻ
    # --------------------------------------------------------

    odd_pages = [
        (number, content)
        for number, content in pages
        if number % 2 == 1
    ]

    print(
        f"Tổng số trang transcript: {len(pages)}"
    )

    print(
        f"Giữ lại: {len(odd_pages)} trang"
    )

    # --------------------------------------------------------
    # Đổi số:
    #
    # Trang 1  -> Trang 1
    # Trang 3  -> Trang 2
    # Trang 5  -> Trang 3
    # ...
    # --------------------------------------------------------

    new_pages = []

    for new_number, (old_number, content) in enumerate(
        odd_pages,
        start=1
    ):
        new_content = re.sub(
            rf"--- Trang {old_number} ---",
            f"--- Trang {new_number} ---",
            content,
            count=1
        )

        new_pages.append(new_content)

    new_text = "".join(new_pages)

    # Ghi lại transcript
    TRANSCRIPT.write_text(
        new_text,
        encoding="utf-8"
    )

    print(
        f"Đã ghi lại transcript.txt"
    )

    print(
        f"Trang cuối: Trang {len(new_pages)}"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("XÓA TRANG CHẴN + ĐÁNH LẠI TRANG LẺ")
    print("=" * 60)

    print(f"\nThư mục làm việc:")
    print(BASE_DIR)

    process_images()
    process_transcript()

    print("\n" + "=" * 60)
    print("HOÀN TẤT")
    print("=" * 60)

    print("\nBackup transcript:")
    print(BACKUP)