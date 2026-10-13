"""Meaning, evidence provenance and exact synonym relations are independently checked."""

import dataclasses
import json
import uuid

import pytest

from study_rag.adapters.evidence_review import (
    apply_evidence_review,
    apply_synonym_resolution,
    apply_synonym_review,
    build_synonym_pairs,
)
from study_rag.domain import entities, errors


def build_context(*, content_text: str) -> entities.RetrievedChunk:
    return entities.RetrievedChunk(
        chunk_record=entities.ChunkRecord(
            chunk_identifier=uuid.uuid4(),
            document_identifier=uuid.uuid4(),
            tenant_identifier=uuid.uuid4(),
            ordinal_value=0,
            content_text=content_text,
            token_count=20,
            page_start=1,
            page_end=1,
        ),
        filename_text="handbook.pdf",
        similarity_score=0.0,
    )


def build_candidate(original_name: str, source_chunk: entities.RetrievedChunk) -> entities.EvidenceItem:
    return entities.EvidenceItem(
        canonical_name="Untrusted initial label",
        source_name=original_name,
        display_name=original_name,
        description_text="",
        evidence_quote=original_name,
        chunk_identifier=source_chunk.chunk_record.chunk_identifier,
    )


def build_verdict(
    *,
    original_name: str,
    item_identifier: str = "I1",
    category_name: str = "structure",
    canonical_name: str = "Canonical concept",
    equivalent_to: str | None = None,
) -> dict[str, object]:
    return {
        "item_identifier": item_identifier,
        "category_name": category_name,
        "canonical_name": canonical_name,
        "definition_quote": original_name,
        "relevant": True,
        "equivalent_to": equivalent_to,
    }


@pytest.mark.parametrize(
    ("original_name", "category_name"),
    [
        ("acyclic", "property"),
        ("partially persistent", "property"),
        ("cycle", "other"),
        ("circle", "geometry"),
        ("Clipping", "pipeline_stage"),
        ("float-kernel", "software"),
        ("unknown representation", "uncertain"),
    ],
)
def test_occurrence_does_not_make_a_term_a_data_structure(original_name: str, category_name: str) -> None:
    source_chunk = build_context(content_text=f"The concept is {original_name}.")
    reviewed_items = apply_evidence_review(
        response_text=json.dumps(
            {"verdicts": [build_verdict(original_name=original_name, category_name=category_name)]}
        ),
        question_text="List all data structures",
        evidence_items=(build_candidate(original_name, source_chunk),),
        context_chunks=(source_chunk,),
        covered_names=(),
    )
    assert not reviewed_items


def test_review_cannot_support_a_name_with_an_unrelated_or_invented_quote() -> None:
    source_chunk = build_context(content_text="Hash Tables store keys. B Trees store ordered records.")
    for evidence_quote in ("Invented definition", "B Trees"):
        item_verdict = build_verdict(original_name="Hash Tables") | {"definition_quote": evidence_quote}
        reviewed_items = apply_evidence_review(
            response_text=json.dumps({"verdicts": [item_verdict]}),
            question_text="List all data structures",
            evidence_items=(build_candidate("Hash Tables", source_chunk),),
            context_chunks=(source_chunk,),
            covered_names=(),
        )
        assert not reviewed_items


@pytest.mark.parametrize("failure_name", ["missing", "duplicate", "unknown_item", "unknown_equivalent"])
def test_review_cannot_omit_duplicate_or_invent_candidate_identifiers(failure_name: str) -> None:
    source_chunk = build_context(content_text="Hash Tables store keys.")
    item_verdict = build_verdict(original_name="Hash Tables")
    verdict_list = [item_verdict]
    if failure_name == "missing":
        verdict_list = []
    elif failure_name == "duplicate":
        verdict_list = [item_verdict, item_verdict]
    elif failure_name == "unknown_item":
        item_verdict["item_identifier"] = "I9"
    else:
        item_verdict["equivalent_to"] = "K9"
    with pytest.raises(errors.DependencyUnavailableError) as raised_error:
        apply_evidence_review(
            response_text=json.dumps({"verdicts": verdict_list}),
            question_text="List data structures",
            evidence_items=(build_candidate("Hash Tables", source_chunk),),
            context_chunks=(source_chunk,),
            covered_names=(),
        )
    assert raised_error.value.error_code == "invalid_evidence_review"


def test_explicit_synonyms_merge_but_related_variants_remain_distinct() -> None:
    source_chunk = build_context(
        content_text="Contents\nBSP trees are binary space partition trees. B Trees differ from B+ Trees."
    )
    original_names = ("BSP trees", "binary space partition trees", "B Trees", "B+ Trees")
    synonym_names = original_names[:2]
    verdict_list = [
        build_verdict(
            original_name=original_name,
            item_identifier=f"I{item_index}",
            canonical_name="BSP tree" if original_name in synonym_names else original_name,
            equivalent_to="I1" if original_name == synonym_names[-1] else None,
        )
        for item_index, original_name in enumerate(original_names, 1)
    ]
    reviewed_items = apply_evidence_review(
        response_text=json.dumps({"verdicts": verdict_list}),
        question_text="List data structures",
        evidence_items=tuple(build_candidate(original_name, source_chunk) for original_name in original_names),
        context_chunks=(source_chunk,),
        covered_names=(),
    )
    assert len(reviewed_items) == len(original_names) - 1
    assert reviewed_items[0].source_name == "BSP trees"
    assert "binary space partition trees" in reviewed_items[0].alias_names
    assert "Untrusted initial label" not in reviewed_items[0].alias_names
    assert reviewed_items[0].chunk_identifier == source_chunk.chunk_record.chunk_identifier
    assert {evidence_item.canonical_name for evidence_item in reviewed_items[1:]} == {"B Trees", "B+ Trees"}


def test_family_and_implementation_do_not_merge_even_when_model_requests_it() -> None:
    source_chunk = build_context(content_text="Search trees include AVL trees.")
    reviewed_items = apply_evidence_review(
        response_text=json.dumps(
            {
                "verdicts": [
                    build_verdict(original_name="Search trees", canonical_name="Search tree", category_name="family")
                    | {"definition_quote": source_chunk.chunk_record.content_text},
                    build_verdict(
                        original_name="AVL trees", item_identifier="I2", canonical_name="AVL tree", equivalent_to="I1"
                    )
                    | {"definition_quote": source_chunk.chunk_record.content_text},
                ]
            }
        ),
        question_text="List data structures",
        evidence_items=(build_candidate("Search trees", source_chunk), build_candidate("AVL trees", source_chunk)),
        context_chunks=(source_chunk,),
        covered_names=(),
    )
    assert len(reviewed_items) == len(("family", "implementation"))
    resolved_items = apply_synonym_resolution(
        response_text=json.dumps({"links": [{"item_identifier": "I2", "equivalent_to": "I1"}]}),
        evidence_items=reviewed_items,
        covered_names=(),
    )
    assert len(resolved_items) == len(reviewed_items)


def test_known_synonym_can_resolve_to_a_previous_russian_name() -> None:
    source_chunk = build_context(content_text="Contents\nBSP trees organize spatial data.")
    reviewed_items = apply_evidence_review(
        response_text=json.dumps(
            {"verdicts": [build_verdict(original_name="BSP trees", canonical_name="BSP tree", equivalent_to="K1")]}
        ),
        question_text="List more data structures",
        evidence_items=(build_candidate("BSP trees", source_chunk),),
        context_chunks=(source_chunk,),
        covered_names=("Деревья двоичного разбиения пространства",),
    )
    assert reviewed_items[0].canonical_name == "Деревья двоичного разбиения пространства"
    assert reviewed_items[0].source_name == "BSP trees"


def test_compound_name_cannot_be_canonicalized_to_only_one_member() -> None:
    original_name = "Binomial, Fibonacci, and Pairing Heaps"
    source_chunk = build_context(content_text=f"Contents\n{original_name}")
    reviewed_items = apply_evidence_review(
        response_text=json.dumps(
            {"verdicts": [build_verdict(original_name=original_name, canonical_name="Binomial Heaps")]}
        ),
        question_text="List data structures",
        evidence_items=(build_candidate(original_name, source_chunk),),
        context_chunks=(source_chunk,),
        covered_names=(),
    )
    assert reviewed_items[0].canonical_name == original_name
    assert "Binomial Heaps" not in reviewed_items[0].alias_names


def test_unrelated_structure_is_excluded_from_a_narrow_question() -> None:
    source_chunk = build_context(content_text="Hash Tables store keys.")
    item_verdict = build_verdict(original_name="Hash Tables") | {"relevant": False}
    assert not apply_evidence_review(
        response_text=json.dumps({"verdicts": [item_verdict]}),
        question_text="List spatial data structures",
        evidence_items=(dataclasses.replace(build_candidate("Hash Tables", source_chunk), alias_names=()),),
        context_chunks=(source_chunk,),
        covered_names=(),
    )


@pytest.mark.parametrize(
    "evidence_quote", ["circle", "A circle in raster graphics", "FIGURE 57.2: A circle in raster graphics"]
)
def test_caption_cannot_prove_type_even_if_model_misclassifies_it(evidence_quote: str) -> None:
    source_chunk = build_context(content_text="FIGURE 57.2: A circle in raster graphics\nThe image contains pixels.")
    item_verdict = build_verdict(original_name="circle") | {"definition_quote": evidence_quote}
    assert not apply_evidence_review(
        response_text=json.dumps({"verdicts": [item_verdict]}),
        question_text="List data structures",
        evidence_items=(build_candidate("circle", source_chunk),),
        context_chunks=(source_chunk,),
        covered_names=(),
    )


def test_unknown_structure_with_an_actual_organization_definition_survives() -> None:
    definition_text = "The Zeta index stores ordered key-value records in linked blocks."
    source_chunk = build_context(content_text=definition_text)
    reviewed_items = apply_evidence_review(
        response_text=json.dumps(
            {"verdicts": [build_verdict(original_name="Zeta index") | {"definition_quote": definition_text}]}
        ),
        question_text="List data structures",
        evidence_items=(build_candidate("Zeta index", source_chunk),),
        context_chunks=(source_chunk,),
        covered_names=(),
    )
    assert tuple(evidence_item.source_name for evidence_item in reviewed_items) == ("Zeta index",)


def test_name_resolution_matches_known_alias_and_preserves_primary_citation() -> None:
    source_chunk = build_context(content_text="BSP trees partition space.")
    reviewed_items = apply_synonym_resolution(
        response_text=json.dumps({"links": [{"item_identifier": "I1", "equivalent_to": "K1"}]}),
        evidence_items=(build_candidate("BSP trees", source_chunk),),
        covered_names=("Binary Space Partitioning Trees",),
    )
    assert reviewed_items[0].canonical_name == "Binary Space Partitioning Trees"
    assert reviewed_items[0].source_name == "BSP trees"
    assert reviewed_items[0].chunk_identifier == source_chunk.chunk_record.chunk_identifier


@pytest.mark.parametrize(
    "synonym_links",
    [
        [{"item_identifier": "I9", "equivalent_to": "I1"}],
        [{"item_identifier": "I1", "equivalent_to": "K9"}],
        [{"item_identifier": "I1", "equivalent_to": "I1"}] * 2,
    ],
)
def test_name_resolution_cannot_invent_identifiers_or_return_conflicting_links(
    synonym_links: list[dict[str, str]],
) -> None:
    source_chunk = build_context(content_text="BSP trees partition space.")
    with pytest.raises(errors.DependencyUnavailableError) as raised_error:
        apply_synonym_resolution(
            response_text=json.dumps({"links": synonym_links}),
            evidence_items=(build_candidate("BSP trees", source_chunk),),
            covered_names=(),
        )
    assert raised_error.value.error_code == "invalid_evidence_review"


def test_pair_similarity_only_proposes_candidates_and_semantics_controls_merging() -> None:
    source_chunk = build_context(content_text="Binary space partitions; Quadtrees and Octrees; B+ Trees.")
    original_names = ("Binary space partitions", "Quadtrees and Octrees", "B+ Trees")
    evidence_items = tuple(
        dataclasses.replace(build_candidate(original_name, source_chunk), canonical_name=original_name)
        for original_name in original_names
    )
    covered_names = ("Binary Space Partitioning Trees", "Multidimensional Spatial Data Structures", "B Trees")
    pair_records = build_synonym_pairs(evidence_items=evidence_items, covered_names=covered_names)
    unchanged_items = apply_synonym_review(
        response_text=json.dumps({"same_concept": [False] * len(pair_records)}),
        pair_records=pair_records,
        evidence_items=evidence_items,
        covered_names=covered_names,
    )
    assert tuple(evidence_item.canonical_name for evidence_item in unchanged_items) == original_names
    reviewed_items = apply_synonym_review(
        response_text=json.dumps(
            {
                "same_concept": [
                    synonym_link.item_identifier == "I1" and synonym_link.equivalent_to == "K1"
                    for synonym_link, _ in pair_records
                ]
            }
        ),
        pair_records=pair_records,
        evidence_items=evidence_items,
        covered_names=covered_names,
    )
    assert tuple(evidence_item.canonical_name for evidence_item in reviewed_items) == (
        covered_names[0],
        *original_names[1:],
    )
    assert reviewed_items[0].chunk_identifier == source_chunk.chunk_record.chunk_identifier


def test_pair_review_cannot_omit_boolean_decisions() -> None:
    source_chunk = build_context(content_text="BSP trees partition space.")
    evidence_items = (build_candidate("BSP trees", source_chunk),)
    pair_records = build_synonym_pairs(evidence_items=evidence_items, covered_names=("Binary space partitions",))
    with pytest.raises(errors.DependencyUnavailableError) as raised_error:
        apply_synonym_review(
            response_text='{"same_concept": []}',
            pair_records=pair_records,
            evidence_items=evidence_items,
            covered_names=("Binary space partitions",),
        )
    assert raised_error.value.error_code == "invalid_evidence_review"
