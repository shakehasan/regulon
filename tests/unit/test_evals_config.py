"""Guards on config/evals.yaml.

The eval gates are declared in config, not code, so these tests are what keep the
declaration honest: every gate must be a number in a plausible range, rubric weights must
form a real weighted average, and the default tracing path must stay local (Non-Negotiable
#3 — the default runtime needs no account, no API key, no paid service).
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALS_CONFIG = REPO_ROOT / "config" / "evals.yaml"

RATE_SUITES = {
    "retrieval": ["recall_at_10", "mrr", "ndcg_at_10"],
    "generation_ragas": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
    "citations": ["citation_precision", "citation_recall", "uncited_claim_rate_max"],
    "routing": ["routing_accuracy", "cost_efficiency_min"],
    "guardrails": ["block_rate", "false_positive_rate_max"],
    "end_to_end": ["structural_assertions_pass_rate", "brief_completion_rate"],
}


@pytest.fixture(scope="module")
def evals_config() -> dict:
    return yaml.safe_load(EVALS_CONFIG.read_text(encoding="utf-8"))


def test_config_file_exists_and_parses(evals_config):
    assert isinstance(evals_config, dict)


def test_all_expected_suites_declared(evals_config):
    expected = set(RATE_SUITES) | {"generation_geval"}
    assert expected <= set(evals_config["suites"])


@pytest.mark.parametrize(("suite", "gate_names"), RATE_SUITES.items())
def test_rate_gates_are_numbers_between_zero_and_one(evals_config, suite, gate_names):
    gates = evals_config["suites"][suite]["gates"]
    for name in gate_names:
        value = gates[name]
        assert isinstance(value, int | float), f"{suite}.{name} must be numeric"
        assert 0.0 <= value <= 1.0, f"{suite}.{name}={value} outside [0, 1]"


def test_geval_rubric_gates_use_the_one_to_five_scale(evals_config):
    rubrics = evals_config["suites"]["generation_geval"]["rubrics"]
    assert rubrics, "G-Eval suite declares no rubrics"
    for name, spec in rubrics.items():
        assert 1.0 <= spec["gate"] <= 5.0, f"rubric {name} gate outside the 1-5 scale"


def test_geval_rubric_weights_form_a_weighted_average(evals_config):
    weights = [spec["weight"] for spec in evals_config["suites"]["generation_geval"]["rubrics"].values()]
    assert sum(weights) == pytest.approx(1.0), "rubric weights must sum to 1.0"


def test_guardrail_suite_requires_a_meaningful_attack_count(evals_config):
    assert evals_config["suites"]["guardrails"]["min_attacks"] >= 30


def test_judge_is_deterministic(evals_config):
    judge = evals_config["judge"]
    assert judge["temperature"] == 0.0
    assert isinstance(judge["seed"], int)


def test_judge_agreement_is_reported_not_gated(evals_config):
    """Gating on an uncalibrated judge is forbidden until M7 sets a baseline (ADR-009)."""
    assert evals_config["judge"]["agreement"]["report_only"] is True


def test_experiment_tracking_is_a_local_store(evals_config):
    """Cross-run comparison must not depend on a hosted service (ADR-009)."""
    tracking = evals_config["experiment_tracking"]
    assert tracking["store"] == "local_jsonl"
    assert tracking["path"].endswith(".jsonl")


def test_runs_are_keyed_for_reproducible_comparison(evals_config):
    key_by = evals_config["experiment_tracking"]["key_by"]
    assert {"git_sha", "config_hash", "dataset_version"} <= set(key_by)


def test_eval_config_declares_no_hosted_or_metered_service(evals_config):
    """Zero-cost guarantee: nothing in the eval path may need an account, key, or endpoint.

    This is the mechanical enforcement of the project's free-to-run promise. If someone
    reintroduces a hosted evaluation backend, this fails before it reaches main.
    """
    blob = yaml.safe_dump(evals_config).lower()
    for forbidden in ("api_key", "apikey", "token", "endpoint", "subscription", "account"):
        assert forbidden not in blob, f"eval config references {forbidden!r}"


def test_reports_carry_traceability_metadata(evals_config):
    reporting = evals_config["reporting"]
    assert reporting["include_config_hash"] is True
    assert reporting["include_machine_spec"] is True
