"""Post the synthetic sample documents to a running EasyForm agent and print the result.

Usage:
    python samples/run_demo.py [agent_url]
Default agent_url: http://127.0.0.1:8000
"""
import base64
import json
import sys
import urllib.request

AGENT_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

DOCS = [
    ("samples/sample_10th_marksheet.png", "10th_marksheet.png"),
    ("samples/sample_12th_marksheet.png", "12th_marksheet.png"),
    ("samples/sample_graduation_marksheet.png", "graduation_marksheet.png"),
    ("samples/sample_aadhaar.png", "aadhaar.png"),
    ("samples/sample_signature.png", "signature.png"),
]


def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


payload = {
    "user_id": "rahul@example.com",
    "email": "rahul@example.com",
    "attempt_number": 1,
    "manual_fields": {
        "marital_status": "single",
        "nationality": "Indian",
        "caste": "General",
        "mobile_number": "9876543210",
        "correspondence_address": "248 Block C, Saket, New Delhi",
        "correspondence_pin_code": "110017",
        "disability_status": "None",
    },
    "documents": [
        {"filename": name, "content_base64": b64(path), "mime_type": "image/png"}
        for path, name in DOCS
    ],
}

req = urllib.request.Request(
    f"{AGENT_URL}/process",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=300) as resp:
    result = json.loads(resp.read())

print(json.dumps(result, indent=2))
