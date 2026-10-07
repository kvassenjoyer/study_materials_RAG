"""Qdrant index; tenant, profile, and SQL readiness gates are always mandatory."""

import asyncio
import hashlib
import math
import pathlib
import typing
import uuid

import httpx
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from study_rag.domain import entities, errors


_ResultType = typing.TypeVar("_ResultType")


@typing.final
class QdrantVectorStore:
    def __init__(self, *, qdrant_directory: pathlib.Path, qdrant_url: str | None = None) -> None:
        self._qdrant_directory = qdrant_directory
        self._qdrant_url = qdrant_url
        self._qdrant_client: QdrantClient | None = None
        self._embedding_profile: str | None = None
        self._vector_dimensions: int | None = None
        self._collection_name: str | None = None
        # The local client shares SQLite-backed resources; serialize access and offload all I/O.
        self._operation_lock = asyncio.Lock()

    async def initialize_store(self, *, embedding_profile: str, vector_dimensions: int) -> None:
        if vector_dimensions <= 0 or not embedding_profile:
            raise ValueError("A nonempty profile and positive dimensions are required")
        async with self._operation_lock:
            if self._qdrant_client is not None:
                self._verify_profile(embedding_profile=embedding_profile)
                if self._vector_dimensions != vector_dimensions:
                    raise errors.DependencyUnavailableError(
                        error_code="vector_profile_mismatch", message_text="Qdrant vector dimensions differ"
                    )
                return
            collection_name: typing.Final = (
                "study_rag_" + hashlib.sha256(embedding_profile.encode("utf-8")).hexdigest()[:32]
            )
            try:
                initialized_client: typing.Final = await self._initialize_client(
                    collection_name=collection_name,
                    vector_dimensions=vector_dimensions,
                )
            except (
                OSError,
                ValueError,
                UnexpectedResponse,
                ResponseHandlingException,
                httpx.HTTPError,
            ) as vector_error:
                raise errors.DependencyUnavailableError(
                    error_code="vector_store_unavailable", message_text="Qdrant could not initialize the collection"
                ) from vector_error
            self._qdrant_client = initialized_client
            self._embedding_profile = embedding_profile
            self._vector_dimensions = vector_dimensions
            self._collection_name = collection_name

    async def upsert_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        embedded_chunks: tuple[entities.EmbeddedChunk, ...],
    ) -> None:
        self._verify_profile(embedding_profile=embedding_profile)
        if not embedded_chunks:
            return
        point_records: list[models.PointStruct] = []
        for one_embedded_chunk in embedded_chunks:
            chunk_record = one_embedded_chunk.chunk_record
            if chunk_record.tenant_identifier != tenant_identifier:
                raise errors.InvalidInputError(
                    error_code="chunk_owner_mismatch", message_text="Chunk ownership mismatch"
                )
            self._verify_vector(embedding_vector=one_embedded_chunk.embedding_vector)
            point_records.append(
                models.PointStruct(
                    id=str(chunk_record.chunk_identifier),
                    vector=list(one_embedded_chunk.embedding_vector),
                    payload={
                        "tenant_identifier": str(tenant_identifier),
                        "document_identifier": str(chunk_record.document_identifier),
                        "chunk_identifier": str(chunk_record.chunk_identifier),
                        "embedding_profile": embedding_profile,
                    },
                )
            )
        vector_client: typing.Final = self._require_client()
        collection_name: typing.Final = self._require_collection()
        await self._execute_operation(
            lambda: vector_client.upsert(collection_name=collection_name, points=point_records, wait=True)
        )

    async def delete_document(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        embedding_profile: str,
    ) -> None:
        if not embedding_profile:
            raise errors.InvalidInputError(
                error_code="invalid_embedding_profile", message_text="Embedding profile must not be empty"
            )
        document_filter: typing.Final = models.Filter(
            must=[
                models.FieldCondition(key="tenant_identifier", match=models.MatchValue(value=str(tenant_identifier))),
                models.FieldCondition(key="embedding_profile", match=models.MatchValue(value=embedding_profile)),
                models.FieldCondition(
                    key="document_identifier", match=models.MatchValue(value=str(document_identifier))
                ),
            ],
        )
        vector_client: typing.Final = self._require_client()
        # Tombstones retain their original profile after configuration changes.
        collection_name: typing.Final = (
            "study_rag_" + hashlib.sha256(embedding_profile.encode("utf-8")).hexdigest()[:32]
        )
        collection_exists: typing.Final = await self._execute_operation(
            lambda: vector_client.collection_exists(collection_name)
        )
        if not collection_exists:
            return
        await self._execute_operation(
            lambda: vector_client.delete(
                collection_name=collection_name,
                points_selector=models.FilterSelector(filter=document_filter),
                wait=True,
            ),
        )

    async def search_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        question_vector: entities.EmbeddingVector,
        document_identifiers: tuple[uuid.UUID, ...],
        search_limit: int,
    ) -> tuple[entities.VectorMatch, ...]:
        self._verify_profile(embedding_profile=embedding_profile)
        if not document_identifiers:
            return ()
        self._verify_vector(embedding_vector=question_vector)
        if search_limit <= 0:
            raise errors.InvalidInputError(
                error_code="invalid_search_limit", message_text="Search limit must be positive"
            )
        query_filter: typing.Final = models.Filter(
            must=[
                models.FieldCondition(key="tenant_identifier", match=models.MatchValue(value=str(tenant_identifier))),
                models.FieldCondition(key="embedding_profile", match=models.MatchValue(value=embedding_profile)),
                models.FieldCondition(
                    key="document_identifier",
                    match=models.MatchAny(
                        any=[str(one_document_identifier) for one_document_identifier in document_identifiers]
                    ),
                ),
            ]
        )
        vector_client: typing.Final = self._require_client()
        collection_name: typing.Final = self._require_collection()
        query_result: typing.Final = await self._execute_operation(
            lambda: vector_client.query_points(
                collection_name=collection_name,
                query=list(question_vector),
                query_filter=query_filter,
                limit=search_limit,
                with_payload=False,
                with_vectors=False,
            )
        )
        return tuple(
            entities.VectorMatch(
                chunk_identifier=uuid.UUID(str(one_point_record.id)),
                similarity_score=one_point_record.score,
            )
            for one_point_record in query_result.points
        )

    async def check_readiness(self) -> bool:
        if self._qdrant_client is None or self._collection_name is None:
            return False
        vector_client: typing.Final = self._qdrant_client
        collection_name: typing.Final = self._collection_name
        try:
            await self._execute_operation(lambda: vector_client.get_collection(collection_name))
        except errors.DependencyUnavailableError:
            return False
        return True

    async def close_store(self) -> None:
        async with self._operation_lock:
            if self._qdrant_client is not None:
                try:
                    await self._await_blocking_operation(self._qdrant_client.close)
                finally:
                    self._qdrant_client = None
                    self._embedding_profile = None
                    self._vector_dimensions = None
                    self._collection_name = None

    def _open_collection(self, *, collection_name: str, vector_dimensions: int) -> QdrantClient:
        if self._qdrant_url is None:
            self._qdrant_directory.mkdir(parents=True, exist_ok=True)
        vector_client: typing.Final = (
            QdrantClient(path=str(self._qdrant_directory), force_disable_check_same_thread=True)
            if self._qdrant_url is None
            else QdrantClient(url=self._qdrant_url, timeout=30)
        )
        try:
            if not vector_client.collection_exists(collection_name):
                vector_client.create_collection(
                    collection_name=collection_name,
                    vectors_config=models.VectorParams(size=vector_dimensions, distance=models.Distance.COSINE),
                )
            collection_config: typing.Final = vector_client.get_collection(collection_name).config.params.vectors
            if (
                not isinstance(collection_config, models.VectorParams)
                or collection_config.size != vector_dimensions
                or collection_config.distance != models.Distance.COSINE
            ):
                raise errors.DependencyUnavailableError(
                    error_code="vector_collection_mismatch",
                    message_text="Existing Qdrant collection has incompatible vector configuration",
                )
            if self._qdrant_url is not None:
                for one_field_name in ("tenant_identifier", "document_identifier", "embedding_profile"):
                    vector_client.create_payload_index(
                        collection_name=collection_name,
                        field_name=one_field_name,
                        field_schema=models.PayloadSchemaType.KEYWORD,
                        wait=True,
                    )
        except (
            errors.DomainError,
            OSError,
            ValueError,
            UnexpectedResponse,
            ResponseHandlingException,
            httpx.HTTPError,
        ):
            vector_client.close()
            raise
        return vector_client

    async def _execute_operation(self, operation: typing.Callable[[], _ResultType]) -> _ResultType:
        try:
            async with self._operation_lock:
                return await self._await_blocking_operation(operation)
        except (OSError, ValueError, UnexpectedResponse, ResponseHandlingException, httpx.HTTPError) as vector_error:
            raise errors.DependencyUnavailableError(
                error_code="vector_store_unavailable", message_text="Qdrant operation failed"
            ) from vector_error

    async def _initialize_client(self, *, collection_name: str, vector_dimensions: int) -> QdrantClient:
        blocking_task: typing.Final = asyncio.create_task(
            asyncio.to_thread(
                self._open_collection,
                collection_name=collection_name,
                vector_dimensions=vector_dimensions,
            )
        )
        try:
            return await asyncio.shield(blocking_task)
        except asyncio.CancelledError as cancellation_error:
            # A worker thread cannot be cancelled; own and close the client it eventually creates.
            try:
                cancelled_client: typing.Final = await self._finish_native_task(blocking_task)
                await self._await_blocking_operation(cancelled_client.close)
            finally:
                raise cancellation_error

    async def _await_blocking_operation(self, operation: typing.Callable[[], _ResultType]) -> _ResultType:
        blocking_task: typing.Final = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(blocking_task)
        except asyncio.CancelledError as cancellation_error:
            # Keep the operation lock until native I/O ends before another call or shutdown.
            try:
                await self._finish_native_task(blocking_task)
            finally:
                raise cancellation_error

    async def _finish_native_task(self, blocking_task: asyncio.Task[_ResultType]) -> _ResultType:
        while not blocking_task.done():
            try:
                await asyncio.shield(blocking_task)
            except asyncio.CancelledError:
                continue
        return blocking_task.result()

    def _require_client(self) -> QdrantClient:
        if self._qdrant_client is None:
            raise errors.DependencyUnavailableError(
                error_code="vector_store_unavailable", message_text="Qdrant is not initialized"
            )
        return self._qdrant_client

    def _require_collection(self) -> str:
        if self._collection_name is None:
            raise errors.DependencyUnavailableError(
                error_code="vector_store_unavailable", message_text="Qdrant is not initialized"
            )
        return self._collection_name

    def _verify_profile(self, *, embedding_profile: str) -> None:
        if embedding_profile != self._embedding_profile:
            raise errors.DependencyUnavailableError(
                error_code="vector_profile_mismatch", message_text="Embedding profile does not match Qdrant collection"
            )

    def _verify_vector(self, *, embedding_vector: entities.EmbeddingVector) -> None:
        if (
            len(embedding_vector) != self._vector_dimensions
            or not all(math.isfinite(one_vector_value) for one_vector_value in embedding_vector)
            or sum(one_vector_value**2 for one_vector_value in embedding_vector) == 0
        ):
            raise errors.InvalidInputError(
                error_code="invalid_embedding",
                message_text="Embedding must be finite, nonzero, and match configured dimensions",
            )
