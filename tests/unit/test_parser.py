import io
import unittest.mock

import pypdf
import pypdf.generic
import pytest

from study_rag.adapters.document_parser import PypdfDocumentParser
from study_rag.domain import errors


def build_text_pdf(*, page_texts: tuple[str | None, ...]) -> bytes:
    document_writer = pypdf.PdfWriter()
    for page_text in page_texts:
        document_page = document_writer.add_blank_page(width=300, height=300)
        if page_text is None:
            continue
        font_dictionary = pypdf.generic.DictionaryObject(
            {
                pypdf.generic.NameObject("/Type"): pypdf.generic.NameObject("/Font"),
                pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/Type1"),
                pypdf.generic.NameObject("/BaseFont"): pypdf.generic.NameObject("/Helvetica"),
            }
        )
        document_page[pypdf.generic.NameObject("/Resources")] = pypdf.generic.DictionaryObject(
            {
                pypdf.generic.NameObject("/Font"): pypdf.generic.DictionaryObject(
                    {pypdf.generic.NameObject("/F1"): font_dictionary}
                )
            }
        )
        content_stream = pypdf.generic.DecodedStreamObject()
        content_stream.set_data(f"BT /F1 12 Tf 10 100 Td ({page_text}) Tj ET".encode("ascii"))
        document_page[pypdf.generic.NameObject("/Contents")] = content_stream
    output_stream = io.BytesIO()
    document_writer.write(output_stream)
    return output_stream.getvalue()


@pytest.mark.parametrize("media_type", ["text/plain", "text/markdown"])
async def test_parser_preserves_utf8_bom_text(media_type: str) -> None:
    document_parser = PypdfDocumentParser()
    original_text = "# Производная\nРусский текст 🧪\n"  # noqa: RUF001 -- intentional Cyrillic UTF-8 fixture.

    extracted_document = await document_parser.extract_document(
        content_bytes=b"\xef\xbb\xbf" + original_text.encode(), media_type=media_type
    )

    assert extracted_document.text_sections[0].content_text == original_text
    assert extracted_document.page_count is None


async def test_parser_preserves_pdf_page_numbers_across_blank_page() -> None:
    document_parser = PypdfDocumentParser()
    content_bytes = build_text_pdf(page_texts=("First page", None, "Last page"))

    extracted_document = await document_parser.extract_document(
        content_bytes=content_bytes, media_type="application/pdf"
    )

    assert extracted_document.page_count == 3  # noqa: PLR2004 -- fixture has exactly three PDF pages.
    assert tuple(text_section.page_number for text_section in extracted_document.text_sections) == (1, 3)
    assert "Last page" in extracted_document.text_sections[-1].content_text


async def test_pdf_words_follow_visual_lines_and_preserve_paragraphs() -> None:
    document_reader = pypdf.PdfReader(io.BytesIO(build_text_pdf(page_texts=("Seed",))))
    document_page = document_reader.pages[0]
    content_stream = pypdf.generic.DecodedStreamObject()
    content_stream.set_data(
        b"BT /F1 12 Tf 1 0 0 1 10 220 Tm (Format) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 60 220 Tm (data:) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 10 170 Tm (Column) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 150 170 Tm (Description) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 10 150 Tm (query) Tj ET\n"
        b"BT /F1 12 Tf 1 0 0 1 150 150 Tm (Search query text) Tj ET\n"
    )
    document_page[pypdf.generic.NameObject("/Contents")] = content_stream
    document_writer = pypdf.PdfWriter()
    document_writer.add_page(document_page)
    output_stream = io.BytesIO()
    document_writer.write(output_stream)
    extracted_document = await PypdfDocumentParser().extract_document(
        content_bytes=output_stream.getvalue(), media_type="application/pdf"
    )
    extracted_text = extracted_document.text_sections[0].content_text
    assert extracted_text.splitlines()[0].split() == ["Format", "data:"]
    assert "\n\nColumn" in extracted_text
    assert any(
        "Column" in content_line and "Description" in content_line for content_line in extracted_text.splitlines()
    )
    assert any(
        "query" in content_line and "Search query text" in content_line for content_line in extracted_text.splitlines()
    )
    assert all(not content_line.startswith(" ") for content_line in extracted_text.splitlines())


@pytest.mark.parametrize("layout_failure", [ValueError("Unsupported layout"), ""])
async def test_pdf_layout_failure_keeps_plain_text_fallback(layout_failure: ValueError | str) -> None:
    with unittest.mock.patch.object(
        pypdf.PageObject, "extract_text", side_effect=[layout_failure, "Readable fallback"]
    ):
        extracted_document = await PypdfDocumentParser().extract_document(
            content_bytes=build_text_pdf(page_texts=("Seed",)), media_type="application/pdf"
        )
    assert extracted_document.text_sections[0].content_text == "Readable fallback"


@pytest.mark.parametrize(
    ("content_bytes", "media_type", "error_code"),
    [
        (b"\xff", "text/plain", "invalid_utf8"),
        (b" ", "text/plain", "no_extractable_text"),
        (b"not PDF", "application/pdf", "invalid_pdf"),
    ],
)
async def test_parser_rejects_invalid_or_empty_content(content_bytes: bytes, media_type: str, error_code: str) -> None:
    document_parser = PypdfDocumentParser()

    with pytest.raises(errors.InvalidInputError) as raised_error:
        await document_parser.extract_document(content_bytes=content_bytes, media_type=media_type)
    assert raised_error.value.error_code == error_code


async def test_parser_rejects_scan_without_text_layer() -> None:
    document_parser = PypdfDocumentParser()

    with pytest.raises(errors.InvalidInputError) as raised_error:
        await document_parser.extract_document(
            content_bytes=build_text_pdf(page_texts=(None,)), media_type="application/pdf"
        )
    assert raised_error.value.error_code == "no_extractable_text"


async def test_parser_enforces_pdf_page_and_text_caps() -> None:
    content_bytes = build_text_pdf(page_texts=("First page", "Last page"))
    limited_pages = PypdfDocumentParser(maximum_pages=1)
    limited_text = PypdfDocumentParser(maximum_characters=4)

    with pytest.raises(errors.InvalidInputError) as pages_error:
        await limited_pages.extract_document(content_bytes=content_bytes, media_type="application/pdf")
    with pytest.raises(errors.InvalidInputError) as text_error:
        await limited_text.extract_document(content_bytes=b"12345", media_type="text/plain")
    assert pages_error.value.error_code == "too_many_pdf_pages"
    assert text_error.value.error_code == "extracted_text_too_large"
