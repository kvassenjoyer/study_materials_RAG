"""The owner of database, model, HTTP clients and the durable worker."""

import asyncio
import contextlib
import dataclasses
import secrets
import typing

import httpx

from study_rag.adapters.database import DatabaseResources
from study_rag.adapters.document_parser import PypdfDocumentParser
from study_rag.adapters.embeddings import HuggingFaceEmbeddingProvider, OllamaEmbeddingProvider
from study_rag.adapters.file_storage import LocalFileStorage
from study_rag.adapters.generation import OllamaAnswerGenerator
from study_rag.adapters.repositories import (
    PostgreSQLAuthRepository,
    PostgreSQLConversationRepository,
    PostgreSQLDocumentRepository,
)
from study_rag.adapters.repositories.projects import PostgreSQLProjectRepository
from study_rag.adapters.security import ArgonPasswordSecurity, JwtAccessTokenCodec
from study_rag.adapters.tokenizer import HuggingFaceTokenizer
from study_rag.adapters.vector_store import QdrantVectorStore
from study_rag.application.answer_queue import AnswerQueue
from study_rag.application.authentication import AuthenticationService
from study_rag.application.conversations import ConversationService
from study_rag.application.documents import DocumentService
from study_rag.application.ingestion import IngestionWorker
from study_rag.application.projects import ProjectService
from study_rag.config import Settings
from study_rag.domain import ports


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ApplicationResources:
    settings: Settings
    authentication_service: AuthenticationService
    project_service: ProjectService
    document_service: DocumentService
    conversation_service: ConversationService
    ingestion_worker: IngestionWorker
    database_resources: DatabaseResources
    vector_store: ports.VectorStore
    embedding_provider: OllamaEmbeddingProvider | HuggingFaceEmbeddingProvider
    answer_generator: OllamaAnswerGenerator
    worker_task: asyncio.Task[None]
    answer_queue: AnswerQueue
    answer_worker_task: asyncio.Task[None]

    async def check_readiness(self) -> bool:
        if self.worker_task.done() or self.answer_worker_task.done():
            return False
        readiness_results: typing.Final = await asyncio.gather(
            self.database_resources.check_readiness(),
            self.vector_store.check_readiness(),
            self.embedding_provider.check_readiness(),
            self.answer_generator.check_readiness(),
            return_exceptions=True,
        )
        return all(readiness_value is True for readiness_value in readiness_results)


type ResourceFactory = typing.Callable[[], contextlib.AbstractAsyncContextManager[ApplicationResources]]


@contextlib.asynccontextmanager
async def initialize_resources(*, settings: Settings | None = None) -> typing.AsyncIterator[ApplicationResources]:
    configured_settings: typing.Final = Settings() if settings is None else settings
    async with contextlib.AsyncExitStack() as resource_stack:
        database_resources: typing.Final = DatabaseResources(database_url=configured_settings.database_url)
        resource_stack.push_async_callback(database_resources.close_database)
        await database_resources.initialize_database()
        http_client: typing.Final = await resource_stack.enter_async_context(
            httpx.AsyncClient(
                base_url=configured_settings.ollama_url,
                timeout=httpx.Timeout(min(configured_settings.generation_timeout_seconds / 3, 40.0), connect=5.0),
            )
        )
        generation_tokenizer: typing.Final = HuggingFaceTokenizer(
            model_identifier=configured_settings.tokenizer_model,
            cache_directory=configured_settings.model_cache_directory,
            chunk_tokens=configured_settings.chunk_token_limit,
            overlap_tokens=configured_settings.chunk_overlap_tokens,
            content_byte_limit=configured_settings.chunk_byte_limit,
            maximum_chunks=configured_settings.maximum_document_chunks,
        )
        resource_stack.push_async_callback(generation_tokenizer.close_tokenizer)
        await generation_tokenizer.initialize_tokenizer()
        embedding_tokenizer = generation_tokenizer
        if configured_settings.embedding_backend == "huggingface":
            embedding_tokenizer = HuggingFaceTokenizer(
                model_identifier=configured_settings.embedding_model,
                cache_directory=configured_settings.model_cache_directory,
                chunk_tokens=configured_settings.chunk_token_limit,
                overlap_tokens=configured_settings.chunk_overlap_tokens,
                content_byte_limit=None,
                formatted_byte_limit=None,
                maximum_chunks=configured_settings.maximum_document_chunks,
            )
            resource_stack.push_async_callback(embedding_tokenizer.close_tokenizer)
            await embedding_tokenizer.initialize_tokenizer()
        embedding_provider: OllamaEmbeddingProvider | HuggingFaceEmbeddingProvider
        if configured_settings.embedding_backend == "huggingface":
            embedding_provider = HuggingFaceEmbeddingProvider(
                text_tokenizer=embedding_tokenizer,
                embedding_profile=configured_settings.embedding_profile,
                model_identifier=configured_settings.embedding_model,
                device_name=configured_settings.embedding_device,
                cache_directory=configured_settings.model_cache_directory,
            )
        else:
            embedding_provider = OllamaEmbeddingProvider(
                http_client=http_client,
                text_tokenizer=embedding_tokenizer,
                embedding_profile=configured_settings.embedding_profile,
                model_identifier=configured_settings.ollama_embedding_model,
                content_byte_limit=configured_settings.chunk_byte_limit,
                request_timeout_seconds=min(configured_settings.generation_timeout_seconds / 3, 40.0),
            )
        resource_stack.push_async_callback(embedding_provider.close_provider)
        await embedding_provider.initialize_provider()
        vector_store: typing.Final = QdrantVectorStore(
            qdrant_url=configured_settings.qdrant_url,
            qdrant_directory=configured_settings.qdrant_directory,
        )
        resource_stack.push_async_callback(vector_store.close_store)
        await vector_store.initialize_store(
            embedding_profile=embedding_provider.embedding_profile,
            vector_dimensions=embedding_provider.vector_dimensions,
        )
        answer_generator: typing.Final = OllamaAnswerGenerator(
            http_client=http_client,
            text_tokenizer=generation_tokenizer,
            model_identifier=configured_settings.generation_model,
            context_tokens=configured_settings.generation_context_tokens,
            output_tokens=configured_settings.generation_output_tokens,
            request_timeout_seconds=min(configured_settings.generation_timeout_seconds, 120.0),
        )
        resource_stack.push_async_callback(answer_generator.close_provider)
        await answer_generator.initialize_provider()
        password_security: typing.Final = ArgonPasswordSecurity()
        dummy_password_hash: typing.Final = await asyncio.to_thread(
            password_security.hash_password, password_text=secrets.token_urlsafe(32)
        )
        auth_repository: typing.Final = PostgreSQLAuthRepository(session_factory=database_resources.session_factory)
        document_repository: typing.Final = PostgreSQLDocumentRepository(
            session_factory=database_resources.session_factory
        )
        conversation_repository: typing.Final = PostgreSQLConversationRepository(
            session_factory=database_resources.session_factory
        )
        authentication_service: typing.Final = AuthenticationService(
            auth_repository=auth_repository,
            password_security=password_security,
            access_token_codec=JwtAccessTokenCodec(
                secret_value=configured_settings.jwt_secret.get_secret_value(),
                issuer_name=configured_settings.jwt_issuer,
                audience_name=configured_settings.jwt_audience,
                expiry_seconds=configured_settings.jwt_expiry_seconds,
            ),
            dummy_password_hash=dummy_password_hash,
        )
        file_storage: typing.Final = LocalFileStorage(
            upload_directory=configured_settings.upload_directory,
            maximum_bytes=configured_settings.maximum_upload_bytes,
        )
        document_service: typing.Final = DocumentService(
            document_repository=document_repository,
            file_storage=file_storage,
            embedding_provider=embedding_provider,
            maximum_upload_bytes=configured_settings.maximum_upload_bytes,
        )
        conversation_service: typing.Final = ConversationService(
            conversation_repository=conversation_repository,
            document_repository=document_repository,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            answer_generator=answer_generator,
            retrieval_limit=configured_settings.retrieval_limit,
            retrieval_candidates=configured_settings.retrieval_candidates,
            overview_chunk_limit=configured_settings.overview_chunk_limit,
            scan_chunk_limit=configured_settings.scan_chunk_limit,
            maximum_search_passes=configured_settings.maximum_search_passes,
            minimum_similarity=configured_settings.minimum_similarity,
            history_message_limit=configured_settings.history_message_limit,
            turn_lease_seconds=configured_settings.turn_lease_seconds,
            answer_timeout_seconds=configured_settings.answer_timeout_seconds,
        )
        ingestion_worker: typing.Final = IngestionWorker(
            document_repository=document_repository,
            file_storage=file_storage,
            document_parser=PypdfDocumentParser(
                maximum_pages=configured_settings.maximum_pdf_pages,
                maximum_characters=configured_settings.maximum_extracted_characters,
            ),
            text_tokenizer=embedding_tokenizer,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            poll_seconds=configured_settings.worker_poll_seconds,
            lease_seconds=configured_settings.document_lease_seconds,
            heartbeat_seconds=configured_settings.heartbeat_seconds,
        )
        worker_task: typing.Final = asyncio.create_task(ingestion_worker.run_worker(), name="study-rag-ingestion")
        resource_stack.push_async_callback(stop_worker_task, ingestion_worker=ingestion_worker, worker_task=worker_task)
        answer_queue = AnswerQueue(
            conversation_repository=conversation_repository, conversation_service=conversation_service
        )
        answer_worker_task = asyncio.create_task(answer_queue.run_worker(), name="study-rag-answer-queue")
        resource_stack.push_async_callback(stop_answer_worker, worker_task=answer_worker_task)
        application_resources: typing.Final = ApplicationResources(
            settings=configured_settings,
            authentication_service=authentication_service,
            project_service=ProjectService(
                project_repository=PostgreSQLProjectRepository(session_factory=database_resources.session_factory)
            ),
            document_service=document_service,
            conversation_service=conversation_service,
            ingestion_worker=ingestion_worker,
            database_resources=database_resources,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            answer_generator=answer_generator,
            worker_task=worker_task,
            answer_queue=answer_queue,
            answer_worker_task=answer_worker_task,
        )
        yield application_resources


async def stop_worker_task(*, ingestion_worker: IngestionWorker, worker_task: asyncio.Task[None]) -> None:
    ingestion_worker.stop_worker()
    worker_task.cancel()
    await asyncio.gather(worker_task, return_exceptions=True)


async def stop_answer_worker(*, worker_task: asyncio.Task[None]) -> None:
    worker_task.cancel()
    await asyncio.gather(worker_task, return_exceptions=True)
