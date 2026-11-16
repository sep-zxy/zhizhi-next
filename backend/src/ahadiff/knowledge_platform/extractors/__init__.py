"""Public response extraction, explicit capabilities and optional integration hooks."""

from ..intake.contracts import Extractor
from .pages import DouyinExtractor, PublicPageExtractor, XiaoheiheExtractor


def default_extractors() -> dict[str, Extractor]:
    """Create isolated parsers; no optional third-party package is imported or executed."""
    return {
        "web": PublicPageExtractor(),
        "xiaoheihe": XiaoheiheExtractor(),
        "douyin": DouyinExtractor(),
    }


__all__ = ["DouyinExtractor", "PublicPageExtractor", "XiaoheiheExtractor", "default_extractors"]
