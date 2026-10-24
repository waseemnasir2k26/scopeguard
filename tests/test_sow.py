import zipfile

from scopeguard.sow import load_sow, load_text, parse_sow

MD = """# SOW: Demo

Title blurb that is not a clause.

## Deliverables
- A four-page website with a contact form.
- Basic on-page SEO.

## Revisions
Three rounds of revisions are included.

## Out of scope
- Hosting and domain fees.
- Photography.

## Timeline
- Delivery within 6 weeks of kickoff.
"""


def test_clause_splitter_kinds_and_ids():
    sow = parse_sow(MD)
    kinds = [(c.id, c.kind) for c in sow.clauses]
    assert kinds == [("C1", "deliverable"), ("C2", "deliverable"), ("C3", "revision"),
                     ("C4", "exclusion"), ("C5", "exclusion"), ("C6", "timeline")]
    assert not any("blurb" in c.text for c in sow.clauses)
    assert sow.revision_limit == 3 and sow.revision_clause == "C3"


def test_numbered_clauses_keep_the_sow_reference():
    sow = parse_sow("## Scope\n1.1 Five pages are included.\n1.2 Hosting is not included.\n")
    assert [c.ref for c in sow.clauses] == ["1.1", "1.2"]
    assert sow.clauses[1].kind == "exclusion"
    assert sow.clauses[0].cite() == "C1 (SOW 1.1)"


def test_exclusion_detected_from_wording_even_under_deliverables_heading():
    sow = parse_sow("## Deliverables\n- A site.\n- Translation is not included.\n")
    assert sow.clauses[1].kind == "exclusion"


def test_revision_limit_variants():
    assert parse_sow("## R\n- Up to 3 revisions are included.").revision_limit == 3
    assert parse_sow("## R\n- Two (2) rounds of revisions.").revision_limit == 2
    assert parse_sow("## R\n- The fee covers one round of changes and edits.").revision_limit == 1
    assert parse_sow("## R\n- Unlimited tweaks within reason.").revision_limit is None


def test_plain_paragraph_sow_is_split_into_sentences():
    sow = parse_sow("We will build a website. Two rounds of revisions are included. Hosting is excluded.")
    assert len(sow.clauses) == 3
    assert sow.revision_limit == 2


def test_example_sow(examples):
    sow = load_sow(examples / "sow.md")
    assert sow.revision_limit == 2
    assert {c.kind for c in sow.clauses} >= {"deliverable", "exclusion", "revision", "timeline"}
    assert sum(c.kind == "exclusion" for c in sow.clauses) == 7


def test_docx(tmp_path):
    body = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Exclusions</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Hosting fees are not included.</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Revisions</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>Two rounds of revisions included.</w:t></w:r></w:p>'
            '</w:body></w:document>')
    p = tmp_path / "sow.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", body)
    text = load_text(p)
    assert "Hosting fees" in text
    sow = parse_sow(text)
    assert sow.revision_limit == 2 and sow.clauses[0].kind == "exclusion"


def test_pdf(tmp_path):
    import pytest
    pytest.importorskip("pypdf")
    stream = b"BT /F1 12 Tf 72 720 Td (Hosting fees are not included.) Tj ET"
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
           b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
           b"4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream endobj\n"
           b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R/Size 6>>\nstartxref\n0\n%%EOF")
    p = tmp_path / "sow.pdf"
    p.write_bytes(pdf)
    assert "Hosting fees are not included" in load_text(p)
