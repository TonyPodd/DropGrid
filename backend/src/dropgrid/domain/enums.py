from enum import StrEnum


class GenderTag(StrEnum):
    male = "male"
    female = "female"
    unspecified = "unspecified"


class CategoryGender(StrEnum):
    """Which accounts may send to a grid category. Unisex categories accept any account."""

    male = "male"
    female = "female"
    unisex = "unisex"


class AccountStatus(StrEnum):
    active = "active"
    disabled = "disabled"
    invalid = "invalid"


class CampaignStatus(StrEnum):
    draft = "draft"
    ready = "ready"
    running = "running"
    monitoring = "monitoring"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class SubmissionStatus(StrEnum):
    pending = "pending"
    sending = "sending"
    submitted = "submitted"
    published = "published"
    not_found = "not_found"
    failed = "failed"
    skipped = "skipped"
