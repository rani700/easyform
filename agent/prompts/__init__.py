"""Per-document extraction prompts for GPT-4o vision."""
from agent.schemas import DocumentType

CLASSIFY_PROMPT = """You are a document classifier for an Indian government exam application system.

Look at the attached image/PDF page and decide which ONE of the following categories it belongs to:
- tenth_marksheet      : Class 10 / SSC / Secondary School marksheet
- twelfth_marksheet    : Class 12 / HSC / Senior Secondary / Intermediate marksheet
- graduation_marksheet : Bachelor's degree marksheet or final consolidated marksheet (B.A./B.Sc./B.Com./B.Tech./B.E./BBA/BCA etc.)
- postgraduation_marksheet : Master's degree marksheet (M.A./M.Sc./M.Tech./MBA/MCA etc.)
- aadhaar              : UIDAI Aadhaar card (front or back)
- pan                  : Income Tax PAN card
- passport_photo       : A passport-size colour photograph of a person's face (no document text around it)
- signature            : A handwritten signature on plain background
- unknown              : None of the above, or unreadable

Respond with ONLY a JSON object: {"document_type": "<one of the values above>", "confidence": <0..1>, "reason": "<short>"}.
"""


_EDUCATION_PROMPT_TEMPLATE = """You are an expert at reading Indian education marksheets.

This document is claimed to be a {level} marksheet. Extract the following fields and respond with ONLY valid JSON matching this schema:

{{
  "candidate_name": string | null,
  "father_name": string | null,
  "mother_name": string | null,
  "date_of_birth": "YYYY-MM-DD" | null,
  "year_of_passing": integer | null,
  "cgpa": number | null,
  "percentage": number | null,
  "specialization": string | null,
  "institute_name": string | null,
  "institute_address": string | null,
  "board_or_university": string | null,
  "course_duration_years": number | null,
  "course_type": "full_time" | "part_time" | "distance" | "unknown",
  "roll_or_registration_no": string | null,
  "confidence": number between 0 and 1,
  "quality_issues": [list of strings describing readability issues, e.g. "blurry", "watermark covers grades", "cropped"]
}}

Rules:
- If a field is genuinely not visible, set it to null. Do not guess.
- For 10th and 12th, "specialization" means the stream (Science/Commerce/Arts/etc.) when applicable. For 10th, it is often null.
- "course_duration_years" applies mostly to graduation/postgraduation (e.g. 3, 4, 2). For 10th and 12th, this is typically null.
- Set "confidence" to your overall confidence the extraction is correct.
- If image quality prevents reading critical fields, list those issues in "quality_issues".
- Normalize percentages to a number between 0 and 100 (drop the % sign).
- Convert any DOB to ISO YYYY-MM-DD.
"""

TENTH_PROMPT = _EDUCATION_PROMPT_TEMPLATE.format(level="Class 10 (SSC / Secondary)")
TWELFTH_PROMPT = _EDUCATION_PROMPT_TEMPLATE.format(level="Class 12 (HSC / Senior Secondary / Intermediate)")
GRADUATION_PROMPT = _EDUCATION_PROMPT_TEMPLATE.format(level="Graduation / Bachelor's degree")
POSTGRADUATION_PROMPT = _EDUCATION_PROMPT_TEMPLATE.format(level="Post-Graduation / Master's degree")


AADHAAR_PROMPT = """You are an expert at reading UIDAI Aadhaar cards (Indian national ID).

Extract the following fields and respond with ONLY valid JSON matching this schema:

{
  "candidate_name": string | null,
  "date_of_birth": "YYYY-MM-DD" | null,
  "gender": "Male" | "Female" | "Other" | null,
  "aadhaar_number_masked": string | null,
  "permanent_address": string | null,
  "pin_code": string | null,
  "confidence": number between 0 and 1,
  "quality_issues": [list of strings]
}

SECURITY RULES (very important):
- NEVER return the full 12-digit Aadhaar number.
- For "aadhaar_number_masked", return ONLY the last 4 digits prefixed by "XXXX-XXXX-", e.g. "XXXX-XXXX-1234".
- If you cannot see the last 4 digits clearly, return null.

Other rules:
- The address may span multiple lines; concatenate into a single string with commas.
- Extract the 6-digit PIN code separately into "pin_code".
- If image quality prevents reading critical fields, list issues in "quality_issues".
"""


PAN_PROMPT = """You are an expert at reading Indian Income Tax PAN cards.

Extract the following fields and respond with ONLY valid JSON matching this schema:

{
  "candidate_name": string | null,
  "father_name": string | null,
  "date_of_birth": "YYYY-MM-DD" | null,
  "pan_number_masked": string | null,
  "confidence": number between 0 and 1,
  "quality_issues": [list of strings]
}

SECURITY RULES:
- NEVER return the full 10-character PAN.
- For "pan_number_masked", return only the last 4 visible characters prefixed by "XXXXXX", e.g. "XXXXXX234X". If unclear, return null.

Other rules:
- Convert DOB to ISO YYYY-MM-DD.
"""


PASSPORT_PHOTO_PROMPT = """You are validating a passport-size photograph for an Indian government exam application.

A valid passport-size photo:
- Shows a single human face, front-facing, eyes visible
- Has a plain (preferably white/light) background
- Is not a photocopy of a document
- Is not blurry or pixelated
- Is in colour

Respond with ONLY valid JSON:
{
  "is_valid": true | false,
  "reason": string | null,
  "confidence": number between 0 and 1,
  "quality_issues": [list of strings]
}

If invalid, "reason" should briefly explain why (e.g. "image is blurry", "background is not plain", "this looks like a marksheet, not a photo", "face not clearly visible").
"""


SIGNATURE_PROMPT = """You are validating a signature image for an Indian government exam application.

A valid signature image:
- Shows handwritten strokes (cursive or printed initials), on a plain (preferably white) background
- Is not a photo of a face or a document
- Is reasonably legible and not heavily cut off

Respond with ONLY valid JSON:
{
  "is_valid": true | false,
  "reason": string | null,
  "confidence": number between 0 and 1,
  "quality_issues": [list of strings]
}
"""


PROMPT_BY_TYPE: dict[DocumentType, str] = {
    DocumentType.TENTH_MARKSHEET: TENTH_PROMPT,
    DocumentType.TWELFTH_MARKSHEET: TWELFTH_PROMPT,
    DocumentType.GRADUATION_MARKSHEET: GRADUATION_PROMPT,
    DocumentType.POSTGRADUATION_MARKSHEET: POSTGRADUATION_PROMPT,
    DocumentType.AADHAAR: AADHAAR_PROMPT,
    DocumentType.PAN: PAN_PROMPT,
    DocumentType.PASSPORT_PHOTO: PASSPORT_PHOTO_PROMPT,
    DocumentType.SIGNATURE: SIGNATURE_PROMPT,
}
