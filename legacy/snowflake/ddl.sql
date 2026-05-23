-- ========================================================================
-- EasyForm Snowflake DDL
-- Run as a role with CREATE DATABASE / CREATE SCHEMA / CREATE TABLE rights.
-- ========================================================================

CREATE DATABASE IF NOT EXISTS EASYFORM;
USE DATABASE EASYFORM;

CREATE SCHEMA IF NOT EXISTS APP;
USE SCHEMA APP;


-- ------------------------------------------------------------------------
-- 1. CANDIDATES  - one row per FINAL, confirmed candidate profile
-- ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CANDIDATES (
    USER_ID                 STRING       NOT NULL,
    EMAIL                   STRING       NOT NULL,

    -- Identity
    NAME                    STRING,
    FATHER_NAME             STRING,
    MOTHER_NAME             STRING,
    DATE_OF_BIRTH           DATE,
    AGE                     NUMBER(3),
    GENDER                  STRING,

    -- Addresses
    PERMANENT_ADDRESS       STRING,
    PERMANENT_PIN_CODE      STRING,
    CORRESPONDENCE_ADDRESS  STRING,
    CORRESPONDENCE_PIN_CODE STRING,

    -- Personal (manual)
    MARITAL_STATUS          STRING,
    NATIONALITY             STRING,
    CASTE                   STRING,
    MOBILE_NUMBER           STRING,
    DISABILITY_STATUS       STRING,

    -- Education (denormalised for fast lookup)
    TENTH_JSON              VARIANT,
    TWELFTH_JSON            VARIANT,
    GRADUATION_JSON         VARIANT,
    POSTGRADUATION_JSON     VARIANT,

    -- Document presence flags
    PASSPORT_PHOTO_VALID    BOOLEAN DEFAULT FALSE,
    SIGNATURE_VALID         BOOLEAN DEFAULT FALSE,
    AADHAAR_PRESENT         BOOLEAN DEFAULT FALSE,
    PAN_PRESENT             BOOLEAN DEFAULT FALSE,

    -- Full raw extracted blob (audit/debug)
    EXTRACTED_RAW           VARIANT,

    CREATED_AT              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT              TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT PK_CANDIDATES PRIMARY KEY (USER_ID)
);


-- ------------------------------------------------------------------------
-- 2. PENDING_REQUESTS - in-flight candidates waiting on follow-up info.
--    n8n's retry-cron workflow reads this table.
-- ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS PENDING_REQUESTS (
    USER_ID             STRING       NOT NULL,
    EMAIL               STRING       NOT NULL,

    -- Latest agent output snapshot (so we can re-ask cleanly).
    LAST_STATUS         STRING       NOT NULL,  -- needs_info | invalid
    MISSING_FIELDS      ARRAY,                  -- e.g. ["field:caste", "document:signature"]
    VALIDATION_ERRORS   VARIANT,                -- list of {code, docs_involved, detail, severity}
    EXTRACTED_SO_FAR    VARIANT,                -- partial CandidateProfile - carry-forward on next attempt

    -- Retry bookkeeping
    ATTEMPT_COUNT       NUMBER(2)    NOT NULL DEFAULT 1,
    LAST_EMAIL_SENT_AT  TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    NEXT_RETRY_AT       TIMESTAMP_NTZ NOT NULL,
    STATUS              STRING       NOT NULL DEFAULT 'awaiting_user',
        -- awaiting_user | discarded | completed

    CREATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT PK_PENDING PRIMARY KEY (USER_ID)
);

-- Helpful indexes (Snowflake auto-clusters small tables; explicit hint only when scaling).
ALTER TABLE PENDING_REQUESTS CLUSTER BY (STATUS, NEXT_RETRY_AT);


-- ------------------------------------------------------------------------
-- 3. DOCUMENTS_AUDIT - one row per uploaded document per attempt
-- ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DOCUMENTS_AUDIT (
    AUDIT_ID            STRING       DEFAULT UUID_STRING(),
    USER_ID             STRING       NOT NULL,
    ATTEMPT_NUMBER      NUMBER(2)    NOT NULL,
    FILENAME            STRING,
    DECLARED_TYPE       STRING,
    CLASSIFIED_TYPE     STRING,
    EXTRACTION          VARIANT,
    PARSE_ERROR         STRING,
    QUALITY_ISSUES      ARRAY,
    CONFIDENCE          FLOAT,
    PROCESSED_AT        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT PK_AUDIT PRIMARY KEY (AUDIT_ID)
);


-- ------------------------------------------------------------------------
-- 4. Convenience view: candidates due for retry email
-- ------------------------------------------------------------------------
CREATE OR REPLACE VIEW PENDING_DUE_FOR_RETRY AS
SELECT
    USER_ID,
    EMAIL,
    ATTEMPT_COUNT,
    MISSING_FIELDS,
    VALIDATION_ERRORS,
    EXTRACTED_SO_FAR,
    LAST_EMAIL_SENT_AT,
    NEXT_RETRY_AT
FROM PENDING_REQUESTS
WHERE STATUS = 'awaiting_user'
  AND ATTEMPT_COUNT < 3
  AND NEXT_RETRY_AT <= CURRENT_TIMESTAMP();


-- ------------------------------------------------------------------------
-- 5. Convenience view: pending users to discard (3 attempts, last expired)
-- ------------------------------------------------------------------------
CREATE OR REPLACE VIEW PENDING_TO_DISCARD AS
SELECT
    USER_ID,
    EMAIL,
    ATTEMPT_COUNT,
    LAST_EMAIL_SENT_AT
FROM PENDING_REQUESTS
WHERE STATUS = 'awaiting_user'
  AND ATTEMPT_COUNT >= 3
  AND NEXT_RETRY_AT <= CURRENT_TIMESTAMP();
