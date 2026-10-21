"""Synthetic parser/cache fixtures with no claim of real-platform coverage."""

import json

import pytest

from ahadiff.knowledge_platform.extractors import (
    DouyinExtractor,
    PublicPageExtractor,
    XiaoheiheExtractor,
)
from ahadiff.knowledge_platform.intake.contracts import IntakeError
from ahadiff.knowledge_platform.intake.normalize import normalize_share
from ahadiff.knowledge_platform.media.cache import IntakeCache


def test_xiaoheihe_structured_post_preserves_body_order():
    url = "https://www.xiaoheihe.cn/app/bbs/link/abc"
    content = json.dumps(
        {
            "result": {
                "link": {
                    "link_id": "abc",
                    "title": "合成样本",
                    "user": {"username": "fixture author"},
                    "text": json.dumps(
                        [
                            {"type": "text", "text": "<p>第一段</p>"},
                            {"type": "img", "url": "https://images.example/one.png"},
                            {"type": "text", "text": "第二段<script>do not execute</script>"},
                        ]
                    ),
                }
            }
        }
    ).encode()
    result = XiaoheiheExtractor().extract(
        normalize_share(url=url), url=url, content=content, content_type="application/json"
    )
    assert result.body_text == "第一段\n\n第二段"
    assert result.author == "fixture author"
    assert result.media[0].kind == "image"
    assert result.body_complete


def test_xiaoheihe_does_not_pick_another_post():
    url = "https://www.xiaoheihe.cn/app/bbs/link/abc"
    content = json.dumps({"link_id": "other", "text": "wrong evidence"}).encode()
    result = XiaoheiheExtractor().extract(
        normalize_share(url=url), url=url, content=content, content_type="application/json"
    )
    assert result.state == "needs_input"
    assert result.body_text == ""


def test_xiaoheihe_linkid_alias_is_only_used_for_matching_post():
    url = "https://www.xiaoheihe.cn/app/bbs/link/abc"
    content = json.dumps({"linkid": "abc", "text": "Synthetic post body."}).encode()
    result = XiaoheiheExtractor().extract(
        normalize_share(url=url), url=url, content=content, content_type="application/json"
    )
    assert result.body_text == "Synthetic post body."


def test_douyin_description_is_not_video_transcript():
    url = "https://www.douyin.com/video/123"
    content = json.dumps(
        {
            "aweme_detail": {
                "aweme_id": "123",
                "desc": "这是视频文案",
                "author": {"nickname": "样本作者"},
                "video": {"play_addr": {"url_list": ["https://cdn.example/video.mp4"]}},
            }
        }
    ).encode()
    result = DouyinExtractor().extract(
        normalize_share(url=url), url=url, content=content, content_type="application/json"
    )
    assert result.description == "这是视频文案"
    assert result.body_text == ""
    assert result.state == "partial"
    assert result.body_complete is False
    assert result.metadata["asr_state"] == "not_run"


def test_public_page_ignores_script_commands_and_navigation():
    url = "https://public.example/post"
    result = PublicPageExtractor().extract(
        normalize_share(url=url),
        url=url,
        content=(
            b"<nav>Navigation</nav><article><p>Actual source.</p>"
            b"<script>delete_all()</script></article>"
        ),
        content_type="text/html",
    )
    assert result.body_text == "Actual source."
    assert "delete" not in result.body_text


def test_metadata_only_page_is_not_summarized_as_full_text():
    url = "https://public.example/post"
    result = PublicPageExtractor().extract(
        normalize_share(url=url),
        url=url,
        content=b'<title>Metadata</title><meta name="description" content="Just a teaser">',
        content_type="text/html",
    )
    assert result.state == "needs_input"
    assert result.body_text == ""


def test_cache_integrity_and_intake_path_boundary(tmp_path):
    cache = IntakeCache(tmp_path / "cache")
    reference = cache.put("in_fixture", {"text": "私有样本"})
    path = cache.path("in_fixture", reference["sha256"])
    assert path.read_text(encoding="utf-8").find("私有样本") >= 0
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(IntakeError) as error:
        cache.path("in_fixture", reference["sha256"])
    assert error.value.code == "CACHE_INTEGRITY"
    with pytest.raises(IntakeError):
        cache.put("../outside", {"text": "invalid"})


def test_discard_only_removes_known_temporary_blobs(tmp_path):
    cache = IntakeCache(tmp_path / "cache")
    reference = cache.put("in_fixture", {"text": "temporary"})
    path = cache.path("in_fixture", reference["sha256"])
    retained = tmp_path / "retained.txt"
    retained.write_text("keep me", encoding="utf-8")
    cache.discard("in_fixture")
    assert not path.exists()
    assert retained.read_text(encoding="utf-8") == "keep me"


def test_cache_rejects_symlink_intake_directory(tmp_path):
    cache = IntakeCache(tmp_path / "cache")
    outside = tmp_path / "outside"
    outside.mkdir()
    (cache.root / "in_bad").symlink_to(outside, target_is_directory=True)
    with pytest.raises(IntakeError):
        cache.put("in_bad", {"text": "must not escape"})
    assert list(outside.iterdir()) == []
