"""Bounded query planning, diverse section retrieval and quote-backed overviews."""

# ruff: noqa: RUF001 -- Russian user-facing copy and language detection.

import asyncio
import dataclasses
import logging
import re
import time
import typing
import unicodedata
import uuid

from study_rag.domain import entities, errors, ports


search_logger: typing.Final = logging.getLogger(__name__)
minimum_outline_entries: typing.Final = 3
outline_ordinal_limit: typing.Final = 80
page_size: typing.Final = 256
evidence_batch_size: typing.Final = 4
maximum_catalog_items: typing.Final = 160
query_stop_words: typing.Final = frozenset(
    [
        "list",
        "all",
        "book",
        "books",
        "project",
        "material",
        "materials",
        "document",
        "documents",
        "mentioned",
        "describe",
        "explain",
        "what",
        "which",
        "from",
        "with",
        "this",
        "that",
        "other",
        "more",
        "find",
        "search",
        "about",
        "known",
        "there",
        "are",
        "the",
        "and",
        "for",
    ]
)


def normalize_text(content_text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", content_text).casefold()))


def normalize_name(content_text: str) -> str:
    normalized_name = " ".join(
        re.sub(r"([a-z]{3,})s$", r"\1", name_part)
        for name_part in re.findall(r"\w+(?:[+*]+)?", unicodedata.normalize("NFKC", content_text).casefold())
    )
    return re.sub(r"\b(?:data )?structure$", "", normalized_name).strip()


def requests_overview(question_text: str) -> bool:
    return bool(
        re.search(
            r"(?:перечисл|обзор).*?(?:книг|материал|проект)|полн\w* (?:спис|переч)|"
            r"(?:все|всех|весь|всю)\b.*(?:книг|материал|структур|тем|понят|раздел)|"
            r"\b(?:overview|catalog|summari[sz]e|list all|all .* (?:book|material|document))\b",
            question_text.casefold(),
        )
    )


def requests_expansion(question_text: str) -> bool:
    return bool(
        re.search(
            r"(?:еще|ещё).*(?:какие|есть|най|структур|пример|тем|понят)|"
            r"(?:найди|поищи|перечисл\w*|покаж\w*).*(?:еще|ещё|друг)|"
            r"друг\w* раздел|\b(?:what else|find more|other sections|additional items)\b",
            question_text.casefold(),
        )
    )


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class SearchResult:
    search_plan: entities.SearchPlan
    context_chunks: tuple[entities.RetrievedChunk, ...]
    scanned_count: int = 0
    scan_complete: bool = False
    section_count: int = 0


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AdaptiveSearch:
    document_repository: ports.DocumentRepository
    embedding_provider: ports.EmbeddingProvider
    vector_store: ports.VectorStore
    answer_generator: ports.AnswerGenerator
    retrieval_limit: int = 6
    retrieval_candidates: int = 24
    minimum_similarity: float = 0.35
    overview_chunk_limit: int = 40
    scan_chunk_limit: int = 20000
    maximum_search_passes: int = 3

    async def search_context(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        project_identifier: uuid.UUID,
        question_text: str,
        conversation_history: tuple[entities.MessageRecord, ...],
    ) -> SearchResult:
        document_identifiers = await self.document_repository.fetch_ready_identifiers(
            tenant_identifier=tenant_identity.user_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
            project_identifier=project_identifier,
        )
        fallback_plan = entities.SearchPlan(
            question_text=question_text,
            scope_name="focused",
            search_queries=(question_text,),
            search_keywords=(),
        )
        if not document_identifiers:
            return SearchResult(search_plan=fallback_plan, context_chunks=())
        search_plan = await self.answer_generator.plan_search(
            question_text=question_text,
            conversation_history=conversation_history,
        )
        # Explicit broad scope must survive a model's accidental topic narrowing.
        if requests_overview(question_text) and not requests_expansion(question_text):
            search_plan = dataclasses.replace(search_plan, scope_name="overview", question_text=question_text)
        elif requests_expansion(question_text):
            previous_broad = next(
                (
                    history_message.content_text
                    for history_message in reversed(conversation_history)
                    if history_message.role_name == "user" and requests_overview(history_message.content_text)
                ),
                None,
            )
            search_plan = dataclasses.replace(
                search_plan,
                scope_name="expansion",
                question_text=f"{previous_broad}\n{question_text}" if previous_broad else search_plan.question_text,
            )
        document_identifiers = await self._select_documents(
            tenant_identifier=tenant_identity.user_identifier,
            document_identifiers=document_identifiers,
            question_text=search_plan.question_text,
        )
        search_logger.info(
            "Search plan project=%s breadth=%s queries=%s",
            project_identifier,
            search_plan.scope_name,
            search_plan.search_queries,
        )
        if search_plan.scope_name in {"overview", "expansion"}:
            return await self._scan_sections(
                tenant_identity=tenant_identity,
                project_identifier=project_identifier,
                search_plan=search_plan,
                document_identifiers=document_identifiers,
                conversation_history=conversation_history,
            )
        return await self._collect_queries(
            tenant_identity=tenant_identity,
            project_identifier=project_identifier,
            search_plan=search_plan,
            document_identifiers=document_identifiers,
            conversation_history=conversation_history,
        )

    async def _collect_queries(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        project_identifier: uuid.UUID,
        search_plan: entities.SearchPlan,
        document_identifiers: tuple[uuid.UUID, ...],
        conversation_history: tuple[entities.MessageRecord, ...],
    ) -> SearchResult:
        selected_chunks: dict[uuid.UUID, entities.RetrievedChunk] = {}
        query_buckets: list[tuple[entities.RetrievedChunk, ...]] = []
        context_limit = self.retrieval_limit * (2 if search_plan.scope_name == "comparison" else 1)
        for query_text in search_plan.search_queries[: self.maximum_search_passes]:
            query_chunks = await self._search_query(
                tenant_identity=tenant_identity,
                project_identifier=project_identifier,
                document_identifiers=document_identifiers,
                query_text=query_text,
            )
            query_buckets.append(query_chunks[: self.retrieval_limit])
            # Reserve room for each comparison subject, rather than one subject filling the prompt.
            for retrieved_chunk in query_chunks[: self.retrieval_limit]:
                selected_chunks.setdefault(retrieved_chunk.chunk_record.chunk_identifier, retrieved_chunk)
            if search_plan.scope_name == "focused" and len(selected_chunks) >= self.retrieval_limit:
                break
        if search_plan.scope_name == "comparison":
            selected_chunks.clear()
            for rank_index in range(self.retrieval_limit):
                for query_bucket in query_buckets:
                    if rank_index < len(query_bucket):
                        retrieved_chunk = query_bucket[rank_index]
                        selected_chunks.setdefault(retrieved_chunk.chunk_record.chunk_identifier, retrieved_chunk)
        if not selected_chunks:
            additional = await self._scan_sections(
                tenant_identity=tenant_identity,
                project_identifier=project_identifier,
                search_plan=search_plan,
                document_identifiers=document_identifiers,
                conversation_history=conversation_history,
            )
            selected_chunks.update(
                {
                    retrieved_chunk.chunk_record.chunk_identifier: retrieved_chunk
                    for retrieved_chunk in additional.context_chunks
                }
            )
        return SearchResult(
            search_plan=search_plan, context_chunks=tuple(selected_chunks.values())[: min(context_limit, 48)]
        )

    async def _select_documents(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifiers: tuple[uuid.UUID, ...],
        question_text: str,
    ) -> tuple[uuid.UUID, ...]:
        documents = tuple(
            [
                await self.document_repository.fetch_document(
                    tenant_identifier=tenant_identifier,
                    document_identifier=document_identifier,
                )
                for document_identifier in document_identifiers
            ]
        )
        named_documents = tuple(
            document.document_identifier
            for document in documents
            if document.filename_text.casefold() in question_text.casefold()
        )
        if named_documents:
            return named_documents
        if re.search(r"книг|\bbook\b", question_text, re.IGNORECASE) and not re.search(
            r"проект|материал|\bproject|\bmaterials",
            question_text,
            re.IGNORECASE,
        ):
            pdf_documents = tuple(
                document.document_identifier for document in documents if document.media_type == "application/pdf"
            )
            if pdf_documents:
                return pdf_documents
        return document_identifiers

    async def _search_query(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        project_identifier: uuid.UUID,
        document_identifiers: tuple[uuid.UUID, ...],
        query_text: str,
    ) -> tuple[entities.RetrievedChunk, ...]:
        query_vector = await self.embedding_provider.embed_question(question_text=query_text)
        vector_matches = await self.vector_store.search_chunks(
            tenant_identifier=tenant_identity.user_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
            question_vector=query_vector,
            document_identifiers=document_identifiers,
            search_limit=self.retrieval_candidates,
        )
        accepted_matches = tuple(
            vector_match for vector_match in vector_matches if vector_match.similarity_score >= self.minimum_similarity
        )
        chunk_records = await self.document_repository.fetch_ready_chunks(
            tenant_identifier=tenant_identity.user_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
            chunk_identifiers=tuple(vector_match.chunk_identifier for vector_match in accepted_matches),
            project_identifier=project_identifier,
        )
        by_identifier = {chunk_record.chunk_identifier: chunk_record for chunk_record in chunk_records}
        documents: dict[uuid.UUID, entities.DocumentRecord] = {}
        selected_chunks: list[entities.RetrievedChunk] = []
        for vector_match in accepted_matches:
            chunk_record = by_identifier.get(vector_match.chunk_identifier)
            if chunk_record is None or chunk_record.tenant_identifier != tenant_identity.user_identifier:
                continue
            document = documents.get(chunk_record.document_identifier)
            if document is None:
                try:
                    document = await self.document_repository.fetch_document(
                        tenant_identifier=tenant_identity.user_identifier,
                        document_identifier=chunk_record.document_identifier,
                    )
                except errors.ResourceNotFoundError:
                    # A document may have been deleted while retrieval was in progress.
                    continue
                documents[chunk_record.document_identifier] = document
            if (
                document.state_name == entities.DocumentState.READY
                and document.project_identifier == project_identifier
                and document.embedding_profile == self.embedding_provider.embedding_profile
            ):
                selected_chunks.append(
                    entities.RetrievedChunk(
                        chunk_record=chunk_record,
                        filename_text=document.filename_text,
                        similarity_score=vector_match.similarity_score,
                    )
                )
        return tuple(selected_chunks)

    async def expand_context(
        self,
        *,
        search_result: SearchResult,
        tenant_identity: entities.TenantIdentity,
        project_identifier: uuid.UUID,
        conversation_history: tuple[entities.MessageRecord, ...],
    ) -> SearchResult:
        identifiers = await self.document_repository.fetch_ready_identifiers(
            tenant_identifier=tenant_identity.user_identifier,
            project_identifier=project_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
        )
        identifiers = await self._select_documents(
            tenant_identifier=tenant_identity.user_identifier,
            document_identifiers=identifiers,
            question_text=search_result.search_plan.question_text,
        )
        additional = await self._scan_sections(
            tenant_identity=tenant_identity,
            project_identifier=project_identifier,
            search_plan=search_result.search_plan,
            document_identifiers=identifiers,
            conversation_history=conversation_history,
        )
        retrieved_chunks = {
            retrieved_chunk.chunk_record.chunk_identifier: retrieved_chunk
            for retrieved_chunk in search_result.context_chunks
        }
        for retrieved_chunk in additional.context_chunks:
            retrieved_chunks.setdefault(retrieved_chunk.chunk_record.chunk_identifier, retrieved_chunk)
        return dataclasses.replace(
            search_result, context_chunks=tuple(retrieved_chunks.values())[: min(self.retrieval_limit * 3, 48)]
        )

    async def _scan_sections(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        project_identifier: uuid.UUID,
        search_plan: entities.SearchPlan,
        document_identifiers: tuple[uuid.UUID, ...],
        conversation_history: tuple[entities.MessageRecord, ...],
    ) -> SearchResult:
        section_groups: dict[tuple[uuid.UUID, str], list[tuple[float, entities.RetrievedChunk]]] = {}
        section_names: dict[uuid.UUID, str] = {}
        previous_sources = {
            citation.chunk_identifier
            for history_message in conversation_history
            if history_message.role_name == "assistant"
            for citation in history_message.citation_records
        }
        query_terms = tuple(
            search_term.rstrip("s")
            for search_query in search_plan.search_queries
            for search_term in re.findall(r"[a-z]{4,}", search_query.casefold())
            if search_term not in query_stop_words
        )
        search_terms = tuple(
            dict.fromkeys(
                (
                    *(
                        normalize_text(search_term)
                        for search_term in search_plan.search_keywords
                        if search_term.strip()
                    ),
                    *query_terms,
                )
            )
        )
        scanned_count = 0
        scan_complete = False
        while scanned_count < self.scan_chunk_limit:
            retrieved_chunks = await self.document_repository.list_ready_chunks(
                tenant_identifier=tenant_identity.user_identifier,
                project_identifier=project_identifier,
                embedding_profile=self.embedding_provider.embedding_profile,
                limit_count=min(page_size, self.scan_chunk_limit - scanned_count),
                offset_value=scanned_count,
            )
            if not retrieved_chunks:
                scan_complete = True
                break
            self._rank_sections(
                retrieved_chunks=retrieved_chunks,
                tenant_identifier=tenant_identity.user_identifier,
                document_identifiers=document_identifiers,
                search_plan=search_plan,
                section_groups=section_groups,
                section_names=section_names,
                search_terms=search_terms,
                previous_sources=previous_sources,
            )
            scanned_count += len(retrieved_chunks)
            if len(retrieved_chunks) < page_size:
                scan_complete = scanned_count < self.scan_chunk_limit
                break
            await asyncio.sleep(0)
        # One representative from every region before taking a second from any region.
        sorted_groups = sorted(
            section_groups,
            key=lambda section_key: (
                section_key[1] != "contents",
                -section_groups[section_key][0][0],
                str(section_key),
            ),
        )
        chosen_chunks: list[entities.RetrievedChunk] = [
            candidate[1]
            for section_key in sorted_groups
            if section_key[1] == "contents"
            for candidate in section_groups[section_key]
        ][: self.overview_chunk_limit]
        chosen_chunks.sort(
            key=lambda retrieved_chunk: (
                str(retrieved_chunk.chunk_record.document_identifier),
                retrieved_chunk.chunk_record.ordinal_value,
            )
        )
        for rank_index in range(3):
            for section_key in sorted_groups:
                if (
                    section_key[1] != "contents"
                    and rank_index < len(section_groups[section_key])
                    and len(chosen_chunks) < self.overview_chunk_limit
                ):
                    chosen_chunks.append(section_groups[section_key][rank_index][1])
        search_logger.info(
            "Section search project=%s scanned=%s selected=%s regions=%s complete=%s",
            project_identifier,
            scanned_count,
            len(chosen_chunks),
            len(section_groups),
            scan_complete,
        )
        return SearchResult(
            search_plan=search_plan,
            context_chunks=tuple(chosen_chunks),
            scanned_count=scanned_count,
            scan_complete=scan_complete,
            section_count=min(len(section_groups), len(chosen_chunks)),
        )

    def _rank_sections(
        self,
        *,
        retrieved_chunks: tuple[entities.RetrievedChunk, ...],
        tenant_identifier: uuid.UUID,
        document_identifiers: tuple[uuid.UUID, ...],
        search_plan: entities.SearchPlan,
        section_groups: dict[tuple[uuid.UUID, str], list[tuple[float, entities.RetrievedChunk]]],
        section_names: dict[uuid.UUID, str],
        search_terms: tuple[str, ...],
        previous_sources: set[uuid.UUID],
    ) -> None:
        for retrieved_chunk in retrieved_chunks:
            chunk_record = retrieved_chunk.chunk_record
            if (
                chunk_record.tenant_identifier != tenant_identifier
                or chunk_record.document_identifier not in document_identifiers
                or self._is_bibliography(retrieved_chunk=retrieved_chunk)
            ):
                continue
            section_name = self._section_name(
                retrieved_chunk=retrieved_chunk,
                previous_name=section_names.get(chunk_record.document_identifier, "intro"),
            )
            section_names[chunk_record.document_identifier] = section_name
            content_text = normalize_text(chunk_record.content_text)
            ranking_score = sum(
                1 + content_text.count(search_term) * 0.1
                for search_term in search_terms
                if search_term and search_term in content_text
            )
            is_outline = self._is_outline(retrieved_chunk=retrieved_chunk)
            if is_outline:
                ranking_score += 12
            if ranking_score <= 0:
                continue
            ranking_score += sum(
                1 for search_term in search_terms if search_term in normalize_text(chunk_record.content_text[:200])
            )
            if search_plan.scope_name == "expansion" and chunk_record.chunk_identifier in previous_sources:
                ranking_score *= 0.5
            section_key = (chunk_record.document_identifier, "contents" if is_outline else section_name)
            candidates = section_groups.setdefault(section_key, [])
            candidates.append((ranking_score, retrieved_chunk))
            candidates.sort(key=lambda evidence_item: (-evidence_item[0], evidence_item[1].chunk_record.ordinal_value))
            del candidates[3:]

    def _is_outline(self, *, retrieved_chunk: entities.RetrievedChunk) -> bool:
        content_text = retrieved_chunk.chunk_record.content_text
        return retrieved_chunk.chunk_record.ordinal_value < outline_ordinal_limit and (
            bool(re.search(r"\bcontents\b|оглавление|содержание", content_text, re.IGNORECASE))
            or len(re.findall(r"\.{3,}[^\n]*", content_text)) >= minimum_outline_entries
        )

    def _is_bibliography(self, *, retrieved_chunk: entities.RetrievedChunk) -> bool:
        content_text = retrieved_chunk.chunk_record.content_text
        return (
            len(re.findall(r"^\s*\[\d+\]", content_text, re.MULTILINE)) >= minimum_outline_entries
            and len(re.findall(r"\b(?:19|20)\d{2}\b", content_text)) >= minimum_outline_entries
        )

    def _section_name(self, *, retrieved_chunk: entities.RetrievedChunk, previous_name: str) -> str:
        content_text = retrieved_chunk.chunk_record.content_text.strip()
        heading_text = re.search(r"^#{1,3}\s+(.+)", content_text, re.MULTILINE)
        if heading_text:
            return heading_text[1][:100]
        chapter_match = re.search(r"\b(\d{1,3})-\d{1,3}\b", content_text.split("\n", maxsplit=1)[0])
        if chapter_match:
            return f"chapter-{chapter_match[1]}"
        chapter_start = re.match(r"(\d{1,3})\s*\n[A-ZА-Я]", content_text)
        if chapter_start:
            return f"chapter-{chapter_start[1]}"
        if previous_name != "intro":
            return previous_name
        return f"region-{retrieved_chunk.chunk_record.ordinal_value // 40}"

    async def compose_overview(
        self,
        *,
        search_result: SearchResult,
        conversation_history: tuple[entities.MessageRecord, ...],
        time_budget_seconds: float,
    ) -> entities.GeneratedAnswer:
        covered_names = (
            tuple(
                dict.fromkeys(
                    display_name
                    for history_message in conversation_history
                    if history_message.role_name == "assistant"
                    for display_name in re.findall(r"\*\*([^*\n]+)\*\*", history_message.content_text)
                )
            )
            if search_result.search_plan.scope_name == "expansion"
            else ()
        )
        extracted_items: dict[str, entities.EvidenceItem] = {}
        accepted_names: dict[str, str] = {}
        previous_names = {normalize_name(covered_name) for covered_name in covered_names}
        examined: set[uuid.UUID] = set()
        deadline = time.monotonic() + time_budget_seconds
        for batch_offset in range(0, len(search_result.context_chunks), evidence_batch_size):
            if batch_offset and time.monotonic() >= deadline:
                break
            source_batch = search_result.context_chunks[batch_offset : batch_offset + evidence_batch_size]
            evidence_items = await self._extract_batch(
                question_text=search_result.search_plan.question_text,
                source_batch=source_batch,
                covered_names=(
                    *covered_names[-80:],
                    *(
                        alias_name
                        for evidence_item in extracted_items.values()
                        for alias_name in (
                            evidence_item.canonical_name,
                            evidence_item.source_name or evidence_item.display_name,
                            *evidence_item.alias_names,
                        )
                    ),
                )[-160:],
                deadline=deadline,
                allow_partial=bool(extracted_items),
            )
            if evidence_items is None:
                break
            examined.update(retrieved_chunk.chunk_record.chunk_identifier for retrieved_chunk in source_batch)
            for evidence_item in evidence_items:
                canonical_key = normalize_name(evidence_item.canonical_name)
                alias_keys = {
                    normalize_name(alias_name)
                    for alias_name in (
                        evidence_item.canonical_name,
                        evidence_item.source_name or evidence_item.display_name,
                        *evidence_item.alias_names,
                    )
                    if normalize_name(alias_name)
                }
                if (
                    evidence_item.chunk_identifier
                    not in {retrieved_chunk.chunk_record.chunk_identifier for retrieved_chunk in source_batch}
                    or not canonical_key
                    or bool(alias_keys & previous_names)
                ):
                    continue
                duplicate_key = next(
                    (accepted_names[alias_key] for alias_key in sorted(alias_keys) if alias_key in accepted_names),
                    None,
                )
                if duplicate_key is not None:
                    original_item = extracted_items[duplicate_key]
                    extracted_items[duplicate_key] = dataclasses.replace(
                        original_item,
                        alias_names=tuple(
                            dict.fromkeys(
                                (
                                    *original_item.alias_names,
                                    evidence_item.canonical_name,
                                    evidence_item.source_name or evidence_item.display_name,
                                    *evidence_item.alias_names,
                                )
                            )
                        ),
                    )
                    accepted_names.update(dict.fromkeys(alias_keys, duplicate_key))
                    continue
                extracted_items.setdefault(canonical_key, evidence_item)
                accepted_names.update(dict.fromkeys(alias_keys, canonical_key))
                if len(extracted_items) >= maximum_catalog_items:
                    break
            if len(extracted_items) >= maximum_catalog_items:
                break
        return self._render_overview(search_result=search_result, extracted_items=extracted_items, examined=examined)

    async def _extract_batch(
        self,
        *,
        question_text: str,
        source_batch: tuple[entities.RetrievedChunk, ...],
        covered_names: tuple[str, ...],
        deadline: float,
        allow_partial: bool,
    ) -> tuple[entities.EvidenceItem, ...] | None:
        try:
            async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                return await self.answer_generator.extract_evidence(
                    question_text=question_text,
                    context_chunks=source_batch,
                    covered_names=covered_names,
                )
        except TimeoutError:
            if not allow_partial:
                raise
            return None
        except errors.DependencyUnavailableError as extraction_error:
            if not allow_partial or extraction_error.error_code not in {
                "model_request_timeout",
                "model_response_truncated",
            }:
                raise
            return None

    def _render_overview(
        self,
        *,
        search_result: SearchResult,
        extracted_items: dict[str, entities.EvidenceItem],
        examined: set[uuid.UUID],
    ) -> entities.GeneratedAnswer:
        russian_question = bool(re.search(r"[а-яё]", search_result.search_plan.question_text, re.IGNORECASE))
        source_labels = {
            retrieved_chunk.chunk_record.chunk_identifier: f"S{item_index}"
            for item_index, retrieved_chunk in enumerate(search_result.context_chunks, 1)
        }
        if not extracted_items:
            content_text = (
                "В проверенных разделах не удалось подтвердить новые пункты по вашему вопросу. "
                "Это не означает, что в остальных материалах их нет."
                if russian_question
                else "No new items could be verified in the examined sections. "
                "Other sections may contain additional items."
            )
        else:
            heading_text = (
                "Дополнения из материалов"
                if search_result.search_plan.scope_name == "expansion"
                else "Обзор материалов"
            )
            if not russian_question:
                heading_text = (
                    "Additional findings"
                    if search_result.search_plan.scope_name == "expansion"
                    else "Material overview"
                )
            answer_lines = [f"### {heading_text}"]
            for item_index, evidence_item in enumerate(extracted_items.values(), 1):
                # The final list is assembled from verified extractions, never copied from chat history.
                display_name = re.sub(r"[\[\]*`\n]", "", evidence_item.source_name or evidence_item.display_name)
                description = re.sub(r"\[S\d+\]", "", evidence_item.description_text).strip()
                if not description:
                    description = "Упомянуто в материале." if russian_question else "Named in the material."
                source_chunk = next(
                    retrieved_chunk
                    for retrieved_chunk in search_result.context_chunks
                    if retrieved_chunk.chunk_record.chunk_identifier == evidence_item.chunk_identifier
                )
                if self._is_outline(retrieved_chunk=source_chunk):
                    description = "Упомянуто в оглавлении." if russian_question else "Named in the table of contents."
                answer_lines.append(
                    f"{item_index}. **{display_name}** - {description} "
                    f"[{source_labels[evidence_item.chunk_identifier]}]"
                )
            content_text = "\n\n".join(answer_lines)
        document_count = len(
            {
                retrieved_chunk.chunk_record.document_identifier
                for retrieved_chunk in search_result.context_chunks
                if retrieved_chunk.chunk_record.chunk_identifier in examined
            }
        )
        coverage_notice = (
            f"Проверено тематических фрагментов: {len(examined)}; файлов: {document_count}. "
            f"При отборе просмотрено фрагментов индекса: {search_result.scanned_count}. "
            "Это обзор найденных сведений, а не гарантия полного перечня всех упоминаний."
            if russian_question
            else f"Examined {len(examined)} topical excerpts from {document_count} files; scanned "
            f"{search_result.scanned_count} indexed excerpts for selection. "
            "This overview does not guarantee an exhaustive list."
        )
        if not search_result.scan_complete:
            coverage_notice += (
                " Достигнут лимит просмотра индекса." if russian_question else " The index scan limit was reached."
            )
        if len(examined) < len(search_result.context_chunks):
            coverage_notice += (
                " Обработка ограничена бюджетом ответа."
                if russian_question
                else " Processing was limited by the answer budget."
            )
        return entities.GeneratedAnswer(
            answer_text=f"> {coverage_notice}\n\n{content_text}",
            sufficient_context=True,
            citation_identifiers=tuple(
                dict.fromkeys(evidence_item.chunk_identifier for evidence_item in extracted_items.values())
            )
            or tuple(
                retrieved_chunk.chunk_record.chunk_identifier
                for retrieved_chunk in search_result.context_chunks
                if retrieved_chunk.chunk_record.chunk_identifier in examined
            )[:1],
        )
