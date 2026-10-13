import asyncio
import contextlib
import dataclasses
import hashlib
import math
import pathlib
import threading
import uuid

import pytest
from qdrant_client import QdrantClient, models

from study_rag.adapters.vector_store import QdrantVectorStore
from study_rag.domain import entities, errors


_PROFILE_NAME = "test-embedding-profile"
_MISMATCH_DIMENSIONS = 4


def embedded_fixture(*, tenant_identifier: uuid.UUID, document_identifier: uuid.UUID) -> entities.EmbeddedChunk:
    return entities.EmbeddedChunk(
        chunk_record=entities.ChunkRecord(
            chunk_identifier=uuid.uuid4(),
            document_identifier=document_identifier,
            tenant_identifier=tenant_identifier,
            ordinal_value=0,
            content_text="A test learning paragraph.",
            token_count=6,
            page_start=1,
            page_end=1,
        ),
        embedding_vector=(1.0, 0.0, 0.0),
    )


async def test_local_index_filters_tenant_ready_ids_and_deletion(tmp_path: pathlib.Path) -> None:
    first_tenant = uuid.uuid4()
    second_tenant = uuid.uuid4()
    first_document = uuid.uuid4()
    second_document = uuid.uuid4()
    hidden_document = uuid.uuid4()
    first_chunk = embedded_fixture(tenant_identifier=first_tenant, document_identifier=first_document)
    second_chunk = embedded_fixture(tenant_identifier=second_tenant, document_identifier=second_document)
    hidden_chunk = embedded_fixture(tenant_identifier=first_tenant, document_identifier=hidden_document)
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    try:
        await vector_store.upsert_chunks(
            tenant_identifier=first_tenant, embedding_profile=_PROFILE_NAME, embedded_chunks=(first_chunk, hidden_chunk)
        )
        await vector_store.upsert_chunks(
            tenant_identifier=second_tenant, embedding_profile=_PROFILE_NAME, embedded_chunks=(second_chunk,)
        )

        query_result = await vector_store.search_chunks(
            tenant_identifier=first_tenant,
            embedding_profile=_PROFILE_NAME,
            question_vector=(1.0, 0.0, 0.0),
            document_identifiers=(first_document, second_document),
            search_limit=5,
        )

        assert tuple(one_match_record.chunk_identifier for one_match_record in query_result) == (
            first_chunk.chunk_record.chunk_identifier,
        )
        assert (
            await vector_store.search_chunks(
                tenant_identifier=first_tenant,
                embedding_profile=_PROFILE_NAME,
                question_vector=(1.0, 0.0, 0.0),
                document_identifiers=(),
                search_limit=5,
            )
            == ()
        )
        await vector_store.delete_document(
            tenant_identifier=first_tenant, document_identifier=second_document, embedding_profile=_PROFILE_NAME
        )
        assert (
            len(
                await vector_store.search_chunks(
                    tenant_identifier=second_tenant,
                    embedding_profile=_PROFILE_NAME,
                    question_vector=(1.0, 0.0, 0.0),
                    document_identifiers=(second_document,),
                    search_limit=5,
                )
            )
            == 1
        )
        await vector_store.delete_document(
            tenant_identifier=first_tenant, document_identifier=first_document, embedding_profile=_PROFILE_NAME
        )
        assert (
            await vector_store.search_chunks(
                tenant_identifier=first_tenant,
                embedding_profile=_PROFILE_NAME,
                question_vector=(1.0, 0.0, 0.0),
                document_identifiers=(first_document,),
                search_limit=5,
            )
            == ()
        )
    finally:
        await vector_store.close_store()


async def test_local_index_persists_across_restart(tmp_path: pathlib.Path) -> None:
    tenant_identifier = uuid.uuid4()
    document_identifier = uuid.uuid4()
    stored_chunk = embedded_fixture(tenant_identifier=tenant_identifier, document_identifier=document_identifier)
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    await vector_store.upsert_chunks(
        tenant_identifier=tenant_identifier, embedding_profile=_PROFILE_NAME, embedded_chunks=(stored_chunk,)
    )
    await vector_store.close_store()
    reopened_store = QdrantVectorStore(qdrant_directory=tmp_path)
    try:
        await reopened_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)

        query_result = await reopened_store.search_chunks(
            tenant_identifier=tenant_identifier,
            embedding_profile=_PROFILE_NAME,
            question_vector=(1.0, 0.0, 0.0),
            document_identifiers=(document_identifier,),
            search_limit=5,
        )

        assert query_result[0].chunk_identifier == stored_chunk.chunk_record.chunk_identifier
        assert await reopened_store.check_readiness()
    finally:
        await reopened_store.close_store()


async def test_collection_mismatch_rejected_without_recreate(tmp_path: pathlib.Path) -> None:
    collection_name = "study_rag_" + hashlib.sha256(_PROFILE_NAME.encode()).hexdigest()[:32]
    with contextlib.closing(QdrantClient(path=str(tmp_path))) as vector_client:
        vector_client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=_MISMATCH_DIMENSIONS, distance=models.Distance.COSINE),
        )
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)

    with pytest.raises(errors.DependencyUnavailableError, match="incompatible"):
        await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)

    with contextlib.closing(QdrantClient(path=str(tmp_path))) as vector_client:
        collection_config = vector_client.get_collection(collection_name).config.params.vectors
        assert isinstance(collection_config, models.VectorParams)
        assert collection_config.size == _MISMATCH_DIMENSIONS


@pytest.mark.parametrize("invalid_vector", [(0.0, 0.0, 0.0), (1.0, 0.0), (math.nan, 0.0, 1.0)])
async def test_invalid_vector_rejected(tmp_path: pathlib.Path, invalid_vector: tuple[float, ...]) -> None:
    tenant_identifier = uuid.uuid4()
    original_chunk = embedded_fixture(tenant_identifier=tenant_identifier, document_identifier=uuid.uuid4())
    invalid_chunk = dataclasses.replace(original_chunk, embedding_vector=invalid_vector)
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    try:
        with pytest.raises(errors.InvalidInputError, match="Embedding must"):
            await vector_store.upsert_chunks(
                tenant_identifier=tenant_identifier, embedding_profile=_PROFILE_NAME, embedded_chunks=(invalid_chunk,)
            )
    finally:
        await vector_store.close_store()


async def test_cross_tenant_upsert_rejected(tmp_path: pathlib.Path) -> None:
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    foreign_chunk = embedded_fixture(tenant_identifier=uuid.uuid4(), document_identifier=uuid.uuid4())
    try:
        with pytest.raises(errors.InvalidInputError, match="ownership"):
            await vector_store.upsert_chunks(
                tenant_identifier=uuid.uuid4(), embedding_profile=_PROFILE_NAME, embedded_chunks=(foreign_chunk,)
            )
    finally:
        await vector_store.close_store()


async def test_old_profile_deletion_after_profile_change(tmp_path: pathlib.Path) -> None:
    tenant_identifier = uuid.uuid4()
    old_document = uuid.uuid4()
    new_document = uuid.uuid4()
    old_chunk = embedded_fixture(tenant_identifier=tenant_identifier, document_identifier=old_document)
    new_chunk = embedded_fixture(tenant_identifier=tenant_identifier, document_identifier=new_document)
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    await vector_store.upsert_chunks(
        tenant_identifier=tenant_identifier, embedding_profile=_PROFILE_NAME, embedded_chunks=(old_chunk,)
    )
    await vector_store.close_store()
    new_profile = "new-embedding-profile"
    reopened_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await reopened_store.initialize_store(embedding_profile=new_profile, vector_dimensions=3)
    try:
        await reopened_store.upsert_chunks(
            tenant_identifier=tenant_identifier, embedding_profile=new_profile, embedded_chunks=(new_chunk,)
        )

        await reopened_store.delete_document(
            tenant_identifier=tenant_identifier, document_identifier=old_document, embedding_profile=_PROFILE_NAME
        )

        new_matches = await reopened_store.search_chunks(
            tenant_identifier=tenant_identifier,
            embedding_profile=new_profile,
            question_vector=(1.0, 0.0, 0.0),
            document_identifiers=(new_document,),
            search_limit=5,
        )
        assert new_matches[0].chunk_identifier == new_chunk.chunk_record.chunk_identifier
    finally:
        await reopened_store.close_store()
    verification_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await verification_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    try:
        assert (
            await verification_store.search_chunks(
                tenant_identifier=tenant_identifier,
                embedding_profile=_PROFILE_NAME,
                question_vector=(1.0, 0.0, 0.0),
                document_identifiers=(old_document,),
                search_limit=5,
            )
            == ()
        )
    finally:
        await verification_store.close_store()


async def test_cancelled_native_write_finishes_before_close(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_started = threading.Event()
    native_released = threading.Event()
    close_started = threading.Event()
    writing_client: QdrantClient | None = None
    original_upsert = QdrantClient.upsert
    original_close = QdrantClient.close

    def blocking_upsert(
        vector_client: QdrantClient,
        *,
        collection_name: str,
        points: list[models.PointStruct],  # noqa: COP006 - Qdrant public callback parameter
        wait: bool,  # noqa: COP006 - Qdrant public callback parameter
    ) -> models.UpdateResult:
        nonlocal writing_client
        writing_client = vector_client
        native_started.set()
        if not native_released.wait(timeout=5):
            raise TimeoutError("Test did not release the native write")
        return original_upsert(vector_client, collection_name=collection_name, points=points, wait=wait)

    def tracked_close(vector_client: QdrantClient) -> None:
        # GC can close clients retained by earlier tests while this class patch is active.
        if vector_client is writing_client:
            close_started.set()
        original_close(vector_client)

    monkeypatch.setattr(QdrantClient, "upsert", blocking_upsert)
    monkeypatch.setattr(QdrantClient, "close", tracked_close)
    tenant_identifier = uuid.uuid4()
    stored_chunk = embedded_fixture(tenant_identifier=tenant_identifier, document_identifier=uuid.uuid4())
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    await vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    upsert_task = asyncio.create_task(
        vector_store.upsert_chunks(
            tenant_identifier=tenant_identifier, embedding_profile=_PROFILE_NAME, embedded_chunks=(stored_chunk,)
        )
    )
    assert await asyncio.to_thread(native_started.wait, 5)
    upsert_task.cancel()
    await asyncio.sleep(0)
    close_task = asyncio.create_task(vector_store.close_store())
    await asyncio.sleep(0)
    try:
        assert not close_started.is_set()
    finally:
        native_released.set()
    with pytest.raises(asyncio.CancelledError):
        await upsert_task
    await close_task
    assert close_started.is_set()


async def test_cancelled_initialization_closes_eventual_client(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_started = threading.Event()
    native_released = threading.Event()
    original_open = QdrantVectorStore._open_collection  # noqa: SLF001 - deterministic native I/O cancellation seam

    def blocking_open(vector_store: QdrantVectorStore, *, collection_name: str, vector_dimensions: int) -> QdrantClient:
        native_started.set()
        if not native_released.wait(timeout=5):
            raise TimeoutError("Test did not release native initialization")
        return original_open(vector_store, collection_name=collection_name, vector_dimensions=vector_dimensions)

    monkeypatch.setattr(QdrantVectorStore, "_open_collection", blocking_open)
    vector_store = QdrantVectorStore(qdrant_directory=tmp_path)
    startup_task = asyncio.create_task(
        vector_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
    )
    assert await asyncio.to_thread(native_started.wait, 5)
    startup_task.cancel()
    await asyncio.sleep(0)
    native_released.set()
    with pytest.raises(asyncio.CancelledError):
        await startup_task

    replacement_store = QdrantVectorStore(qdrant_directory=tmp_path)
    try:
        await replacement_store.initialize_store(embedding_profile=_PROFILE_NAME, vector_dimensions=3)
        assert await replacement_store.check_readiness()
    finally:
        await replacement_store.close_store()
