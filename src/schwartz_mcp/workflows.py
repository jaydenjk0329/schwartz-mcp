from copy import deepcopy
from .common import read_json, digest, DomainError
from .contracts import ROLES, PLAN_ROLES, SCHEMAS
from .retrieval import VERSION as RETRIEVAL_VERSION, LEVEL_TECHNIQUES


class Registry:
    def __init__(self, root):
        self.workflows = {
            w["id"]: w for w in read_json(root / "workflows/catalog.json")
        }
        self.stages = {s["id"]: s for s in read_json(root / "workflows/stages.json")}
        self.channels = {
            c["id"]: c for c in read_json(root / "workflows/channel-adapters.json")
        }
        self.version = digest(
            {
                "workflows": self.workflows,
                "stages": self.stages,
                "channels": self.channels,
                "payload_schemas": SCHEMAS,
                "contract_migration": 3,
                "retrieval_policy": [RETRIEVAL_VERSION, LEVEL_TECHNIQUES],
            }
        )

    def get(self, ident):
        if ident not in self.workflows:
            raise DomainError("unknown_workflow", ident)
        return deepcopy(self.workflows[ident])

    def compile(self, ident, profile):
        workflow = self.get(ident)
        nodes = []

        def append(stage, outputs, bindings, task="model", **extra):
            nodes.append(
                {
                    "stage_id": stage,
                    "outputs": outputs,
                    "bindings": bindings,
                    "task_type": task,
                    **extra,
                }
            )

        for node in workflow["nodes"]:
            stage = node["stage_id"]
            s = self.stages[stage]
            bindings = deepcopy(node["bindings"])
            # Each task receives explicit strategy inputs, never all prior outputs or peer drafts.
            if stage in (
                "headlines",
                "structure",
                "draft",
                "proof",
                "polish",
                "adapt",
                "independent_review",
                "debate",
                "export",
                "theme",
            ):
                for role in PLAN_ROLES:
                    bindings[role] = "optional:" + role
            if stage in ("draft", "structure", "theme"):
                bindings["audit_findings"] = "optional:audit_findings"
            if stage == "learn":
                bindings["observed_results"] = "external:observed_results"
            if stage == "theme":
                count = profile["generators"]
                for i in range(count):
                    append(
                        stage,
                        [f"strategy_hypotheses:g{i}"],
                        bindings,
                        generator_index=i,
                    )
                append(
                    stage,
                    ["strategy_hypotheses"],
                    {f"g{i}": f"strategy_hypotheses:g{i}" for i in range(count)},
                    "merge_candidates",
                )
            elif stage == "independent_review":
                for role in ROLES:
                    append(
                        stage,
                        [f"independent_reviews:{role}"],
                        bindings,
                        review_role=role,
                    )
                append(
                    stage,
                    ["independent_reviews"],
                    {r: f"independent_reviews:{r}" for r in ROLES},
                    "merge_reviews",
                )
            elif stage == "debate" and profile["debate_rounds"] == 0:
                append(
                    stage,
                    ["revised_candidates"],
                    {"candidate_pool": bindings["candidate_pool"]},
                    "copy_candidates",
                )
            elif stage == "debate":
                # One bounded decision/revision round per compiled step; preserve dissent at each round.
                for round_no in range(profile["debate_rounds"]):
                    b = deepcopy(bindings)
                    if round_no:
                        b["candidate_pool"] = "revised_candidates"
                    append(
                        stage,
                        ["debate_record", "revised_candidates"],
                        b,
                        debate_round=round_no + 1,
                    )
                    for role in ROLES:
                        append(
                            "independent_review",
                            [f"independent_reviews:{role}"],
                            {
                                "candidate_pool": "revised_candidates",
                                "evidence_ledger": "evidence_ledger",
                                "campaign_brief": "campaign_brief",
                                **{r: "optional:" + r for r in PLAN_ROLES},
                            },
                            review_role=role,
                            after_revision=True,
                        )
                    append(
                        "independent_review",
                        ["independent_reviews"],
                        {r: f"independent_reviews:{r}" for r in ROLES},
                        "merge_reviews",
                    )
            elif stage == "rank":
                append(stage, s["produces"], bindings, "tournament")
            elif stage == "export":
                bindings["experiment_plan"] = "optional:experiment_plan"
                append(stage, s["produces"], bindings, "export")
            else:
                append(stage, s["produces"], bindings)
        return nodes
