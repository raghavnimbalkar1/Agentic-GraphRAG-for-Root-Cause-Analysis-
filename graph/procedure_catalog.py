"""Read one graph catalog capture and bind it to the local reviewed implementation."""

from pathlib import Path

from core.procedure_contracts import compile_catalog
from graph.schema_definitions import get_sop_catalog_rows


def capture_procedure_catalog(client, *, project_root=None):
    root = project_root if project_root is not None else Path(__file__).resolve().parents[1]
    definitions = []
    for row in get_sop_catalog_rows(client):
        if {"applies_to", "next_if_fail"} & row["skill"].keys():
            raise ValueError("Relationship fields cannot be supplied as skill properties")
        definitions.append({**row["skill"], "applies_to": row["applies_to"], "next_if_fail": row["next_if_fail"]})
    return compile_catalog(definitions, project_root=root)
