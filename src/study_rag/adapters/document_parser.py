"""Bounded UTF-8 and page-aware PDF extraction; image-only pages are never OCRed."""

import asyncio
import io
import typing

import pypdf
import pypdf.errors

from study_rag.domain import entities, errors


MAXIMUM_PAGE_STREAM_BYTES: typing.Final = 20_000_000


@typing.final
class PypdfDocumentParser:
    def __init__(self, *, maximum_pages: int = 5000, maximum_characters: int = 20_000_000) -> None:
        if maximum_pages < 1 or maximum_characters < 1:
            raise ValueError("Extraction limits must be positive")
        self._maximum_pages: typing.Final = maximum_pages
        self._maximum_characters: typing.Final = maximum_characters
        self._parsing_lock: typing.Final = asyncio.Lock()

    async def extract_document(self, *, content_bytes: bytes, media_type: str) -> entities.ExtractedDocument:
        async with self._parsing_lock:
            parsing_task: typing.Final = asyncio.create_task(
                asyncio.to_thread(self._extract_document, content_bytes=content_bytes, media_type=media_type)
            )
            try:
                return await asyncio.shield(parsing_task)
            except asyncio.CancelledError:
                await parsing_task
                raise

    def _extract_document(self, *, content_bytes: bytes, media_type: str) -> entities.ExtractedDocument:
        if media_type in {"text/plain", "text/markdown"}:
            return self._extract_utf8(content_bytes=content_bytes)
        if media_type == "application/pdf":
            return self._extract_pdf(content_bytes=content_bytes)
        raise errors.InvalidInputError(
            error_code="unsupported_document_type",
            message_text="Only UTF-8 TXT, Markdown and text-layer PDF files are supported.",
        )

    def _extract_utf8(self, *, content_bytes: bytes) -> entities.ExtractedDocument:
        try:
            content_text: typing.Final = content_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as encoding_error:
            raise errors.InvalidInputError(
                error_code="invalid_utf8", message_text="The text document must use UTF-8 encoding."
            ) from encoding_error
        self._validate_text(content_text=content_text)
        return entities.ExtractedDocument(
            text_sections=(entities.TextSection(content_text=content_text, page_number=None),), page_count=None
        )

    def _extract_pdf(self, *, content_bytes: bytes) -> entities.ExtractedDocument:
        if not content_bytes.startswith(b"%PDF-"):
            raise errors.InvalidInputError(
                error_code="invalid_pdf", message_text="The uploaded file is not a valid PDF."
            )
        try:
            document_reader: typing.Final = pypdf.PdfReader(io.BytesIO(content_bytes), strict=False)
            if document_reader.is_encrypted:
                raise errors.InvalidInputError(
                    error_code="encrypted_pdf", message_text="Password-protected PDFs are not supported."
                )
            page_count: typing.Final = len(document_reader.pages)
            if page_count > self._maximum_pages:
                raise errors.InvalidInputError(
                    error_code="too_many_pdf_pages", message_text="The PDF exceeds its page limit."
                )
            text_sections: typing.Final[list[entities.TextSection]] = []
            total_characters = 0
            for page_number, document_page in enumerate(document_reader.pages, start=1):
                # Decompressed streams can be much larger than the uploaded file.
                content_stream = document_page.get_contents()
                if content_stream is not None and len(content_stream.get_data()) > MAXIMUM_PAGE_STREAM_BYTES:
                    raise errors.InvalidInputError(
                        error_code="pdf_page_too_large", message_text="A PDF page exceeds its extraction limit."
                    )
                content_text = document_page.extract_text() or ""
                total_characters += len(content_text)
                if total_characters > self._maximum_characters:
                    raise errors.InvalidInputError(
                        error_code="extracted_text_too_large",
                        message_text="The document exceeds its extracted text limit.",
                    )
                if content_text.strip():
                    text_sections.append(entities.TextSection(content_text=content_text, page_number=page_number))
        except (pypdf.errors.PyPdfError, ValueError, TypeError, KeyError, RecursionError) as parsing_error:
            raise errors.InvalidInputError(
                error_code="invalid_pdf", message_text="The PDF text layer could not be read."
            ) from parsing_error
        if not text_sections:
            raise errors.InvalidInputError(
                error_code="no_extractable_text",
                message_text="The PDF has no extractable text layer; scanned PDFs require OCR.",
            )
        return entities.ExtractedDocument(text_sections=tuple(text_sections), page_count=page_count)

    def _validate_text(self, *, content_text: str) -> None:
        if not content_text.strip():
            raise errors.InvalidInputError(
                error_code="no_extractable_text", message_text="The document contains no text."
            )
        if len(content_text) > self._maximum_characters:
            raise errors.InvalidInputError(
                error_code="extracted_text_too_large", message_text="The document exceeds its extracted text limit."
            )
