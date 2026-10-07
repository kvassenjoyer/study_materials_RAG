"""Atomic storage under generated, validated names independent of upload filenames."""

import asyncio
import hashlib
import os
import pathlib
import tempfile
import typing
import uuid

from study_rag.domain import entities, errors


STORAGE_SEGMENT_COUNT: typing.Final = 3


@typing.final
class LocalFileStorage:
    def __init__(self, *, upload_directory: pathlib.Path, maximum_bytes: int = 100 * 1024 * 1024) -> None:
        if maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive")
        self._upload_directory: typing.Final = upload_directory.absolute()
        self._maximum_bytes: typing.Final = maximum_bytes

    async def save_upload(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        content_bytes: bytes,
        filename_text: str,  # noqa: ARG002 -- the port accepts metadata; filenames never select a disk path.
    ) -> entities.StoredUpload:
        if not content_bytes:
            raise errors.InvalidInputError(error_code="empty_document", message_text="The uploaded file is empty.")
        if len(content_bytes) > self._maximum_bytes:
            raise errors.InvalidInputError(
                error_code="upload_too_large", message_text="The uploaded file exceeds its size limit."
            )
        storage_key: typing.Final = f"{tenant_identifier}/{document_identifier}/{uuid.uuid4()}.upload"
        # Await completion even on cancellation: an abandoned thread must not publish an orphan later.
        saving_task: typing.Final = asyncio.create_task(
            asyncio.to_thread(self._save_document, storage_key=storage_key, content_bytes=content_bytes)
        )
        try:
            return await asyncio.shield(saving_task)
        except asyncio.CancelledError:
            await saving_task
            await self.delete_document(storage_key=storage_key)
            raise

    async def load_document(self, *, storage_key: str) -> bytes:
        return await asyncio.to_thread(self._load_document, storage_key=storage_key)

    async def delete_document(self, *, storage_key: str) -> None:
        await asyncio.to_thread(self._delete_document, storage_key=storage_key)

    def _save_document(self, *, storage_key: str, content_bytes: bytes) -> entities.StoredUpload:
        storage_path: typing.Final = self._resolve_storage_path(storage_key=storage_key)
        temporary_path: pathlib.Path | None = None
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._resolve_storage_path(storage_key=storage_key)
            with tempfile.NamedTemporaryFile(
                dir=storage_path.parent, prefix=".upload-", delete=False
            ) as temporary_file:
                temporary_path = pathlib.Path(temporary_file.name)
                temporary_file.write(content_bytes)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.replace(storage_path)
            temporary_path = None
        except OSError as filesystem_error:
            raise errors.DependencyUnavailableError(
                error_code="file_storage_unavailable", message_text="The uploaded file could not be saved."
            ) from filesystem_error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return entities.StoredUpload(
            storage_key=storage_key,
            content_digest=hashlib.sha256(content_bytes).hexdigest(),
            byte_count=len(content_bytes),
        )

    def _load_document(self, *, storage_key: str) -> bytes:
        storage_path: typing.Final = self._resolve_storage_path(storage_key=storage_key)
        try:
            with storage_path.open("rb") as document_file:
                content_bytes: typing.Final = document_file.read(self._maximum_bytes + 1)
        except FileNotFoundError as filesystem_error:
            raise errors.ResourceNotFoundError(
                error_code="stored_file_not_found", message_text="The stored document was not found."
            ) from filesystem_error
        except OSError as filesystem_error:
            raise errors.DependencyUnavailableError(
                error_code="file_storage_unavailable", message_text="The stored document could not be read."
            ) from filesystem_error
        if len(content_bytes) > self._maximum_bytes:
            raise errors.InvalidInputError(
                error_code="upload_too_large", message_text="The stored file exceeds its size limit."
            )
        return content_bytes

    def _delete_document(self, *, storage_key: str) -> None:
        storage_path: typing.Final = self._resolve_storage_path(storage_key=storage_key)
        try:
            storage_path.unlink(missing_ok=True)
        except OSError as filesystem_error:
            raise errors.DependencyUnavailableError(
                error_code="file_storage_unavailable", message_text="The stored document could not be removed."
            ) from filesystem_error

    def _resolve_storage_path(self, *, storage_key: str) -> pathlib.Path:
        path_segments: typing.Final = storage_key.split("/")
        try:
            if len(path_segments) != STORAGE_SEGMENT_COUNT or not path_segments[2].endswith(".upload"):
                self._reject_storage_key()
            identifiers: typing.Final = (
                uuid.UUID(path_segments[0]),
                uuid.UUID(path_segments[1]),
                uuid.UUID(path_segments[2][:-7]),
            )
            canonical_key: typing.Final = f"{identifiers[0]}/{identifiers[1]}/{identifiers[2]}.upload"
            if storage_key != canonical_key:
                self._reject_storage_key()
            root_path: typing.Final = self._upload_directory.resolve()
            candidate_path: typing.Final = (root_path / storage_key).resolve()
            if not candidate_path.is_relative_to(root_path):
                self._reject_storage_key()
        except (ValueError, OSError, RuntimeError) as invalid_key:
            raise errors.InvalidInputError(
                error_code="invalid_storage_key", message_text="The document storage key is invalid."
            ) from invalid_key
        return candidate_path

    def _reject_storage_key(self) -> typing.NoReturn:
        raise ValueError("Storage keys must be canonical generated paths under the upload directory")
