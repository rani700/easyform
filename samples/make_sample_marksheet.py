"""Generate a synthetic Class 10 marksheet image for end-to-end testing.

This is fake data only — used to verify the agent's extraction pipeline without
needing a real candidate document.
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1300
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)


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


d.rectangle([20, 20, W - 20, H - 20], outline="black", width=3)
d.text((W // 2, 70), "CENTRAL BOARD OF SECONDARY EDUCATION", font=font(30), fill="black", anchor="mm")
d.text((W // 2, 120), "SECONDARY SCHOOL EXAMINATION (CLASS X)", font=font(24), fill="black", anchor="mm")
d.text((W // 2, 160), "STATEMENT OF MARKS - 2018", font=font(22), fill="black", anchor="mm")
d.line([60, 195, W - 60, 195], fill="black", width=2)

rows = [
    ("Candidate Name", "RAHUL KUMAR SHARMA"),
    ("Father's Name", "SURESH KUMAR SHARMA"),
    ("Mother's Name", "SUNITA SHARMA"),
    ("Date of Birth", "15-03-2002"),
    ("Roll Number", "2018/CBSE/4471829"),
    ("School", "Delhi Public School, R.K. Puram, New Delhi - 110022"),
    ("Year of Passing", "2018"),
    ("Percentage", "88.4%"),
    ("Result", "PASS"),
]
y = 240
for label, value in rows:
    d.text((80, y), f"{label}:", font=font(24), fill="black")
    d.text((420, y), value, font=font(24), fill="black")
    y += 60

d.line([60, y + 10, W - 60, y + 10], fill="black", width=2)
d.text((80, y + 40), "SUBJECT-WISE MARKS", font=font(22), fill="black")
subjects = [
    ("English", "85"),
    ("Hindi", "82"),
    ("Mathematics", "95"),
    ("Science", "91"),
    ("Social Science", "89"),
]
y += 90
for sub, marks in subjects:
    d.text((100, y), sub, font=font(22), fill="black")
    d.text((600, y), marks + " / 100", font=font(22), fill="black")
    y += 50

d.text((W // 2, H - 70), "(This is a SYNTHETIC document for software testing only)",
       font=font(18), fill="gray", anchor="mm")

img.save("samples/sample_10th_marksheet.png")
print("wrote samples/sample_10th_marksheet.png")
