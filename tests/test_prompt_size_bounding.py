"""Phase 6A.6: prompt/evidence size bounding.

Constructs EvidencePackage/Observation objects directly (rather than via
a real GraphEngine) so each section's line count can be controlled
precisely — this is testing prompts.py's rendering/truncation policy in
isolation, not evidence-gathering correctness (that's tests/test_evidence.py's
job, unaffected by this change).

Limits (Phase 6A.6):
    FACTS                    — hard safety ceiling 200, not a normal-use limit
    INFERRED, high confidence — max 25
    INFERRED, heuristic       — max 25
    OTHER OBSERVATIONS        — max 25
"""

from app.explanation.evidence import EvidencePackage, Observation, ObservationKind
from app.explanation.prompts import build_prompt


def _parsed_observation(index: int) -> Observation:
    return Observation(
        kind=ObservationKind.DIRECT_RELATIONSHIP,
        subject_id="root",
        related_id=f"parsed-{index:04d}",
        origin="parsed",
        detail={"edge_type": "depends_on"},
    )


def _high_confidence_observation(index: int) -> Observation:
    return Observation(
        kind=ObservationKind.DIRECT_RELATIONSHIP,
        subject_id="root",
        related_id=f"high-{index:04d}",
        origin="inferred",
        confidence="high",
        basis="label selector match",
        detail={"edge_type": "connects_to"},
    )


def _heuristic_observation(index: int) -> Observation:
    return Observation(
        kind=ObservationKind.DIRECT_RELATIONSHIP,
        subject_id="root",
        related_id=f"heuristic-{index:04d}",
        origin="inferred",
        confidence="heuristic",
        basis="image reference match",
        detail={"edge_type": "connects_to"},
    )


def _other_observation(index: int) -> Observation:
    return Observation(
        kind=ObservationKind.DEPENDENCY,
        subject_id="root",
        related_id=f"other-{index:04d}",
        detail={"name": f"other-{index:04d}"},
    )


# --- INFERRED / OTHER: max 25, truncated with an explicit marker -----------


def test_high_confidence_section_truncated_at_25_with_omission_marker() -> None:
    observations = [_high_confidence_observation(i) for i in range(30)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert request.prompt.count("high-") == 25
    assert "... and 5 more (omitted for length)." in request.prompt


def test_heuristic_section_truncated_at_25_with_omission_marker() -> None:
    observations = [_heuristic_observation(i) for i in range(40)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert request.prompt.count("heuristic-") == 25
    assert "... and 15 more (omitted for length)." in request.prompt


def test_other_observations_section_truncated_at_25_with_omission_marker() -> None:
    observations = [_other_observation(i) for i in range(28)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert "other-0000" in request.prompt
    assert "other-0024" in request.prompt
    assert "other-0025" not in request.prompt
    assert "other-0027" not in request.prompt
    assert "... and 3 more (omitted for length)." in request.prompt


def test_sections_under_the_limit_are_not_truncated() -> None:
    observations = [_high_confidence_observation(i) for i in range(10)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert request.prompt.count("high-") == 10
    assert "omitted for length" not in request.prompt


# --- FACTS: hard ceiling 200, not a normal-use limit ------------------------


def test_facts_are_not_truncated_under_the_200_ceiling() -> None:
    """150 parsed facts — well above the 25-line limit used for other
    sections, but nowhere near FACTS' 200 ceiling — must ALL appear."""
    observations = [_parsed_observation(i) for i in range(150)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert request.prompt.count("parsed-") == 150
    assert "omitted for length" not in request.prompt


def test_facts_are_truncated_at_the_200_hard_ceiling_with_omission_marker() -> None:
    observations = [_parsed_observation(i) for i in range(250)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert request.prompt.count("parsed-") == 200
    assert "... and 50 more (omitted for length)." in request.prompt


# --- deterministic first-N ordering ------------------------------------------


def test_truncation_keeps_the_first_n_observations_deterministically() -> None:
    observations = [_high_confidence_observation(i) for i in range(30)]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert "high-0000" in request.prompt
    assert "high-0024" in request.prompt
    assert "high-0025" not in request.prompt
    assert "high-0029" not in request.prompt


def test_truncation_is_deterministic_across_repeated_calls() -> None:
    observations = [_heuristic_observation(i) for i in range(40)]
    package = EvidencePackage(subject_ids=["root"], observations=observations)

    first = build_prompt(package)
    second = build_prompt(package)
    assert first == second


# --- the critical guarantee: inferred/other can never crowd out FACTS ------


def test_facts_are_never_crowded_out_by_a_large_inferred_section() -> None:
    """A genuinely large heuristic section (500 observations, far beyond
    its own 25-line limit) must have zero effect on how many FACTS are
    shown — each section's budget is independent, not shared."""
    observations = [_parsed_observation(i) for i in range(10)] + [
        _heuristic_observation(i) for i in range(500)
    ]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    assert request.prompt.count("parsed-") == 10  # every fact still shown, untouched
    assert "omitted for length" in request.prompt  # the heuristic section is the one that's truncated


def test_facts_heading_and_omission_marker_appear_before_other_sections() -> None:
    """Sanity check on rendering order: FACTS (with its own marker, if
    any) is never pushed out of the prompt or displaced by a later,
    larger section."""
    observations = [_parsed_observation(i) for i in range(250)] + [
        _heuristic_observation(i) for i in range(50)
    ]
    request = build_prompt(EvidencePackage(subject_ids=["root"], observations=observations))

    facts_index = request.prompt.index("FACTS")
    heuristic_index = request.prompt.index("INFERRED, heuristic confidence")
    assert facts_index < heuristic_index
    assert request.prompt.count("parsed-") == 200
    assert request.prompt.count("heuristic-") == 25
