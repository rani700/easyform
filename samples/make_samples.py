"""Generate a consistent set of synthetic documents for end-to-end testing.

All documents use the SAME fake candidate (Rahul Kumar Sharma, DOB 2002-03-15)
so cross-validation (name + DOB match) passes. Fake data only.
"""
from PIL import Image, ImageDraw, ImageFont


def font(size: int):
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _marksheet(filename, board_lines, rows, footer="(SYNTHETIC - software testing only)"):
    W, H = 1000, 1150
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, W - 20, H - 20], outline="black", width=3)
    y = 65
    for i, line in enumerate(board_lines):
        d.text((W // 2, y), line, font=font(30 if i == 0 else 22), fill="black", anchor="mm")
        y += 45
    d.line([60, y, W - 60, y], fill="black", width=2)
    y += 35
    for label, value in rows:
        d.text((80, y), f"{label}:", font=font(23), fill="black")
        d.text((440, y), value, font=font(23), fill="black")
        y += 58
    d.text((W // 2, H - 55), footer, font=font(17), fill="gray", anchor="mm")
    img.save(filename)
    print("wrote", filename)


# --- Class 10 ---
_marksheet(
    "samples/sample_10th_marksheet.png",
    ["CENTRAL BOARD OF SECONDARY EDUCATION",
     "SECONDARY SCHOOL EXAMINATION (CLASS X)",
     "STATEMENT OF MARKS - 2018"],
    [("Candidate Name", "RAHUL KUMAR SHARMA"),
     ("Father's Name", "SURESH KUMAR SHARMA"),
     ("Mother's Name", "SUNITA SHARMA"),
     ("Date of Birth", "15-03-2002"),
     ("Roll Number", "2018/CBSE/4471829"),
     ("School", "Delhi Public School, R.K. Puram, New Delhi - 110022"),
     ("Year of Passing", "2018"),
     ("Percentage", "88.4%"),
     ("Result", "PASS")],
)

# --- Class 12 ---
_marksheet(
    "samples/sample_12th_marksheet.png",
    ["CENTRAL BOARD OF SECONDARY EDUCATION",
     "SENIOR SCHOOL CERTIFICATE EXAMINATION (CLASS XII)",
     "STATEMENT OF MARKS - 2020"],
    [("Candidate Name", "RAHUL KUMAR SHARMA"),
     ("Father's Name", "SURESH KUMAR SHARMA"),
     ("Date of Birth", "15-03-2002"),
     ("Stream", "Science (PCM)"),
     ("Roll Number", "2020/CBSE/8830211"),
     ("School", "Delhi Public School, R.K. Puram, New Delhi - 110022"),
     ("Year of Passing", "2020"),
     ("Percentage", "91.2%"),
     ("Result", "PASS")],
)

# --- Graduation ---
_marksheet(
    "samples/sample_graduation_marksheet.png",
    ["DELHI TECHNOLOGICAL UNIVERSITY",
     "BACHELOR OF TECHNOLOGY - FINAL CONSOLIDATED MARKSHEET",
     "DEGREE AWARDED - 2024"],
    [("Candidate Name", "RAHUL KUMAR SHARMA"),
     ("Father's Name", "SURESH KUMAR SHARMA"),
     ("Date of Birth", "15-03-2002"),
     ("Programme", "B.Tech - Computer Science & Engineering"),
     ("Mode of Study", "Full Time"),
     ("Course Duration", "4 Years"),
     ("Enrollment No", "DTU/2K20/CSE/091"),
     ("University", "Delhi Technological University, Shahbad Daulatpur, Delhi - 110042"),
     ("Year of Passing", "2024"),
     ("CGPA", "8.62 / 10"),
     ("Result", "FIRST DIVISION")],
)


# --- Aadhaar card (front side layout) ---
def _aadhaar():
    W, H = 1000, 640
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, W - 8, H - 8], outline="#b34700", width=3)

    # Top band: Government of India
    d.rectangle([8, 8, W - 8, 105], fill="#fdf0e3")
    d.ellipse([35, 28, 95, 88], outline="#8a4b1f", width=3)
    d.text((130, 38), "भारत सरकार",
           font=font(24), fill="#333333")
    d.text((130, 68), "Government of India", font=font(24), fill="#333333")

    # Photo box (left)
    d.rectangle([45, 150, 245, 410], outline="#999999", width=2)
    d.ellipse([105, 195, 185, 275], fill="#d9d9d9")
    d.rectangle([95, 290, 195, 400], fill="#d9d9d9")
    d.text((145, 425), "Photo", font=font(16), fill="#999999", anchor="mm")

    # Details (right of photo)
    x = 290
    d.text((x, 160), "Name: RAHUL KUMAR SHARMA", font=font(25), fill="black")
    d.text((x, 210), "DOB: 15/03/2002", font=font(25), fill="black")
    d.text((x, 260), "Gender: MALE", font=font(25), fill="black")
    d.text((x, 320), "Address:", font=font(22), fill="black")
    addr = ["S/O Suresh Kumar Sharma, House No. 248,",
            "Block C, Saket, South Delhi,",
            "New Delhi, Delhi - 110017"]
    yy = 352
    for line in addr:
        d.text((x + 20, yy), line, font=font(20), fill="black")
        yy += 32

    # Aadhaar number band
    d.rectangle([8, 490, W - 8, 575], fill="#fdf0e3")
    d.text((W // 2, 522), "2847  6391  7391", font=font(44), fill="black", anchor="mm")
    d.text((W // 2, 558),
           "आधार - आम आदमी का अधिकार",
           font=font(20), fill="#8a4b1f", anchor="mm")

    d.text((W // 2, 600), "Unique Identification Authority of India",
           font=font(20), fill="#333333", anchor="mm")
    d.text((W // 2, 624), "(SYNTHETIC Aadhaar - software testing only)",
           font=font(14), fill="gray", anchor="mm")
    img.save("samples/sample_aadhaar.png")
    print("wrote samples/sample_aadhaar.png")


_aadhaar()


# --- Signature (rendered in a cursive font) ---
def cursive_font(size: int):
    for path in (
        "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc",
        "/System/Library/Fonts/Supplemental/BrushScriptMT.ttf",
        "/Library/Fonts/BrushScriptMT.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return font(size)


def _signature():
    W, H = 620, 230
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((45, 70), "R. K. Sharma", font=cursive_font(78), fill="navy")
    d.line([(45, 178), (470, 178)], fill="navy", width=3)
    img.save("samples/sample_signature.png")
    print("wrote samples/sample_signature.png")


_signature()

print("\nAll samples generated.")
