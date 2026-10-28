"""Schema index: lexical retrieval и fallback без embeddings."""

from app.core.data.profiling import ColumnStats, TableProfile
from app.core.data.schema_index import SchemaIndex, SchemaRetriever
from app.models.semantic import SemanticMetric, SemanticModel


def profile() -> TableProfile:
    return TableProfile(
        table="sales",
        row_count=10,
        column_count=3,
        profiled_at="2026-10-28",
        columns=[
            ColumnStats("id", "BIGINT", "identifier"),
            ColumnStats("amount", "DOUBLE", "numeric", top_values=[{"value": 10, "count": 3}]),
            ColumnStats("region", "VARCHAR", "text", top_values=[{"value": "msk", "count": 2}]),
        ],
    )


def test_search_finds_metric_synonym_without_embeddings() -> None:
    index = SchemaIndex()
    semantic = SemanticModel(
        table="sales",
        metrics=[
            SemanticMetric(
                name="revenue",
                title="Выручка",
                aggregation="SUM",
                column="amount",
                synonyms=["выручка"],
            )
        ],
    )
    index.rebuild("sales", profile(), semantic)
    results = index.search("покажи выручку", "sales", top_k=5)
    assert results
    assert results[0].name == "revenue"


def test_identifier_is_not_indexed_and_fallback_is_lexical() -> None:
    index = SchemaIndex()
    index.rebuild("sales", profile())
    objects = index.search("регион msk", "sales", top_k=10)
    names = {item.name for item in objects}
    assert "id" not in names
    assert "region" in names


def test_retriever_wraps_index() -> None:
    index = SchemaIndex()
    index.rebuild("sales", profile())
    retriever = SchemaRetriever(index)
    assert retriever.retrieve("amount", "sales")
