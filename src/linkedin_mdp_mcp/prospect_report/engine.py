"""Pure deterministic prospect reconciliation policy."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .model import (
    Batch,
    ChannelView,
    EmploymentClaim,
    EmploymentView,
    EventFact,
    MessageClaim,
    Report,
    ResearchRequest,
    ReviewItem,
    RoleView,
    TimeWindow,
    Validation,
    require_aware,
)


def _is_current_candidate(claim: EmploymentClaim, as_of: datetime) -> bool:
    """Return whether a claim can describe a current role at the explicit clock."""
    if claim.status != "current":
        return False
    if claim.effective_at and claim.effective_at.latest > as_of:
        return False
    if claim.ended_at and claim.ended_at.latest <= as_of:
        return False
    return True


def _validation_applicability(
    validation: Validation,
    batch: Batch,
    candidates: dict[str, EmploymentClaim],
    as_of: datetime,
) -> tuple[bool, str | None]:
    """Check whether a supported validation can resolve current employment now."""
    if validation.verdict != "supported":
        return False, None
    if validation.assessed_at > as_of:
        return False, "validation_future"
    if validation.applies_from is None:
        return False, "validation_missing_effective_time"
    if validation.applies_from > as_of:
        return False, "validation_future"
    if validation.applies_until is not None and validation.applies_until < as_of:
        return False, "validation_expired"
    if any(item not in candidates for item in validation.claim_ids):
        return False, "validation_claim_not_current"
    if any(
        not candidates[item].company or not candidates[item].title
        for item in validation.claim_ids
    ):
        return False, "validation_incomplete_role"

    cited_evidence = set(validation.evidence_ids)
    if any(
        not set(candidates[claim_id].evidence_ids).issubset(cited_evidence)
        for claim_id in validation.claim_ids
    ):
        return False, "validation_missing_claim_evidence"

    evidence = {item.id: item for item in batch.evidence}
    for evidence_id in validation.evidence_ids:
        item = evidence[evidence_id]
        if item.effective_at is None:
            return False, "validation_evidence_undated"
        if item.effective_at.latest > as_of:
            return False, "validation_evidence_future"
    return True, None


def _fact_key(claim: EmploymentClaim) -> tuple[str | None, str | None]:
    """Return the role fields that a validation resolves as one atomic fact."""
    return claim.company, claim.title


def _role_views(claims: list[EmploymentClaim]) -> tuple[RoleView, ...]:
    """Group identical atomic role facts while unioning provenance deterministically."""
    groups: dict[tuple[str | None, str | None], list[EmploymentClaim]] = defaultdict(list)
    for claim in claims:
        groups[_fact_key(claim)].append(claim)

    views: list[RoleView] = []
    for (company, title), items in groups.items():
        views.append(
            RoleView(
                claim_ids=tuple(sorted(item.id for item in items)),
                role_keys=tuple(sorted({item.role_key for item in items})),
                company=company,
                title=title,
                evidence_ids=tuple(sorted({evidence_id for item in items for evidence_id in item.evidence_ids})),
            )
        )
    return tuple(
        sorted(
            views,
            key=lambda item: (
                item.company or "",
                item.title or "",
                item.claim_ids,
            ),
        )
    )


def _resolve_employment(
    batch: Batch,
    as_of: datetime,
) -> tuple[EmploymentView, tuple[ResearchRequest, ...], tuple[ReviewItem, ...]]:
    """Resolve the complete current-role set from applicable cited validations."""
    candidates = {
        claim.id: claim
        for claim in batch.claims
        if isinstance(claim, EmploymentClaim) and _is_current_candidate(claim, as_of)
    }
    candidate_list = [candidates[item] for item in sorted(candidates)]
    reviews: list[ReviewItem] = []
    applicable: list[Validation] = []

    for validation in sorted(batch.validations, key=lambda item: item.id):
        ok, reason = _validation_applicability(validation, batch, candidates, as_of)
        if ok:
            applicable.append(validation)
        elif reason is not None:
            reviews.append(ReviewItem(code=reason, ids=(validation.id,)))

    supported_sets: dict[
        frozenset[tuple[str | None, str | None]], list[Validation]
    ] = defaultdict(list)
    for validation in applicable:
        facts = frozenset(_fact_key(candidates[item]) for item in validation.claim_ids)
        supported_sets[facts].append(validation)

    current: tuple[RoleView, ...] = ()
    support_ids: tuple[str, ...] = ()
    selected: list[EmploymentClaim] = []
    reasons: tuple[str, ...]
    if len(supported_sets) == 1:
        validations = next(iter(supported_sets.values()))
        selected_ids = {
            claim_id
            for validation in validations
            for claim_id in validation.claim_ids
        }
        selected = [candidates[claim_id] for claim_id in sorted(selected_ids)]
        current = _role_views(selected)
        support_ids = tuple(sorted(item.id for item in validations))
        status = "resolved"
        reasons = ()
    elif len(supported_sets) > 1:
        status = "needs_review"
        reasons = ("conflicting_supported_validations",)
        reviews.append(
            ReviewItem(
                code="conflicting_supported_validations",
                ids=tuple(sorted(item.id for values in supported_sets.values() for item in values)),
            )
        )
    elif candidate_list:
        status = "needs_review"
        facts = {_fact_key(item) for item in candidate_list}
        reasons = (
            "conflicting_current_employment" if len(facts) > 1 else "unvalidated_current_employment",
        )
    else:
        status = "unknown"
        reasons = ("current_employment_unknown",)

    selected_facts = {_fact_key(item) for item in selected}
    alternatives = _role_views(
        [item for item in candidate_list if _fact_key(item) not in selected_facts]
    )
    research: tuple[ResearchRequest, ...] = ()
    if status != "resolved":
        research = (
            ResearchRequest(
                scope="current_employment",
                target_url=batch.target.linkedin_url,
                claim_ids=tuple(sorted(candidates)),
                question="Which complete current role set applies to this exact LinkedIn identity at the report time?",
                required_evidence=(
                    "dated citation bound to the exact target identity",
                    "company and title from the same role observation",
                    "explicit support for all concurrent current roles",
                ),
                reason_codes=reasons,
            ),
        )

    return (
        EmploymentView(
            status=status,
            current_roles=current,
            alternative_roles=alternatives,
            supporting_validation_ids=support_ids,
            reason_codes=reasons,
        ),
        research,
        tuple(sorted(reviews, key=lambda item: (item.code, item.ids))),
    )


def _strict_latest(claims: list[MessageClaim]) -> TimeWindow | None:
    """Return a latest event only when its interval is strictly after every peer."""
    if not claims or any(item.occurred_at is None for item in claims):
        return None
    if len(claims) == 1:
        return claims[0].occurred_at

    for candidate in claims:
        window = candidate.occurred_at
        if window is None:
            continue
        others = [item.occurred_at for item in claims if item.id != candidate.id]
        if all(other is not None and window.earliest > other.latest for other in others):
            return window
    return None


def _event_fact(
    claims: list[MessageClaim],
    channel: str,
    event: str,
    as_of: datetime,
) -> EventFact:
    """Derive one positive event fact without treating absence as a negative fact."""
    matches = [
        item
        for item in claims
        if item.channel == channel
        and item.event == event
        and (item.occurred_at is None or item.occurred_at.latest <= as_of)
    ]
    matches.sort(key=lambda item: item.id)
    if not matches:
        return EventFact(status="unknown", claim_ids=(), undated_claim_ids=(), latest=None)
    return EventFact(
        status="known",
        claim_ids=tuple(item.id for item in matches),
        undated_claim_ids=tuple(item.id for item in matches if item.occurred_at is None),
        latest=_strict_latest(matches),
    )


def _resolve_channels(batch: Batch, as_of: datetime) -> tuple[ChannelView, ...]:
    """Derive email and LinkedIn histories independently from actual event claims."""
    messages = [item for item in batch.claims if isinstance(item, MessageClaim)]
    return tuple(
        ChannelView(
            channel=channel,
            sent=_event_fact(messages, channel, "sent", as_of),
            received=_event_fact(messages, channel, "received", as_of),
        )
        for channel in ("email", "linkedin")
    )


def resolve(batch: Batch, *, as_of: datetime) -> Report:
    """Purely derive one canonical report from an immutable batch and explicit clock."""
    as_of = require_aware(as_of)
    employment, research, review = _resolve_employment(batch, as_of)
    return Report(
        version=1,
        as_of=as_of,
        target=batch.target,
        evidence=tuple(sorted(batch.evidence, key=lambda item: item.id)),
        claims=tuple(sorted(batch.claims, key=lambda item: item.id)),
        validations=tuple(sorted(batch.validations, key=lambda item: item.id)),
        employment=employment,
        channels=_resolve_channels(batch, as_of),
        research_requests=research,
        review=review,
    )
