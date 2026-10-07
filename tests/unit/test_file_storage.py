import asyncio
import hashlib
import pathlib
import uuid

import pytest

from study_rag.adapters.file_storage import LocalFileStorage
from study_rag.domain import errors


async def test_generated_storage_key_ignores_filename_and_roundtrips(tmp_path: pathlib.Path) -> None:  # noqa: COP006 -- pytest fixture.
    file_storage = LocalFileStorage(upload_directory=tmp_path)
    content_bytes = "Учебный материал 🧪".encode()
    saved_upload = await file_storage.save_upload(
        tenant_identifier=uuid.uuid4(),
        document_identifier=uuid.uuid4(),
        content_bytes=content_bytes,
        filename_text="../../other-tenant/secret.txt",
    )

    loaded_bytes = await file_storage.load_document(storage_key=saved_upload.storage_key)

    assert loaded_bytes == content_bytes
    assert saved_upload.content_digest == hashlib.sha256(content_bytes).hexdigest()
    assert saved_upload.byte_count == len(content_bytes)
    assert "secret" not in saved_upload.storage_key
    assert not await asyncio.to_thread(_read_storage_paths, root_directory=tmp_path, glob_pattern=".upload-*")
    await file_storage.delete_document(storage_key=saved_upload.storage_key)
    await file_storage.delete_document(storage_key=saved_upload.storage_key)
    with pytest.raises(errors.ResourceNotFoundError):
        await file_storage.load_document(storage_key=saved_upload.storage_key)


@pytest.mark.parametrize("storage_key", ["../../outside", "/absolute/file", "tenant/doc/source.upload", "a\\b"])
async def test_storage_rejects_noncanonical_keys(tmp_path: pathlib.Path, storage_key: str) -> None:  # noqa: COP006 -- pytest fixture.
    file_storage = LocalFileStorage(upload_directory=tmp_path)

    with pytest.raises(errors.InvalidInputError, match="storage key"):
        await file_storage.load_document(storage_key=storage_key)


async def test_storage_rejects_symlink_escape(tmp_path: pathlib.Path) -> None:  # noqa: COP006 -- pytest fixture.
    uploads_root = tmp_path / "uploads"
    outside_root = tmp_path / "outside"
    uploads_root.mkdir()
    outside_root.mkdir()
    tenant_identifier = uuid.uuid4()
    (uploads_root / str(tenant_identifier)).symlink_to(outside_root, target_is_directory=True)
    file_storage = LocalFileStorage(upload_directory=uploads_root)

    with pytest.raises(errors.InvalidInputError, match="storage key"):
        await file_storage.save_upload(
            tenant_identifier=tenant_identifier,
            document_identifier=uuid.uuid4(),
            content_bytes=b"safe text",
            filename_text="notes.txt",
        )
    assert not tuple(outside_root.iterdir())


async def test_storage_enforces_byte_limit_before_writing(tmp_path: pathlib.Path) -> None:  # noqa: COP006 -- pytest fixture.
    file_storage = LocalFileStorage(upload_directory=tmp_path, maximum_bytes=3)

    with pytest.raises(errors.InvalidInputError) as raised_error:
        await file_storage.save_upload(
            tenant_identifier=uuid.uuid4(),
            document_identifier=uuid.uuid4(),
            content_bytes=b"1234",
            filename_text="notes.txt",
        )
    assert raised_error.value.error_code == "upload_too_large"
    assert not await asyncio.to_thread(_read_storage_paths, root_directory=tmp_path, glob_pattern="*")


def _read_storage_paths(*, root_directory: pathlib.Path, glob_pattern: str) -> tuple[pathlib.Path, ...]:
    return tuple(root_directory.rglob(glob_pattern))
