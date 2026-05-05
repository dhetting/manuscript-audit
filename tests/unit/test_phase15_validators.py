from manuscript_audit.schemas.artifacts import ParsedManuscript
from manuscript_audit.schemas.routing import ManuscriptClassification
from manuscript_audit.validators.core import (
    validate_code_run_example,
    validate_data_versioning,
    validate_model_card_presence,
    validate_negative_control_reporting,
    validate_uncertainty_terminology_clarity,
)


def _base_parsed(full_text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="phase15-test",
        source_path="synthetic",
        source_format="markdown",
        title="Phase 15 test",
        abstract="",
        sections=[],
        full_text=full_text,
    )


def _empirical_classification() -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="data_science",
        paper_type="empirical_paper",
        recommended_stack="standard",
    )


def test_missing_model_card_fires_when_model_described() -> None:
    parsed = _base_parsed("We present a new neural network model to predict outcomes.")
    cl = _empirical_classification()
    result = validate_model_card_presence(parsed, cl)
    assert any(f.code == "missing-model-card" for f in result.findings)


def test_missing_negative_control_fires_for_causal_claims() -> None:
    parsed = _base_parsed(
        "We estimate the treatment effect using difference-in-differences "
        "(DiD) and report the results."
    )
    cl = _empirical_classification()
    result = validate_negative_control_reporting(parsed, cl)
    assert any(f.code == "missing-negative-control" for f in result.findings)


def test_missing_uncertainty_quantification_detects_plain_uncertainty() -> None:
    parsed = _base_parsed("We discuss uncertainty in our estimates and the limits of the analysis.")
    cl = _empirical_classification()
    result = validate_uncertainty_terminology_clarity(parsed, cl)
    assert any(f.code == "missing-uncertainty-quantification" for f in result.findings)


def test_missing_data_version_detects_dataset_mentions_without_version() -> None:
    parsed = _base_parsed(
        "We use the dataset provided by the repository to train and evaluate models."
    )
    cl = _empirical_classification()
    result = validate_data_versioning(parsed, cl)
    assert any(f.code == "missing-data-version" for f in result.findings)


def test_missing_code_run_example_detects_repo_without_run_instructions() -> None:
    parsed = _base_parsed(
        "Code is available at https://github.com/example/repo but no instructions are provided."
    )
    cl = _empirical_classification()
    result = validate_code_run_example(parsed, cl)
    assert any(f.code == "missing-code-run-example" for f in result.findings)
