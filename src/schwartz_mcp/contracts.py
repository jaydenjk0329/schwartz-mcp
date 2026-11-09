"""Typed stage payloads. Source truth and persuasion quality need review beyond schemas."""

from copy import deepcopy
from jsonschema import Draft202012Validator
from .common import DomainError, digest

TEXT = {"type": "string", "minLength": 1}
STR = {"type": "string"}
NUM = {"type": "number"}


def enum(*xs):
    return {"type": "string", "enum": list(xs)}


def arr(item=TEXT):
    return {"type": "array", "items": deepcopy(item)}


def obj(**props):
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


BASIS = obj(
    summary=TEXT,
    reasoning=TEXT,
    evidence_ids=arr(),
    source_atom_ids=arr(),
    assumptions=arr(),
    limitations=arr(),
)


def analysis(**props):
    return obj(**BASIS["properties"], **props)


CLAIM = obj(
    text=TEXT,
    kind=enum("factual", "hypothesis", "subjective"),
    support=enum("supported", "unsupported", "contradicted", "not_applicable"),
    evidence_ids=arr(),
)
CANDIDATE = obj(
    id=TEXT,
    content=TEXT,
    rationale=TEXT,
    hypothesis_id=TEXT,
    claims=arr(CLAIM),
    source_atom_ids=arr(),
    evidence_ids=arr(),
    limitations=arr(),
)
LEVELS = (
    "market_hypothesis",
    "strategic_angle",
    "headline_and_lead",
    "structure",
    "complete_asset",
)
CANDIDATES = obj(context_id=TEXT, level=enum(*LEVELS), candidates=arr(CANDIDATE))
FINDING = obj(
    criterion=TEXT,
    span=STR,
    severity=enum("blocking", "revision", "note"),
    finding=TEXT,
    evidence_ids=arr(),
    source_atom_ids=arr(),
    suggested_change=STR,
)
REVIEW = obj(
    role=enum("market", "source", "creative", "evidence", "experiment"),
    candidate_id=TEXT,
    candidate_hash=TEXT,
    verdict=enum("pass", "revise", "abstain"),
    findings=arr(FINDING),
    uncertainty=STR,
)
REVIEWS = obj(reviews=arr(REVIEW))
COMPARISON = obj(
    outcome=enum("A", "B", "tie", "abstain"),
    rationale=TEXT,
    criterion_findings=arr(TEXT),
    evidence_ids=arr(),
)
CHECK_REQUEST = obj(
    adapter_id=enum("knowledge_search", "offer_arithmetic", "asset_measurements"),
    arguments_json=TEXT,
    question=TEXT,
)
SCHEMAS = {
    "campaign_brief": analysis(
        product=TEXT,
        audience=TEXT,
        objective=TEXT,
        channels=arr(),
        offer=TEXT,
        constraints=arr(),
        decision=TEXT,
    ),
    "evidence_ledger": analysis(evidence_refs=arr(), missing_evidence=arr()),
    "product_map": analysis(
        features=arr(),
        performances=arr(),
        benefits=arr(),
        physical_facts=arr(),
        limitations_of_product=arr(),
    ),
    "claim_ledger": analysis(claims=arr(CLAIM)),
    "market_map": analysis(
        existing_desires=arr(),
        permanent_forces=arr(),
        changing_forces=arr(),
        viability_assumptions=arr(),
    ),
    "segment_profile": analysis(
        segment=TEXT,
        motivations=arr(),
        identity=arr(),
        beliefs=arr(),
        situations=arr(),
        alternatives=arr(),
    ),
    "market_diagnosis": analysis(
        awareness=enum(
            "most-aware",
            "product-aware",
            "solution-aware",
            "problem-aware",
            "unaware",
            "mixed",
            "unknown",
        ),
        sophistication=enum("s1", "s2", "s3", "s4", "s5", "mixed", "unknown"),
        competing_diagnoses=arr(),
        research_needed=arr(),
    ),
    "identity_plan": analysis(
        primary_image=TEXT,
        desired_roles=arr(),
        product_trait_credibility=TEXT,
        audience_identification=TEXT,
        bridge=arr(),
    ),
    "mechanism_plan": analysis(
        real_mechanism=TEXT,
        presentation=enum("name", "describe", "feature", "unknown", "not_applicable"),
        causal_steps=arr(),
        unverified_steps=arr(),
    ),
    "belief_plan": analysis(
        accepted_premises=arr(),
        intermediate_conclusions=arr(),
        final_claim=TEXT,
        sequence=arr(),
    ),
    "objection_plan": analysis(
        difficulty=arr(),
        importance=arr(),
        price=arr(),
        switching=arr(),
        responses=arr(),
    ),
    "comparison_plan": analysis(
        compare=enum("yes", "no", "unknown"),
        alternatives=arr(),
        buyer_costs=arr(),
        matched_remedies=arr(),
    ),
    "offer_plan": analysis(
        price=TEXT,
        terms=arr(),
        delivery=TEXT,
        guarantee=TEXT,
        limitations_of_offer=arr(),
        reason_for_terms=TEXT,
    ),
    "campaign_sequence": analysis(
        ordered_asset_ids=arr(), jobs=arr(), continuity_rule=TEXT
    ),
    "independent_reviews": REVIEWS,
    "debate_record": analysis(
        disagreements=arr(),
        decisions=arr(),
        unresolved_dissent=arr(),
        tool_requests=arr(CHECK_REQUEST),
    ),
    "experiment_plan": analysis(
        control_id=TEXT,
        treatment_ids=arr(),
        metric=TEXT,
        allocation=TEXT,
        stopping_rule=TEXT,
        analysis_plan=TEXT,
        falsification=TEXT,
    ),
    "experiment_analysis": analysis(
        observation_artifact_id=TEXT,
        design=enum("randomized", "observational", "unknown"),
        results=arr(),
        causal_limitations=arr(),
    ),
    "project_findings": analysis(
        population=TEXT,
        channel=TEXT,
        period=TEXT,
        findings=arr(),
        transfer_limits=arr(),
    ),
    "audit_findings": analysis(defects=arr(FINDING), original_asset_refs=arr()),
    "repair_plan": analysis(priorities=arr(), changes=arr(), preserve=arr()),
    "product_improvement_brief": analysis(
        unsupported_promises=arr(), requirements=arr(), verification_needed=arr()
    ),
    "source_analysis": analysis(
        assertions=arr(), exceptions=arr(), tensions=arr(), passage_ids=arr()
    ),
}
for name in (
    "strategy_hypotheses",
    "headline_candidates",
    "structure_candidates",
    "draft_candidates",
    "proof_checked_candidates",
    "polished_candidates",
    "channel_assets",
    "revised_candidates",
    "selected_candidates",
):
    SCHEMAS[name] = CANDIDATES
# Builtin outputs are also explicit artifacts; never accepted from model submissions.
SCHEMAS["tournament_result"] = obj(
    context_id=TEXT,
    level=enum(*LEVELS),
    candidate_ids=arr(),
    comparison_ids=arr(),
    ranking=arr(
        obj(
            candidate_id=TEXT,
            points=NUM,
            comparisons={"type": "integer"},
            hypothesis_id=TEXT,
        )
    ),
    excluded=arr(obj(candidate_id=TEXT, reason=TEXT)),
    limitations=arr(),
    label=enum("judge_preference_not_conversion_probability"),
    sensitivity=obj(
        score_method=TEXT,
        components=arr(arr()),
        judge_count={"type": "integer"},
        order_checks=arr(
            obj(
                candidate_ids=arr(),
                judge=TEXT,
                comparison_ids=arr(),
                preference_changed={"type": "boolean"},
                same_displayed_side_selected={"type": "boolean"},
            )
        ),
        rank_bands=arr(
            obj(
                candidate_id=TEXT,
                best_rank={"type": "integer"},
                worst_rank={"type": "integer"},
            )
        ),
        scenarios=arr(
            obj(
                label=TEXT,
                comparable={"type": "boolean"},
                ranking=arr(
                    obj(candidate_id=TEXT, points=NUM, comparisons={"type": "integer"})
                ),
            )
        ),
        interpretation=TEXT,
        empirical_confidence={"type": "boolean"},
    ),
)
SCHEMAS["campaign_package"] = obj(
    artifact_ids=arr(),
    selected_candidate_ids=arr(),
    gap_ids=arr(),
    milestone=enum("package_ready"),
    limitations=arr(),
)
ROLES = ("market", "source", "creative", "evidence", "experiment")
PLAN_ROLES = (
    "identity_plan",
    "mechanism_plan",
    "belief_plan",
    "objection_plan",
    "comparison_plan",
    "offer_plan",
    "repair_plan",
    "campaign_sequence",
)


def validate(role, payload):
    schema = SCHEMAS[role.split(":")[0]]
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda e: str(e.path)
    )
    if errors:
        e = errors[0]
        raise DomainError("artifact_schema", f"{role} at {list(e.path)}: {e.message}")


def candidate_hash(candidate):
    return digest(candidate)


def candidate_set(inputs):
    value = inputs.get("candidate_pool")
    return value["payload"] if value else None


def output_schema(roles):
    return obj(**{r: deepcopy(SCHEMAS[r.split(":")[0]]) for r in roles})


def inspect_references(value, known_atoms, evidence_ids):
    if isinstance(value, dict):
        for key, sub in value.items():
            if key == "source_atom_ids" and not set(sub) <= known_atoms:
                raise DomainError(
                    "unknown_source", "Output cites unknown semantic atoms"
                )
            if (
                key in ("evidence_ids", "evidence_refs")
                and not set(sub) <= evidence_ids
            ):
                raise DomainError(
                    "unknown_evidence", "Output cites evidence not in this project"
                )
            inspect_references(sub, known_atoms, evidence_ids)
    elif isinstance(value, list):
        for x in value:
            inspect_references(x, known_atoms, evidence_ids)
