"""Exercise the actual HTTP API with local models, without external study data."""

import argparse
import asyncio
import dataclasses
import http
import pathlib
import secrets
import typing
import uuid

import httpx

from study_rag.api import schemas


@typing.final
class SmokeFailureError(RuntimeError):
    """A real local request violated an expected service invariant."""


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class LocalSmokeRunner:
    http_client: httpx.AsyncClient
    materials_directory: pathlib.Path
    created_documents: list[tuple[str, uuid.UUID]] = dataclasses.field(default_factory=list)

    async def run_smoke(self) -> None:
        primary_token: typing.Final = await self._register_account()
        foreign_token: typing.Final = await self._register_account()
        try:
            first_document: typing.Final = await self._upload_material(
                access_token=primary_token, material_name="gradient-descent-ru.md"
            )
            second_document: typing.Final = await self._upload_material(
                access_token=primary_token, material_name="probability-en.md"
            )
            await self._wait_ready(access_token=primary_token, document_identifier=first_document.document_identifier)
            await self._wait_ready(access_token=primary_token, document_identifier=second_document.document_identifier)
            primary_chat: typing.Final = await self._create_chat(access_token=primary_token)
            foreign_chat: typing.Final = await self._create_chat(access_token=foreign_token)
            request_identifier: typing.Final = uuid.uuid4()
            question_text: typing.Final = "Что такое градиентный спуск и как меняются параметры?"
            first_answer: typing.Final = await self._ask_question(
                access_token=primary_token,
                chat_identifier=primary_chat.chat_identifier,
                question_text=question_text,
                client_request_identifier=request_identifier,
            )
            self._expect_supported(answer_record=first_answer)
            repeated_answer: typing.Final = await self._ask_question(
                access_token=primary_token,
                chat_identifier=primary_chat.chat_identifier,
                question_text=question_text,
                client_request_identifier=request_identifier,
            )
            self._expect_condition(
                condition_value=first_answer == repeated_answer, message_text="Completed request replay changed"
            )
            followup_answer: typing.Final = await self._ask_question(
                access_token=primary_token,
                chat_identifier=primary_chat.chat_identifier,
                question_text="Покажи численный пример из материала.",
            )
            self._expect_supported(answer_record=followup_answer)
            self._expect_condition(
                condition_value="3.2" in followup_answer.assistant_message.content_text
                or "3,2" in followup_answer.assistant_message.content_text,
                message_text="Follow-up omitted the numerical example from the uploaded note",
            )
            english_answer: typing.Final = await self._ask_question(
                access_token=primary_token,
                chat_identifier=(await self._create_chat(access_token=primary_token)).chat_identifier,
                question_text="What are the expectation and variance of a Bernoulli random variable?",
            )
            self._expect_supported(answer_record=english_answer)
            await self._verify_isolation(
                access_token=foreign_token,
                primary_chat=primary_chat,
                foreign_chat=foreign_chat,
                document_identifier=first_document.document_identifier,
                citation_record=first_answer.assistant_message.citation_records[0],
            )
            history_response: typing.Final = await self.http_client.get(
                "/rest/study-rag/messages/",
                headers=self._authorization(access_token=primary_token),
                params={"chat_identifier": str(primary_chat.chat_identifier)},
            )
            history_response.raise_for_status()
            history_page: typing.Final = schemas.PageResponse[schemas.MessageResponse].model_validate(
                history_response.json()
            )
            expected_message_count: typing.Final = 4
            self._expect_condition(
                condition_value=history_page.total_count == expected_message_count,
                message_text="Replay duplicated messages",
            )
            missing_answer: typing.Final = await self._ask_question(
                access_token=primary_token,
                chat_identifier=(await self._create_chat(access_token=primary_token)).chat_identifier,
                question_text="What is the exact secret access code to the Zarnox lunar vault?",
            )
            self._expect_refusal(answer_record=missing_answer)
            for document_identifier in (first_document.document_identifier, second_document.document_identifier):
                await self._delete_material(access_token=primary_token, document_identifier=document_identifier)
            deleted_answer: typing.Final = await self._ask_question(
                access_token=primary_token,
                chat_identifier=primary_chat.chat_identifier,
                question_text="Что такое градиентный спуск?",
            )
            self._expect_refusal(answer_record=deleted_answer)
            print(  # noqa: T201 -- The verification CLI reports its outcome.
                "PASS: real RU/EN retrieval, citations, chat history, replay, "
                "two-tenant isolation, refusal and deletion"
            )
        finally:
            for access_token, document_identifier in self.created_documents:
                await self._delete_material(access_token=access_token, document_identifier=document_identifier)

    async def _register_account(self) -> str:
        response_value: typing.Final = await self.http_client.post(
            "/rest/study-rag/users/",
            json={"email_address": f"smoke-{uuid.uuid4().hex}@example.com", "password_text": secrets.token_urlsafe(24)},
        )
        response_value.raise_for_status()
        return schemas.AuthResponse.model_validate(response_value.json()).access_token

    async def _upload_material(self, *, access_token: str, material_name: str) -> schemas.DocumentResponse:
        material_path: typing.Final = self.materials_directory / material_name
        response_value: typing.Final = await self.http_client.post(
            "/rest/study-rag/documents/",
            headers=self._authorization(access_token=access_token),
            files={"file": (material_name, material_path.read_bytes(), "text/markdown")},
        )
        response_value.raise_for_status()
        document_record: typing.Final = schemas.DocumentResponse.model_validate(response_value.json())
        self.created_documents.append((access_token, document_record.document_identifier))
        return document_record

    async def _wait_ready(self, *, access_token: str, document_identifier: uuid.UUID) -> None:
        async with asyncio.timeout(300):
            while True:
                response_value = await self.http_client.get(
                    f"/rest/study-rag/documents/{document_identifier}/",
                    headers=self._authorization(access_token=access_token),
                )
                response_value.raise_for_status()
                document_record = schemas.DocumentResponse.model_validate(response_value.json())
                if document_record.state_name == "ready":
                    return
                if document_record.state_name == "failed":
                    raise SmokeFailureError(f"Indexing failed: {document_record.error_code}")
                await asyncio.sleep(1)

    async def _create_chat(self, *, access_token: str) -> schemas.ChatResponse:
        response_value: typing.Final = await self.http_client.post(
            "/rest/study-rag/chats/",
            headers=self._authorization(access_token=access_token),
            json={"title_text": "Local smoke"},
        )
        response_value.raise_for_status()
        return schemas.ChatResponse.model_validate(response_value.json())

    async def _ask_question(
        self,
        *,
        access_token: str,
        chat_identifier: uuid.UUID,
        question_text: str,
        client_request_identifier: uuid.UUID | None = None,
    ) -> schemas.AnswerResponse:
        question_request: typing.Final = schemas.QuestionRequest(
            chat_identifier=chat_identifier,
            client_request_identifier=uuid.uuid4() if client_request_identifier is None else client_request_identifier,
            question_text=question_text,
        )
        response_value: typing.Final = await self.http_client.post(
            "/rest/study-rag/messages/",
            headers=self._authorization(access_token=access_token),
            json=question_request.model_dump(mode="json"),
        )
        response_value.raise_for_status()
        return schemas.AnswerResponse.model_validate(response_value.json())

    async def _verify_isolation(
        self,
        *,
        access_token: str,
        primary_chat: schemas.ChatResponse,
        foreign_chat: schemas.ChatResponse,
        document_identifier: uuid.UUID,
        citation_record: schemas.CitationResponse,
    ) -> None:
        for resource_path in (
            f"/rest/study-rag/documents/{document_identifier}/",
            f"/rest/study-rag/chats/{primary_chat.chat_identifier}/",
            citation_record.source_url,
        ):
            response_value = await self.http_client.get(
                resource_path, headers=self._authorization(access_token=access_token)
            )
            self._expect_condition(
                condition_value=response_value.status_code == http.HTTPStatus.NOT_FOUND,
                message_text="Foreign resource was exposed",
            )
        foreign_answer: typing.Final = await self._ask_question(
            access_token=access_token,
            chat_identifier=foreign_chat.chat_identifier,
            question_text="Что такое градиентный спуск?",
        )
        self._expect_refusal(answer_record=foreign_answer)

    async def _delete_material(self, *, access_token: str, document_identifier: uuid.UUID) -> None:
        response_value: typing.Final = await self.http_client.delete(
            f"/rest/study-rag/documents/{document_identifier}/", headers=self._authorization(access_token=access_token)
        )
        if response_value.status_code != http.HTTPStatus.NOT_FOUND:
            response_value.raise_for_status()

    def _expect_supported(self, *, answer_record: schemas.AnswerResponse) -> None:
        self._expect_condition(
            condition_value=answer_record.assistant_message.sufficient_context is True
            and bool(answer_record.assistant_message.citation_records),
            message_text="Supported fixture question did not receive citations",
        )
        for citation_record in answer_record.assistant_message.citation_records:
            self._expect_condition(
                condition_value=f"[{citation_record.source_label}]" in answer_record.assistant_message.content_text,
                message_text="Citation label does not map to the answer",
            )

    def _expect_refusal(self, *, answer_record: schemas.AnswerResponse) -> None:
        self._expect_condition(
            condition_value=answer_record.assistant_message.sufficient_context is False
            and not answer_record.assistant_message.citation_records,
            message_text="Question without usable evidence received a supported answer",
        )

    def _expect_condition(self, *, condition_value: bool, message_text: str) -> None:
        if not condition_value:
            raise SmokeFailureError(message_text)

    def _authorization(self, *, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}


async def run_local_smoke(*, base_url: str, materials_directory: pathlib.Path) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=260.0) as http_client:
        smoke_runner: typing.Final = LocalSmokeRunner(http_client=http_client, materials_directory=materials_directory)
        await smoke_runner.run_smoke()


def main_entrypoint() -> None:
    argument_parser: typing.Final = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    argument_parser.add_argument("--materials-directory", type=pathlib.Path, default=pathlib.Path("examples/materials"))
    parsed_arguments: typing.Final = argument_parser.parse_args()
    asyncio.run(
        run_local_smoke(base_url=parsed_arguments.base_url, materials_directory=parsed_arguments.materials_directory)
    )


if __name__ == "__main__":
    main_entrypoint()
