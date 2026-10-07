import typing
import uuid

import pytest
import tokenizers
import tokenizers.models
import tokenizers.pre_tokenizers

from study_rag.adapters.model_support import EmbeddingInputFormatter
from study_rag.adapters.tokenizer import HuggingFaceTokenizer
from study_rag.domain import entities, errors


FIRST_PAGE_NUMBER: typing.Final = 2
LAST_PAGE_NUMBER: typing.Final = 4
WINDOW_TOKEN_LIMIT: typing.Final = 24
WINDOW_BYTE_LIMIT: typing.Final = 20
EXPECTED_CHUNK_COUNT: typing.Final = 2
FORMATTED_BYTE_LIMIT: typing.Final = 90


def build_byte_tokenizer() -> tokenizers.Tokenizer:
    alphabet_symbols = tokenizers.pre_tokenizers.ByteLevel.alphabet()
    tokenizer_engine = tokenizers.Tokenizer(
        tokenizers.models.BPE(
            vocab={"[UNK]": 0, **{character: position for position, character in enumerate(alphabet_symbols, start=1)}},
            merges=[],
            unk_token="[UNK]",  # noqa: S106 -- tokenizer unknown-symbol marker, not authentication.
        )
    )
    tokenizer_engine.pre_tokenizer = tokenizers.pre_tokenizers.ByteLevel(add_prefix_space=False)
    return tokenizer_engine


async def test_chunks_preserve_unicode_tail_and_pages_with_byte_budget() -> None:
    text_tokenizer = HuggingFaceTokenizer(
        tokenizer_engine=build_byte_tokenizer(),
        chunk_tokens=24,
        overlap_tokens=0,
        content_byte_limit=20,
        formatted_byte_limit=70,
        full_token_limit=100,
    )
    await text_tokenizer.initialize_tokenizer()
    original_text = "Русский 🧪 é 漢字\n" * 12 + "TAIL🦉"
    second_page = "A second PDF page."
    extracted_document = entities.ExtractedDocument(
        text_sections=(
            entities.TextSection(content_text=original_text, page_number=2),
            entities.TextSection(content_text=second_page, page_number=4),
        ),
        page_count=4,
    )
    tenant_identifier = uuid.uuid4()
    document_identifier = uuid.uuid4()

    chunk_records = await text_tokenizer.split_sections(
        tenant_identifier=tenant_identifier,
        document_identifier=document_identifier,
        embedding_profile="testing-byte-profile",
        extracted_document=extracted_document,
        filename_text="notes",
    )

    first_chunks = tuple(chunk_record for chunk_record in chunk_records if chunk_record.page_start == FIRST_PAGE_NUMBER)
    assert "".join(chunk_record.content_text for chunk_record in first_chunks) == original_text
    assert (
        "".join(
            chunk_record.content_text for chunk_record in chunk_records if chunk_record.page_start == LAST_PAGE_NUMBER
        )
        == second_page
    )
    assert all(
        chunk_record.token_count <= WINDOW_TOKEN_LIMIT and len(chunk_record.content_text.encode()) <= WINDOW_BYTE_LIMIT
        for chunk_record in chunk_records
    )
    assert all(chunk_record.page_start == chunk_record.page_end for chunk_record in chunk_records)
    assert all("�" not in chunk_record.content_text for chunk_record in chunk_records)


async def test_chunk_overlap_advances_and_ids_are_reproducible() -> None:
    text_tokenizer = HuggingFaceTokenizer(
        tokenizer_engine=build_byte_tokenizer(),
        chunk_tokens=32,
        overlap_tokens=8,
        content_byte_limit=28,
        formatted_byte_limit=120,
        full_token_limit=160,
    )
    await text_tokenizer.initialize_tokenizer()
    original_text = "0123456789 abcdefghijklmnopqrstuvwxyz TAIL"
    tenant_identifier: typing.Final = uuid.uuid4()
    document_identifier: typing.Final = uuid.uuid4()
    extracted_document: typing.Final = entities.ExtractedDocument(
        text_sections=(entities.TextSection(content_text=original_text, page_number=None),), page_count=None
    )

    first_chunks = await text_tokenizer.split_sections(
        tenant_identifier=tenant_identifier,
        document_identifier=document_identifier,
        embedding_profile="test-profile",
        extracted_document=extracted_document,
        filename_text="notes",
    )
    second_chunks = await text_tokenizer.split_sections(
        tenant_identifier=tenant_identifier,
        document_identifier=document_identifier,
        embedding_profile="test-profile",
        extracted_document=extracted_document,
        filename_text="notes",
    )

    assert first_chunks == second_chunks
    assert len(first_chunks) == EXPECTED_CHUNK_COUNT
    assert first_chunks[0].content_text[-8:] == first_chunks[1].content_text[:8]
    assert first_chunks[-1].content_text.endswith("TAIL")
    assert tuple(chunk_record.ordinal_value for chunk_record in first_chunks) == (0, 1)


async def test_full_prefix_budget_is_checked_with_unicode_title() -> None:
    text_tokenizer = HuggingFaceTokenizer(
        tokenizer_engine=build_byte_tokenizer(),
        chunk_tokens=64,
        overlap_tokens=8,
        content_byte_limit=64,
        formatted_byte_limit=90,
        full_token_limit=100,
    )
    await text_tokenizer.initialize_tokenizer()
    extracted_document = entities.ExtractedDocument(
        text_sections=(entities.TextSection(content_text="abcdef " * 30, page_number=None),), page_count=None
    )
    input_formatter = EmbeddingInputFormatter()

    chunk_records = await text_tokenizer.split_sections(
        tenant_identifier=uuid.uuid4(),
        document_identifier=uuid.uuid4(),
        embedding_profile="test-budget",
        extracted_document=extracted_document,
        filename_text="Учебник",
    )

    assert all(
        len(input_formatter.format_document(content_text=chunk_record.content_text, filename_text="Учебник").encode())
        <= FORMATTED_BYTE_LIMIT
        for chunk_record in chunk_records
    )


async def test_chunk_count_cap_rejects_excessive_windows() -> None:
    text_tokenizer = HuggingFaceTokenizer(
        tokenizer_engine=build_byte_tokenizer(),
        chunk_tokens=10,
        overlap_tokens=0,
        content_byte_limit=10,
        formatted_byte_limit=100,
        full_token_limit=100,
        maximum_chunks=1,
    )
    await text_tokenizer.initialize_tokenizer()
    extracted_document = entities.ExtractedDocument(
        text_sections=(entities.TextSection(content_text="a" * 50, page_number=None),), page_count=None
    )

    with pytest.raises(errors.InvalidInputError) as raised_error:
        await text_tokenizer.split_sections(
            tenant_identifier=uuid.uuid4(),
            document_identifier=uuid.uuid4(),
            embedding_profile="test",
            extracted_document=extracted_document,
            filename_text="notes",
        )
    assert raised_error.value.error_code == "too_many_chunks"
