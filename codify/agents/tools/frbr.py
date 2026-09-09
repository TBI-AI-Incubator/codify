"""Agent tools for FRBR URI construction from jurisdiction configs."""

from codify.frbr import build_frbr_work_uri


def construct_frbr_uri(country: str, doctype: str, year: str, number: str) -> str:
    """Canonical FRBR work URI (jurisdiction pattern, else the canonical
    `/akn/{country}/act/{doctype}/{year}/{number}` default)."""
    return build_frbr_work_uri(country, doctype, year, number)
