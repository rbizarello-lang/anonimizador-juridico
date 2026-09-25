"""Nucleo local do anonimizador juridico."""

from .detector import StructuredDetector, resolve_overlaps
from .extraction import ExtractedPage, extract_pdf_pages, pages_to_markdown
from .id_propagation import propagate_mapped_ids
from .pseudonymizer import PseudonymMap, PseudonymizationResult, pseudonymize
from .types import EntitySpan

__all__ = [
    "EntitySpan",
    "ExtractedPage",
    "PseudonymMap",
    "PseudonymizationResult",
    "propagate_mapped_ids",
    "resolve_overlaps",
    "StructuredDetector",
    "extract_pdf_pages",
    "pages_to_markdown",
    "pseudonymize",
]
