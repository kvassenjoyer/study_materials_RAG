"""Project creation and browsing within the authenticated account."""

import dataclasses
import typing
import uuid

from study_rag.domain import entities, errors, ports


maximum_project_title_characters: typing.Final = 200


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ProjectService:
    project_repository: ports.ProjectRepository

    async def rename_project(
        self, *, tenant_identity: entities.TenantIdentity, project_identifier: uuid.UUID, title_text: str
    ) -> entities.ProjectRecord:
        normalized_title = title_text.strip()
        if not normalized_title or len(normalized_title) > maximum_project_title_characters:
            raise errors.InvalidInputError(
                error_code="invalid_project_title", message_text="Project title must contain 1 to 200 characters"
            )
        return await self.project_repository.rename_project(
            tenant_identifier=tenant_identity.user_identifier,
            project_identifier=project_identifier,
            title_text=normalized_title,
        )

    async def delete_project(self, *, tenant_identity: entities.TenantIdentity, project_identifier: uuid.UUID) -> None:
        await self.project_repository.delete_project(
            tenant_identifier=tenant_identity.user_identifier, project_identifier=project_identifier
        )

    async def create_project(
        self, *, tenant_identity: entities.TenantIdentity, title_text: str
    ) -> entities.ProjectRecord:
        normalized_title = title_text.strip()
        if not normalized_title or len(normalized_title) > maximum_project_title_characters:
            raise errors.InvalidInputError(
                error_code="invalid_project_title", message_text="Project title must contain 1 to 200 characters"
            )
        return await self.project_repository.create_project(
            tenant_identifier=tenant_identity.user_identifier,
            title_text=normalized_title,
        )

    async def fetch_project(
        self, *, tenant_identity: entities.TenantIdentity, project_identifier: uuid.UUID
    ) -> entities.ProjectRecord:
        return await self.project_repository.fetch_project(
            tenant_identifier=tenant_identity.user_identifier,
            project_identifier=project_identifier,
        )

    async def list_projects(
        self, *, tenant_identity: entities.TenantIdentity, limit_count: int, offset_value: int
    ) -> entities.PageResult[entities.ProjectRecord]:
        return await self.project_repository.list_projects(
            tenant_identifier=tenant_identity.user_identifier,
            limit_count=limit_count,
            offset_value=offset_value,
        )
