"""Example courses API: the course bundles shipped in the repo's examples/ folder (app/bundle.py), ready to study with
no API key."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import bundle
from ..schemas import ExampleCourse, ExampleImportResult

router = APIRouter()


@router.get("/api/examples", response_model=list[ExampleCourse])
def list_examples() -> list[ExampleCourse]:
    return [ExampleCourse(**e) for e in bundle.examples()]


@router.post("/api/examples/{name}/import", response_model=ExampleImportResult)
async def import_example(name: str) -> ExampleImportResult:
    folder = bundle.example_dir(name)
    if folder is None:
        raise HTTPException(404, f"No example course called {name!r}.")
    try:
        result = await bundle.import_bundle(folder)
    except bundle.BundleError as e:
        raise HTTPException(400, str(e)) from e
    return ExampleImportResult(course_id=result.course_id, already_imported=result.already_imported, message=result.message)
