"""Pydantic models for the EasyForm agent API and internal state."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


class DocumentType(str, Enum):
    TENTH_MARKSHEET = "tenth_marksheet"
    TWELFTH_MARKSHEET = "twelfth_marksheet"
    GRADUATION_MARKSHEET = "graduation_marksheet"
    POSTGRADUATION_MARKSHEET = "postgraduation_marksheet"
    PASSPORT_PHOTO = "passport_photo"
    SIGNATURE = "signature"
    AADHAAR = "aadhaar"
    PAN = "pan"
    UNKNOWN = "unknown"


class ProcessStatus(str, Enum):
    COMPLETE = "complete"
    NEEDS_INFO = "needs_info"
    INVALID = "invalid"


class CourseType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    DISTANCE = "distance"
    UNKNOWN = "unknown"


class MaritalStatus(str, Enum):
    SINGLE = "single"
    MARRIED = "married"
    DIVORCED = "divorced"
    WIDOWED = "widowed"


# ---------- Per-document extraction schemas ----------

class EducationRecord(BaseModel):
    """Common shape for 10th/12th/graduation/postgraduation marksheets."""
    candidate_name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    date_of_birth: Optional[str] = None  # ISO YYYY-MM-DD if parseable, else raw
    year_of_passing: Optional[int] = None
    cgpa: Optional[float] = None
    percentage: Optional[float] = None
    specialization: Optional[str] = None  # stream / major
    institute_name: Optional[str] = None
    institute_address: Optional[str] = None
    board_or_university: Optional[str] = None
    course_duration_years: Optional[float] = None
    course_type: CourseType = CourseType.UNKNOWN
    roll_or_registration_no: Optional[str] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    quality_issues: list[str] = Field(default_factory=list)


class AadhaarRecord(BaseModel):
    candidate_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    aadhaar_number_masked: Optional[str] = None  # last 4 digits only, e.g. "XXXX-XXXX-1234"
    permanent_address: Optional[str] = None
    pin_code: Optional[str] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    quality_issues: list[str] = Field(default_factory=list)


class PANRecord(BaseModel):
    candidate_name: Optional[str] = None
    father_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    pan_number_masked: Optional[str] = None  # last 4 only, e.g. "XXXXX1234X"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    quality_issues: list[str] = Field(default_factory=list)


class PhotoRecord(BaseModel):
    """Passport-size photo OR signature image."""
    is_valid: bool = False
    reason: Optional[str] = None  # e.g. "blurry", "not a face", "not a signature"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    quality_issues: list[str] = Field(default_factory=list)


# ---------- Manual fields supplied by user in email body ----------

class ManualFields(BaseModel):
    marital_status: Optional[MaritalStatus] = None
    nationality: Optional[str] = None
    caste: Optional[str] = None  # e.g. General/OBC/SC/ST/EWS
    mobile_number: Optional[str] = None
    correspondence_address: Optional[str] = None
    correspondence_pin_code: Optional[str] = None
    disability_status: Optional[str] = None  # "None" or description
    email: Optional[EmailStr] = None

    # Optional education-stream hints — used when the candidate states the
    # stream in their email body but it couldn't be read from the marksheet.
    tenth_specialization: Optional[str] = None
    twelfth_specialization: Optional[str] = None
    graduation_specialization: Optional[str] = None
    postgraduation_specialization: Optional[str] = None


# ---------- API request / response ----------

class IncomingDocument(BaseModel):
    """One uploaded file as the agent receives it."""
    filename: str
    content_base64: str  # base64-encoded file bytes
    mime_type: str
    declared_type: Optional[DocumentType] = None  # optional caller-declared type


class ProcessRequest(BaseModel):
    user_id: str  # stable id (usually the email address)
    email: EmailStr
    documents: list[IncomingDocument]
    manual_fields: ManualFields = Field(default_factory=ManualFields)
    attempt_number: int = 1  # increments on follow-up emails
    previous_extracted: Optional[dict[str, Any]] = None  # carry-over from prior attempt


class ValidationError(BaseModel):
    code: str  # e.g. "name_mismatch", "dob_mismatch", "wrong_doc_type", "poor_image_quality"
    docs_involved: list[DocumentType] = Field(default_factory=list)
    detail: str
    severity: str = "error"  # "warning" | "error"


class CandidateProfile(BaseModel):
    """Merged final profile written to the `candidates` table."""
    user_id: str
    email: EmailStr

    # Identity (cross-validated from docs)
    name: Optional[str] = None
    father_name: Optional[str] = None
    mother_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None

    # Addresses
    permanent_address: Optional[str] = None
    permanent_pin_code: Optional[str] = None
    correspondence_address: Optional[str] = None
    correspondence_pin_code: Optional[str] = None

    # Personal (from manual fields)
    marital_status: Optional[MaritalStatus] = None
    nationality: Optional[str] = None
    caste: Optional[str] = None
    mobile_number: Optional[str] = None
    disability_status: Optional[str] = None

    # Education
    tenth: Optional[EducationRecord] = None
    twelfth: Optional[EducationRecord] = None
    graduation: Optional[EducationRecord] = None
    postgraduation: Optional[EducationRecord] = None

    # Document presence flags
    passport_photo_valid: bool = False
    signature_valid: bool = False
    aadhaar_present: bool = False
    pan_present: bool = False

    processed_at: datetime = Field(default_factory=datetime.utcnow)


class ProcessResponse(BaseModel):
    status: ProcessStatus
    user_id: str
    extracted: CandidateProfile
    missing_fields: list[str] = Field(default_factory=list)
    validation_errors: list[ValidationError] = Field(default_factory=list)
    documents_received: dict[str, bool] = Field(default_factory=dict)  # which DocumentType -> seen
    notes: list[str] = Field(default_factory=list)


# ---------- LangGraph internal state ----------

class AgentState(BaseModel):
    """Mutable state threaded through the graph."""
    request: ProcessRequest
    classified: dict[str, DocumentType] = Field(default_factory=dict)  # filename -> type
    per_doc_extractions: dict[str, Any] = Field(default_factory=dict)  # filename -> record
    profile: CandidateProfile = None  # type: ignore[assignment]
    validation_errors: list[ValidationError] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
