"""Word export of a Funding AI result."""
import io

from docx import Document

from tender_app import funding_export as fx


def test_docx_has_headings_bullets_and_paragraphs():
    data = fx.build_docx("Grant - Application draft", "## Need\nWe build software.\nYou need to add:\n- Turnover\n\n- Bank details")
    doc = Document(io.BytesIO(data))
    kinds = [(p.style.name, p.text) for p in doc.paragraphs]
    assert kinds[0] == ("Heading 1", "Grant - Application draft")
    assert ("Heading 2", "Need") in kinds and ("Normal", "We build software.") in kinds
    assert ("List Bullet", "Turnover") in kinds and ("List Bullet", "Bank details") in kinds
    assert len(kinds) == 6, "blank lines are skipped"


def test_file_name_is_safe():
    assert fx.file_name("Frontier AI: SME Champions - Summary") == "Frontier_AI_SME_Champions_-_Summary.docx"
    assert fx.file_name("../../etc/passwd") == "etcpasswd.docx"
    assert fx.file_name("") == "grant.docx"
