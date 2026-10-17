from financial_analyst.agents.citations import check_filing_citations


def test_known_filing_reference_passes_and_stage1_sentence_needs_no_citation():
    assert check_filing_citations(
        "The filing says demand remained strong [FILING 1]. Revenue grew 9.9%.",
        ["FILING 1"],
    ) == []


def test_unknown_filing_reference_is_reported():
    issues = check_filing_citations(
        "Demand improved [FILING 2].", ["FILING 1"]
    )
    assert len(issues) == 1
    assert issues[0].kind == "unknown_filing_reference"


def test_explicit_filing_claim_without_reference_is_reported():
    issues = check_filing_citations(
        "The annual report identifies supply constraints as a risk factor.",
        ["FILING 1"],
    )
    assert len(issues) == 1
    assert issues[0].kind == "missing_filing_reference"


def test_both_unknown_and_missing_references_are_reported():
    issues = check_filing_citations(
        "[FILING 8] indicates risk factors remain. The filing says demand is stable.",
        ["FILING 1"],
    )
    assert {issue.kind for issue in issues} == {
        "unknown_filing_reference", "missing_filing_reference"
    }
