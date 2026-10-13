"""Regression coverage for scope, section diversity, novelty and bounded extraction."""

import dataclasses
import datetime
import typing
import unittest.mock
import uuid

import pytest

from study_rag.application.adaptive_search import AdaptiveSearch, SearchResult
from study_rag.domain import entities, errors, ports


def make_source(
    tenant_identifier: uuid.UUID, document_identifier: uuid.UUID, ordinal_value: int, content_text: str, /
) -> entities.RetrievedChunk:
    return entities.RetrievedChunk(
        chunk_record=entities.ChunkRecord(
            chunk_identifier=uuid.uuid4(),
            document_identifier=document_identifier,
            tenant_identifier=tenant_identifier,
            ordinal_value=ordinal_value,
            content_text=content_text,
            token_count=50,
            page_start=ordinal_value + 1,
            page_end=ordinal_value + 1,
        ),
        filename_text="handbook.pdf",
        similarity_score=0.0,
    )


def make_message(content_text: str, role_name: entities.MessageRole = "user") -> entities.MessageRecord:
    return entities.MessageRecord(
        message_identifier=uuid.uuid4(),
        chat_identifier=uuid.uuid4(),
        turn_identifier=uuid.uuid4(),
        role_name=role_name,
        content_text=content_text,
        citation_records=(),
        sufficient_context=role_name == "assistant",
        turn_status="completed",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def mock_dependency(dependency: object) -> unittest.mock.AsyncMock:
    return typing.cast("unittest.mock.AsyncMock", dependency)


def make_service() -> tuple[AdaptiveSearch, entities.TenantIdentity, uuid.UUID]:
    tenant_identity = entities.TenantIdentity(user_identifier=uuid.uuid4(), login_name="student@example.test")
    document_identifier = uuid.uuid4()
    document_repository = unittest.mock.AsyncMock(spec=ports.DocumentRepository)
    document_repository.fetch_ready_identifiers.return_value = (document_identifier,)
    document_repository.list_ready_chunks.return_value = ()
    document_repository.fetch_document.return_value = entities.DocumentRecord(
        document_identifier=document_identifier,
        tenant_identifier=tenant_identity.user_identifier,
        project_identifier=uuid.uuid4(),
        filename_text="handbook.pdf",
        media_type="application/pdf",
        storage_key="fixture",
        content_digest="fixture",
        byte_count=1000,
        embedding_profile="fixture",
        state_name=entities.DocumentState.READY,
        chunk_count=100,
        page_count=100,
        error_code=None,
        error_message=None,
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    embedding_provider = unittest.mock.AsyncMock(spec=ports.EmbeddingProvider)
    embedding_provider.embedding_profile = "fixture"
    embedding_provider.embed_question.return_value = (1.0, 0.0)
    answer_generator = unittest.mock.AsyncMock(spec=ports.AnswerGenerator)
    answer_generator.plan_search.return_value = entities.SearchPlan(
        question_text="What is a graph?", scope_name="focused", search_queries=("graphs",), search_keywords=("graph",)
    )
    search_service = AdaptiveSearch(
        document_repository=document_repository,
        embedding_provider=embedding_provider,
        vector_store=unittest.mock.AsyncMock(spec=ports.VectorStore),
        answer_generator=answer_generator,
    )
    return search_service, tenant_identity, document_identifier


async def test_explicit_whole_project_scope_overrides_narrow_history_and_covers_sections() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    # Simulate a bad planner narrowing the question to graphs, while English terms occur in its queries.
    mock_dependency(search_service.answer_generator).plan_search.return_value = entities.SearchPlan(
        question_text="graph representations",
        scope_name="focused",
        search_queries=("data structures",),
        search_keywords=("структуры данных",),
    )
    retrieved_chunks = (
        make_source(
            tenant_identity.user_identifier,
            document_identifier,
            0,
            "Contents\nHash Tables .... 9-1\nTrees .... 10-1\nHeaps .... 7-1",
        ),
        make_source(tenant_identity.user_identifier, document_identifier, 1, "12\nSplay Trees\nA data structure."),
        make_source(tenant_identity.user_identifier, document_identifier, 2, "7\nFibonacci Heaps\nA data structure."),
        make_source(uuid.uuid4(), document_identifier, 3, "Other tenant's data structures"),
        make_source(
            tenant_identity.user_identifier,
            document_identifier,
            4,
            "[1] Smith. Trees. Journal, 1999.\n[2] Doe. Heaps. Proceedings, 2000.\n[3] Roe. Graphs. ACM, 2001.",
        ),
    )
    mock_dependency(search_service.document_repository).list_ready_chunks.return_value = retrieved_chunks
    question_text = "Перечисли все структуры данных в материалах проекта"
    search_result = await search_service.search_context(
        tenant_identity=tenant_identity,
        project_identifier=uuid.uuid4(),
        question_text=question_text,
        conversation_history=(make_message("Что такое граф?"),),
    )
    assert search_result.search_plan.scope_name == "overview"
    assert search_result.search_plan.question_text == question_text
    assert {retrieved_chunk.chunk_record.chunk_identifier for retrieved_chunk in search_result.context_chunks} == {
        retrieved_chunk.chunk_record.chunk_identifier for retrieved_chunk in retrieved_chunks[:3]
    }
    assert search_result.scan_complete
    mock_dependency(search_service.embedding_provider).embed_question.assert_not_awaited()


async def test_comparison_reserves_context_for_both_subjects_and_bounds_query_count() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    project_identifier = mock_dependency(
        search_service.document_repository
    ).fetch_document.return_value.project_identifier
    first_source = make_source(tenant_identity.user_identifier, document_identifier, 0, "AVL tree")
    second_source = make_source(tenant_identity.user_identifier, document_identifier, 1, "Red-black tree")
    mock_dependency(search_service.answer_generator).plan_search.return_value = entities.SearchPlan(
        question_text="Compare trees",
        scope_name="comparison",
        search_queries=("AVL tree", "red-black tree", "third", "fourth"),
        search_keywords=("tree",),
    )
    search_service = dataclasses.replace(search_service, maximum_search_passes=2)
    mock_dependency(search_service.vector_store).search_chunks.side_effect = [
        (entities.VectorMatch(chunk_identifier=first_source.chunk_record.chunk_identifier, similarity_score=0.8),),
        (entities.VectorMatch(chunk_identifier=second_source.chunk_record.chunk_identifier, similarity_score=0.8),),
    ]
    mock_dependency(search_service.document_repository).fetch_ready_chunks.side_effect = [
        (first_source.chunk_record,),
        (second_source.chunk_record,),
    ]
    search_result = await search_service.search_context(
        tenant_identity=tenant_identity,
        project_identifier=project_identifier,
        question_text="Compare AVL and red-black trees",
        conversation_history=(),
    )
    assert len(search_result.context_chunks) == len((first_source, second_source))
    assert (
        mock_dependency(search_service.embedding_provider).embed_question.await_count
        == search_service.maximum_search_passes
    )
    mock_dependency(search_service.document_repository).list_ready_chunks.assert_not_awaited()


async def test_narrow_request_to_list_operations_does_not_force_whole_book_overview() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    document_record = mock_dependency(search_service.document_repository).fetch_document.return_value
    retrieved_chunk = make_source(tenant_identity.user_identifier, document_identifier, 0, "Graph operations")
    mock_dependency(search_service.vector_store).search_chunks.return_value = (
        entities.VectorMatch(chunk_identifier=retrieved_chunk.chunk_record.chunk_identifier, similarity_score=0.8),
    )
    mock_dependency(search_service.document_repository).fetch_ready_chunks.return_value = (
        retrieved_chunk.chunk_record,
    )
    search_result = await search_service.search_context(
        tenant_identity=tenant_identity,
        project_identifier=document_record.project_identifier,
        question_text="Перечисли основные операции над графом",
        conversation_history=(),
    )
    assert search_result.search_plan.scope_name == "focused"
    assert search_result.context_chunks == (dataclasses.replace(retrieved_chunk, similarity_score=0.8),)
    mock_dependency(search_service.document_repository).list_ready_chunks.assert_not_awaited()


@pytest.mark.parametrize(
    "question_text",
    ["Еще какие структуры есть? Поищи в других разделах", "Перечисли еще структуры данных из других разделов"],
)
async def test_expansion_recovers_broad_user_scope_and_omits_previous_items(question_text: str) -> None:
    search_service, tenant_identity, document_identifier = make_service()
    retrieved_chunk = make_source(
        tenant_identity.user_identifier, document_identifier, 0, "Contents\nHash tables and Splay trees"
    )
    saved_history = (
        make_message("Перечисли все структуры данных из книги"),
        make_message("**Hash Tables** and **Union-find data structures** — already discussed", "assistant"),
    )
    mock_dependency(search_service.document_repository).list_ready_chunks.return_value = (retrieved_chunk,)
    search_result = await search_service.search_context(
        tenant_identity=tenant_identity,
        project_identifier=uuid.uuid4(),
        question_text=question_text,
        conversation_history=saved_history,
    )
    assert search_result.search_plan.scope_name == "expansion"
    assert "все структуры" in search_result.search_plan.question_text
    mock_dependency(search_service.answer_generator).extract_evidence.return_value = tuple(
        entities.EvidenceItem(
            canonical_name=structure_name,
            display_name=structure_name,
            description_text="Named in the book",
            evidence_quote=structure_name,
            chunk_identifier=retrieved_chunk.chunk_record.chunk_identifier,
        )
        for structure_name in ("Hash table", "Splay trees", "Splay tree", "Union find structure")
    )
    generated_answer = await search_service.compose_overview(
        search_result=search_result, conversation_history=saved_history, time_budget_seconds=10
    )
    assert "Hash table" not in generated_answer.answer_text
    assert "Union find" not in generated_answer.answer_text
    assert generated_answer.answer_text.count("**Splay trees**") == 1
    assert "**Splay tree**" not in generated_answer.answer_text
    assert generated_answer.citation_identifiers == (retrieved_chunk.chunk_record.chunk_identifier,)


async def test_insufficient_answer_expansion_preserves_explicit_document_scope() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    other_document = uuid.uuid4()
    named_document = mock_dependency(search_service.document_repository).fetch_document.return_value
    mock_dependency(search_service.document_repository).fetch_ready_identifiers.return_value = (
        document_identifier,
        other_document,
    )
    mock_dependency(search_service.document_repository).fetch_document.side_effect = [
        named_document,
        dataclasses.replace(named_document, document_identifier=other_document, filename_text="other.pdf"),
    ]
    selected_source = make_source(tenant_identity.user_identifier, document_identifier, 0, "# Trees\nData structures")
    foreign_source = make_source(tenant_identity.user_identifier, other_document, 0, "# Trees\nData structures")
    mock_dependency(search_service.document_repository).list_ready_chunks.return_value = (
        selected_source,
        foreign_source,
    )
    search_result = await search_service.expand_context(
        search_result=SearchResult(
            search_plan=entities.SearchPlan(
                question_text="Explain trees from handbook.pdf",
                scope_name="focused",
                search_queries=("trees",),
                search_keywords=("trees",),
            ),
            context_chunks=(),
        ),
        tenant_identity=tenant_identity,
        project_identifier=named_document.project_identifier,
        conversation_history=(),
    )
    assert search_result.context_chunks == (selected_source,)


async def test_verified_partial_overview_survives_later_timeout_with_coverage_notice() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    retrieved_chunks = tuple(
        make_source(tenant_identity.user_identifier, document_identifier, item_index, f"Structure {item_index}")
        for item_index in range(5)
    )
    mock_dependency(search_service.answer_generator).extract_evidence.side_effect = [
        (
            entities.EvidenceItem(
                canonical_name="Hash table",
                display_name="Hash table",
                description_text="Supported",
                evidence_quote="Structure 0",
                chunk_identifier=retrieved_chunks[0].chunk_record.chunk_identifier,
            ),
            entities.EvidenceItem(
                canonical_name="Hash dictionary",
                display_name="Hash table",
                description_text="Supported",
                evidence_quote="Structure 0",
                chunk_identifier=retrieved_chunks[0].chunk_record.chunk_identifier,
            ),
        ),
        errors.DependencyUnavailableError(error_code="model_request_timeout", message_text="Timed out"),
    ]
    search_result = SearchResult(
        search_plan=entities.SearchPlan(
            question_text="List all structures",
            scope_name="overview",
            search_queries=("data structures",),
            search_keywords=("structure",),
        ),
        context_chunks=retrieved_chunks,
        scanned_count=100,
        scan_complete=True,
    )
    generated_answer = await search_service.compose_overview(
        search_result=search_result, conversation_history=(), time_budget_seconds=10
    )
    assert "Hash table" in generated_answer.answer_text
    assert generated_answer.answer_text.count("**Hash table**") == 1
    assert "limited by the answer budget" in generated_answer.answer_text
    assert "Examined 4" in generated_answer.answer_text
    assert "exhaustive" in generated_answer.answer_text


async def test_synonyms_merge_across_batches_without_merging_b_tree_variants() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    retrieved_chunks = tuple(
        make_source(tenant_identity.user_identifier, document_identifier, item_index, "BSP trees and B+ trees")
        for item_index in range(9)
    )
    first_items = tuple(
        entities.EvidenceItem(
            canonical_name=original_name,
            display_name=original_name,
            source_name=original_name,
            description_text="",
            evidence_quote=original_name,
            chunk_identifier=retrieved_chunks[0].chunk_record.chunk_identifier,
            alias_names=("binary space partitioning trees",) if original_name == "BSP trees" else (),
        )
        for original_name in ("BSP trees", "B Trees", "B+ Trees")
    )
    later_item = entities.EvidenceItem(
        canonical_name="Binary Space Partition tree",
        source_name="binary space partition trees",
        display_name="binary space partition trees",
        description_text="",
        evidence_quote="binary space partition trees",
        chunk_identifier=retrieved_chunks[4].chunk_record.chunk_identifier,
        alias_names=("BSP tree",),
    )
    third_item = dataclasses.replace(
        later_item,
        canonical_name="Binary space partitions",
        source_name="Binary space partitions",
        display_name="Binary space partitions",
        alias_names=("binary space partition trees",),
        chunk_identifier=retrieved_chunks[-1].chunk_record.chunk_identifier,
    )
    mock_dependency(search_service.answer_generator).extract_evidence.side_effect = [
        first_items,
        (later_item,),
        (third_item,),
    ]
    search_result = SearchResult(
        search_plan=entities.SearchPlan(
            question_text="List all structures",
            scope_name="overview",
            search_queries=("structures",),
            search_keywords=("structures",),
        ),
        context_chunks=retrieved_chunks,
        scanned_count=5,
        scan_complete=True,
    )
    generated_answer = await search_service.compose_overview(
        search_result=search_result, conversation_history=(), time_budget_seconds=10
    )
    assert generated_answer.answer_text.count("**BSP trees**") == 1
    assert "**binary space partition trees**" not in generated_answer.answer_text
    assert "**Binary space partitions**" not in generated_answer.answer_text
    assert "**B Trees**" in generated_answer.answer_text
    assert "**B+ Trees**" in generated_answer.answer_text
    later_request = mock_dependency(search_service.answer_generator).extract_evidence.await_args_list[-1]
    assert "binary space partitioning trees" in later_request.kwargs["covered_names"]
    assert "binary space partition trees" in later_request.kwargs["covered_names"]
    assert generated_answer.citation_identifiers == (retrieved_chunks[0].chunk_record.chunk_identifier,)


async def test_missing_materials_skip_planning_and_scanning() -> None:
    search_service, tenant_identity, _document = make_service()
    mock_dependency(search_service.document_repository).fetch_ready_identifiers.return_value = ()
    search_result = await search_service.search_context(
        tenant_identity=tenant_identity,
        project_identifier=uuid.uuid4(),
        question_text="List everything",
        conversation_history=(),
    )
    assert not search_result.context_chunks
    mock_dependency(search_service.answer_generator).plan_search.assert_not_awaited()


async def test_scan_limit_is_reported_and_source_pool_is_bounded() -> None:
    search_service, tenant_identity, document_identifier = make_service()
    search_service = dataclasses.replace(search_service, scan_chunk_limit=256, overview_chunk_limit=8)
    mock_dependency(search_service.document_repository).list_ready_chunks.return_value = tuple(
        make_source(
            tenant_identity.user_identifier,
            document_identifier,
            item_index,
            f"{item_index + 1}\nTrees\nData structures",
        )
        for item_index in range(256)
    )
    search_result = await search_service.search_context(
        tenant_identity=tenant_identity,
        project_identifier=uuid.uuid4(),
        question_text="List all structures in the book",
        conversation_history=(),
    )
    assert search_result.scanned_count == search_service.scan_chunk_limit
    assert not search_result.scan_complete
    assert len(search_result.context_chunks) <= search_service.overview_chunk_limit
    assert mock_dependency(search_service.document_repository).list_ready_chunks.await_count == 1
