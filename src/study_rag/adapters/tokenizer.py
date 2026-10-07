"""HF tokenizer profiles and lossless character-boundary, page-aware chunk windows."""

import asyncio
import bisect
import pathlib
import typing
import uuid

import huggingface_hub
import huggingface_hub.errors
import tokenizers

from study_rag.adapters.model_support import EmbeddingInputFormatter
from study_rag.domain import entities, errors


@typing.final
class HuggingFaceTokenizer:
    def __init__(
        self,
        *,
        model_identifier: str = "Qwen/Qwen3.5-4B",
        cache_directory: pathlib.Path | None = None,
        chunk_tokens: int = 512,
        overlap_tokens: int = 64,
        content_byte_limit: int | None = 1536,
        formatted_byte_limit: int | None = 1900,
        full_token_limit: int = 2048,
        maximum_chunks: int = 100_000,
        tokenizer_engine: tokenizers.Tokenizer | None = None,
    ) -> None:
        if not 0 <= overlap_tokens < chunk_tokens or full_token_limit < chunk_tokens or maximum_chunks < 1:
            raise ValueError("Invalid tokenizer window limits")
        if any(
            limit_value is not None and limit_value < 1 for limit_value in (content_byte_limit, formatted_byte_limit)
        ):
            raise ValueError("Byte limits must be positive when supplied")
        self._model_identifier: typing.Final = model_identifier
        self._cache_directory: typing.Final = cache_directory
        self._chunk_tokens: typing.Final = chunk_tokens
        self._overlap_tokens: typing.Final = overlap_tokens
        self._content_byte_limit: typing.Final = content_byte_limit
        self._formatted_byte_limit: typing.Final = formatted_byte_limit
        self._full_token_limit: typing.Final = full_token_limit
        self._maximum_chunks: typing.Final = maximum_chunks
        self._input_formatter: typing.Final = EmbeddingInputFormatter()
        self._tokenizing_lock: typing.Final = asyncio.Lock()
        self._tokenizer_engine = tokenizer_engine

    @property
    def model_identifier(self) -> str:
        return self._model_identifier

    async def initialize_tokenizer(self) -> None:
        async with self._tokenizing_lock:
            if self._tokenizer_engine is None:
                self._tokenizer_engine = await asyncio.to_thread(self._load_tokenizer)
            self._tokenizer_engine.no_truncation()
            self._tokenizer_engine.no_padding()

    async def close_tokenizer(self) -> None:
        async with self._tokenizing_lock:
            self._tokenizer_engine = None

    def count_tokens(self, *, content_text: str) -> int:
        tokenizer_engine: typing.Final = self._require_tokenizer()
        # The tokenizer JSON's post-processor determines the profile's special tokens.
        return len(tokenizer_engine.encode(content_text, add_special_tokens=True).ids)

    async def split_sections(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        embedding_profile: str,
        extracted_document: entities.ExtractedDocument,
        filename_text: str,
    ) -> tuple[entities.ChunkRecord, ...]:
        async with self._tokenizing_lock:
            splitting_task: typing.Final = asyncio.create_task(
                asyncio.to_thread(
                    self._split_sections,
                    tenant_identifier=tenant_identifier,
                    document_identifier=document_identifier,
                    embedding_profile=embedding_profile,
                    extracted_document=extracted_document,
                    filename_text=filename_text,
                )
            )
            try:
                return await asyncio.shield(splitting_task)
            except asyncio.CancelledError:
                await splitting_task
                raise

    def _load_tokenizer(self) -> tokenizers.Tokenizer:
        try:
            tokenizer_path: typing.Final = huggingface_hub.hf_hub_download(
                repo_id=self._model_identifier, filename="tokenizer.json", cache_dir=self._cache_directory
            )
            return tokenizers.Tokenizer.from_file(tokenizer_path)
        except (huggingface_hub.errors.HfHubHTTPError, OSError, ValueError, RuntimeError) as loading_error:
            raise errors.DependencyUnavailableError(
                error_code="tokenizer_unavailable",
                message_text=(
                    "The HF tokenizer is unavailable. Gated models require prior local HF authorization "
                    "and accepted access terms."
                ),
            ) from loading_error

    def _require_tokenizer(self) -> tokenizers.Tokenizer:
        if self._tokenizer_engine is None:
            raise errors.DependencyUnavailableError(
                error_code="tokenizer_not_initialized", message_text="The tokenizer has not been initialized."
            )
        return self._tokenizer_engine

    def _split_sections(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        embedding_profile: str,
        extracted_document: entities.ExtractedDocument,
        filename_text: str,
    ) -> tuple[entities.ChunkRecord, ...]:
        chunk_records: typing.Final[list[entities.ChunkRecord]] = []
        for text_section in extracted_document.text_sections:
            if not text_section.content_text.strip():
                continue
            for content_text in self._split_content(
                content_text=text_section.content_text, filename_text=filename_text
            ):
                if len(chunk_records) >= self._maximum_chunks:
                    raise errors.InvalidInputError(
                        error_code="too_many_chunks", message_text="The document exceeds its chunk limit."
                    )
                ordinal_value = len(chunk_records)
                chunk_records.append(
                    entities.ChunkRecord(
                        chunk_identifier=uuid.uuid5(document_identifier, f"{embedding_profile}:{ordinal_value}"),
                        document_identifier=document_identifier,
                        tenant_identifier=tenant_identifier,
                        ordinal_value=ordinal_value,
                        content_text=content_text,
                        token_count=self.count_tokens(content_text=content_text),
                        page_start=text_section.page_number,
                        page_end=text_section.page_number,
                    )
                )
        if not chunk_records:
            raise errors.InvalidInputError(
                error_code="no_extractable_text", message_text="The document contains no tokenizable text."
            )
        return tuple(chunk_records)

    def _split_content(self, *, content_text: str, filename_text: str) -> typing.Iterator[str]:
        tokenizer_engine: typing.Final = self._require_tokenizer()
        full_encoding: typing.Final = tokenizer_engine.encode(content_text, add_special_tokens=False)
        offset_ends: typing.Final = [offset_pair[1] for offset_pair in full_encoding.offsets]
        start_character = 0
        while start_character < len(content_text):
            token_position = bisect.bisect_right(offset_ends, start_character)
            next_position = token_position + self._chunk_tokens
            candidate_end = offset_ends[next_position - 1] if next_position < len(offset_ends) else len(content_text)
            candidate_end = max(candidate_end, start_character + 1)
            end_character = self._bound_window(
                content_text=content_text,
                filename_text=filename_text,
                start_character=start_character,
                candidate_end=candidate_end,
            )
            chunk_content = content_text[start_character:end_character]
            yield chunk_content
            if end_character == len(content_text):
                break
            window_encoding = tokenizer_engine.encode(chunk_content, add_special_tokens=False)
            next_character = end_character
            if self._overlap_tokens and len(window_encoding.ids) > self._overlap_tokens:
                next_character = start_character + window_encoding.offsets[-self._overlap_tokens][0]
            # Byte windows can be shorter than overlap; always advance without dropping unindexed text.
            start_character = next_character if start_character < next_character < end_character else end_character

    def _bound_window(self, *, content_text: str, filename_text: str, start_character: int, candidate_end: int) -> int:
        if self._window_fits(content_text=content_text[start_character:candidate_end], filename_text=filename_text):
            return candidate_end
        lower_position = start_character
        upper_position = candidate_end
        while lower_position + 1 < upper_position:
            middle_position = (lower_position + upper_position) // 2
            if self._window_fits(
                content_text=content_text[start_character:middle_position], filename_text=filename_text
            ):
                lower_position = middle_position
            else:
                upper_position = middle_position
        if lower_position == start_character:
            raise errors.InvalidInputError(
                error_code="chunk_budget_too_small",
                message_text="The tokenizer budget cannot hold one complete Unicode character and its prefix.",
            )
        return lower_position

    def _window_fits(self, *, content_text: str, filename_text: str) -> bool:
        if self._content_byte_limit is not None and len(content_text.encode("utf-8")) > self._content_byte_limit:
            return False
        formatted_text: typing.Final = self._input_formatter.format_document(
            content_text=content_text, filename_text=filename_text
        )
        if self._formatted_byte_limit is not None and len(formatted_text.encode("utf-8")) > self._formatted_byte_limit:
            return False
        return (
            self.count_tokens(content_text=content_text) <= self._chunk_tokens
            and self.count_tokens(content_text=formatted_text) <= self._full_token_limit
        )
