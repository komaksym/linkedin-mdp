"""Strict domain models for the offline prospect report."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    """Base model that rejects undeclared fields and caller mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def normalize_linkedin_profile_url(value: str) -> str:
    """Return the canonical LinkedIn `/in/<slug>` identity key or reject the URL."""
    parsed = urlparse(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"linkedin.com", "www.linkedin.com"}
        or parsed.netloc.casefold() not in {"linkedin.com", "www.linkedin.com"}
    ):
        raise ValueError("invalid LinkedIn profile URL")
    if parsed.query or parsed.fragment or parsed.params:
        raise ValueError("LinkedIn profile URL must not contain query or fragment")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].casefold() != "in" or not parts[1].strip():
        raise ValueError("LinkedIn profile URL must use /in/<slug>")
    slug = parts[1].strip().casefold()
    return f"https://www.linkedin.com/in/{slug}"


def require_aware(value: datetime) -> datetime:
    """Reject naive datetimes because replay depends on an unambiguous clock."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value


class Target(FrozenModel):
    """Canonical subject identity for one reconciliation batch."""

    linkedin_url: str

    @field_validator("linkedin_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        """Normalize only an explicit LinkedIn profile URL."""
        return normalize_linkedin_profile_url(value)


class TimeWindow(FrozenModel):
    """A time instant or interval whose precision is retained explicitly."""

    earliest: datetime
    latest: datetime
    precision: Literal["instant", "day"]

    @field_validator("earliest", "latest")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """Require timezone-aware interval boundaries."""
        return require_aware(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "TimeWindow":
        """Reject inverted intervals instead of inventing chronology."""
        if self.earliest > self.latest:
            raise ValueError("time window earliest is after latest")
        return self


class Evidence(FrozenModel):
    """Immutable source observation with raw payload and traceable provenance."""

    id: str = Field(min_length=1)
    delivery: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    subject_url: str
    observed_at: TimeWindow
    published_at: TimeWindow | None = None
    effective_at: TimeWindow | None = None
    raw: dict[str, Any]

    @field_validator("subject_url")
    @classmethod
    def _normalize_subject(cls, value: str) -> str:
        """Bind evidence to one explicit LinkedIn profile key."""
        return normalize_linkedin_profile_url(value)


class EmploymentClaim(FrozenModel):
    """One indivisible job assertion; company and title never cross claim boundaries."""

    kind: Literal["employment"]
    id: str = Field(min_length=1)
    subject_url: str
    role_key: str = Field(min_length=1)
    company: str | None = None
    title: str | None = None
    status: Literal["current", "historical", "unspecified", "ended"]
    effective_at: TimeWindow | None = None
    ended_at: TimeWindow | None = None
    evidence_ids: tuple[str, ...]

    @field_validator("subject_url")
    @classmethod
    def _normalize_subject(cls, value: str) -> str:
        """Bind the claim to one explicit LinkedIn profile key."""
        return normalize_linkedin_profile_url(value)

    @field_validator("evidence_ids")
    @classmethod
    def _normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique provenance references and canonicalize their order."""
        if not value or len(value) != len(set(value)) or any(not item for item in value):
            raise ValueError("claim evidence IDs must be non-empty and unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def _require_role_fact(self) -> "EmploymentClaim":
        """Require at least one role field while preserving incomplete observations."""
        if not self.company and not self.title:
            raise ValueError("employment claim needs a company or title")
        return self


class MessageClaim(FrozenModel):
    """One channel event claim; drafts and plans remain non-events for state derivation."""

    kind: Literal["message"]
    id: str = Field(min_length=1)
    subject_url: str
    channel: Literal["email", "linkedin"]
    event: Literal["sent", "received", "draft", "planned"]
    occurred_at: TimeWindow | None = None
    evidence_ids: tuple[str, ...]

    @field_validator("subject_url")
    @classmethod
    def _normalize_subject(cls, value: str) -> str:
        """Bind the event to one explicit LinkedIn profile key."""
        return normalize_linkedin_profile_url(value)

    @field_validator("evidence_ids")
    @classmethod
    def _normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique provenance references and canonicalize their order."""
        if not value or len(value) != len(set(value)) or any(not item for item in value):
            raise ValueError("message evidence IDs must be non-empty and unique")
        return tuple(sorted(value))


Claim = Annotated[EmploymentClaim | MessageClaim, Field(discriminator="kind")]


class Validation(FrozenModel):
    """Cited assessment of a complete current-employment role set."""

    id: str = Field(min_length=1)
    scope: Literal["current_employment"]
    assessor: str = Field(min_length=1)
    assessed_at: datetime
    target_url: str
    claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    basis: str = Field(min_length=1)
    verdict: Literal["supported", "rejected", "uncertain"]
    applies_from: datetime | None = None
    applies_until: datetime | None = None

    @field_validator("assessed_at", "applies_from", "applies_until")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        """Require timezone-aware assessment applicability boundaries."""
        return None if value is None else require_aware(value)

    @field_validator("target_url")
    @classmethod
    def _normalize_target(cls, value: str) -> str:
        """Bind the assessment to one explicit LinkedIn profile key."""
        return normalize_linkedin_profile_url(value)

    @field_validator("claim_ids", "evidence_ids")
    @classmethod
    def _normalize_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique referenced IDs and canonicalize their order."""
        if not value or len(value) != len(set(value)) or any(not item for item in value):
            raise ValueError("validation references must be non-empty and unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def _validate_applicability(self) -> "Validation":
        """Reject inverted validation applicability intervals."""
        if self.applies_from and self.applies_until and self.applies_from > self.applies_until:
            raise ValueError("validation applicability is inverted")
        return self


class Batch(FrozenModel):
    """Validated immutable input for one target identity."""

    version: Literal[1]
    target: Target
    evidence: tuple[Evidence, ...]
    claims: tuple[Claim, ...]
    validations: tuple[Validation, ...] = ()

    @model_validator(mode="after")
    def _validate_graph(self) -> "Batch":
        """Validate unique IDs, identity bindings and every provenance reference."""
        evidence = {item.id: item for item in self.evidence}
        claims = {item.id: item for item in self.claims}
        validations = {item.id: item for item in self.validations}
        if len(evidence) != len(self.evidence):
            raise ValueError("duplicate evidence ID")
        if len(claims) != len(self.claims):
            raise ValueError("duplicate claim ID")
        if len(validations) != len(self.validations):
            raise ValueError("duplicate validation ID")

        target = self.target.linkedin_url
        for item in self.evidence:
            if item.subject_url != target:
                raise ValueError("evidence identity mismatch")
        for claim in self.claims:
            if claim.subject_url != target:
                raise ValueError("claim identity mismatch")
            if any(item not in evidence for item in claim.evidence_ids):
                raise ValueError("claim references unknown evidence")
        for validation in self.validations:
            if validation.target_url != target:
                raise ValueError("validation identity mismatch")
            if any(item not in evidence for item in validation.evidence_ids):
                raise ValueError("validation references unknown evidence")
            if any(item not in claims for item in validation.claim_ids):
                raise ValueError("validation references unknown claim")
            if any(not isinstance(claims[item], EmploymentClaim) for item in validation.claim_ids):
                raise ValueError("current employment validation references non-employment claim")
        return self


class RoleView(FrozenModel):
    """One atomic role fact with all selected claim and evidence provenance."""

    claim_ids: tuple[str, ...]
    role_keys: tuple[str, ...]
    company: str | None
    title: str | None
    evidence_ids: tuple[str, ...]


class EmploymentView(FrozenModel):
    """Resolved current roles or a conservative unknown/review state."""

    status: Literal["resolved", "unknown", "needs_review"]
    current_roles: tuple[RoleView, ...]
    alternative_roles: tuple[RoleView, ...]
    supporting_validation_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]


class EventFact(FrozenModel):
    """Positive event evidence with no negative inference from absence."""

    status: Literal["known", "unknown"]
    claim_ids: tuple[str, ...]
    undated_claim_ids: tuple[str, ...]
    latest: TimeWindow | None


class ChannelView(FrozenModel):
    """Independent sent and received facts for one outreach channel."""

    channel: Literal["email", "linkedin"]
    sent: EventFact
    received: EventFact


class ResearchRequest(FrozenModel):
    """Exact unresolved claim scope and evidence required for a later run."""

    scope: Literal["current_employment"]
    target_url: str
    claim_ids: tuple[str, ...]
    question: str
    required_evidence: tuple[str, ...]
    reason_codes: tuple[str, ...]


class ReviewItem(FrozenModel):
    """Stable diagnostic code tied to exact validation or claim IDs."""

    code: str
    ids: tuple[str, ...]


class Report(FrozenModel):
    """Canonical replayable report that retains the full normalized evidence batch."""

    version: Literal[1]
    as_of: datetime
    target: Target
    evidence: tuple[Evidence, ...]
    claims: tuple[Claim, ...]
    validations: tuple[Validation, ...]
    employment: EmploymentView
    channels: tuple[ChannelView, ...]
    research_requests: tuple[ResearchRequest, ...]
    review: tuple[ReviewItem, ...]

    @field_validator("as_of")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """Require an explicit report clock."""
        return require_aware(value)
