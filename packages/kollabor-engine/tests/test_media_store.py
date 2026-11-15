"""Unit tests for the sent-image store (#1088)."""

from __future__ import annotations

from pathlib import Path

import pytest
from kollabor_engine.media_store import (
    MAX_DECODED_BYTES,
    REF_RE,
    attach_media_to_rows,
    count_image_tokens,
    list_for,
    open_media,
    save,
)

PNG = "data:image/png;base64,AQID"
JPEG = "data:image/jpeg;base64,BAUF"
WEBP = "data:image/webp;base64,BgYH"


def test_save_stores_and_returns_ref(tmp_path: Path) -> None:
    record = save(tmp_path, "s1", PNG)
    assert REF_RE.match(record.ref)
    assert record.mime == "image/png"
    stored = list((tmp_path / "media" / "s1").glob("*.png"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == bytes([1, 2, 3])


@pytest.mark.parametrize(
    ("mime", "ext"),
    [("image/jpeg", ".jpeg"), ("image/webp", ".webp"), ("image/gif", ".gif")],
)
def test_save_known_types(tmp_path: Path, mime: str, ext: str) -> None:
    record = save(tmp_path, "s1", f"data:{mime};base64,BAUF")
    assert record.mime == mime
    assert list((tmp_path / "media" / "s1").glob(f"*{ext}"))


@pytest.mark.parametrize(
    "data_url",
    [
        "data:text/plain;base64,AQID",
        "data:image/svg+xml;base64,AQID",
        "AQID",
        "data:image/png,not-base64!!",
    ],
)
def test_save_rejects_bad_data_urls(tmp_path: Path, data_url: str) -> None:
    with pytest.raises(ValueError):
        save(tmp_path, "s1", data_url)


def test_save_rejects_oversize(tmp_path: Path) -> None:
    import base64

    blob = base64.b64encode(bytes(MAX_DECODED_BYTES + 1)).decode()
    with pytest.raises(ValueError):
        save(tmp_path, "s1", f"data:image/png;base64,{blob}")


def test_save_retries_a_colliding_ref(tmp_path: Path, monkeypatch) -> None:
    import kollabor_engine.media_store as media_store

    suffixes = iter(("abcd", "abcd", "ef01"))
    monkeypatch.setattr(media_store.time, "strftime", lambda _fmt: "20261115-120000")
    monkeypatch.setattr(media_store.secrets, "token_hex", lambda _size: next(suffixes))
    first = save(tmp_path, "s1", PNG)
    second = save(tmp_path, "s1", JPEG)
    assert first.ref != second.ref
    assert open_media(tmp_path, first.ref)[0] == bytes([1, 2, 3])


def test_list_for_is_oldest_first(tmp_path: Path) -> None:
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG, WEBP)]
    assert [entry["ref"] for entry in list_for(tmp_path, "s1")] == refs


def test_open_media_roundtrip_and_rejects_bad_refs(tmp_path: Path) -> None:
    record = save(tmp_path, "s1", PNG)
    data, mime = open_media(tmp_path, record.ref)
    assert data == bytes([1, 2, 3])
    assert mime == "image/png"
    for bad in ("../s1/x.png", "x.txt", "", "a/b.png"):
        with pytest.raises(FileNotFoundError):
            open_media(tmp_path, bad)


def test_attach_binds_rows_in_order(tmp_path: Path) -> None:
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG, WEBP)]
    rows = [
        {"role": "user", "content": "look [image1] and [image2]"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": "more [image3]"}]},
        {"role": "user", "content": "no images here"},
    ]
    attach_media_to_rows(tmp_path, "s1", rows)
    assert [m["ref"] for m in rows[0]["media"]] == refs[:2]
    assert [m["ref"] for m in rows[2]["media"]] == [refs[2]]
    assert "media" not in rows[1] and "media" not in rows[3]


def test_attach_skips_images_before_the_page(tmp_path: Path) -> None:
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG, WEBP)]
    page = [{"role": "user", "content": "later [image3]"}]
    attach_media_to_rows(tmp_path, "s1", page, skip=3)
    assert "media" not in page[0]
    middle = [{"role": "user", "content": "mid [image3]"}]
    attach_media_to_rows(tmp_path, "s1", middle, skip=2)
    assert [m["ref"] for m in middle[0]["media"]] == [refs[2]]


def test_attach_keeps_existing_media(tmp_path: Path) -> None:
    save(tmp_path, "s1", PNG)
    rows = [
        {
            "role": "user",
            "content": "[image1]",
            "media": [{"ref": "live", "mime": "image/png"}],
        }
    ]
    attach_media_to_rows(tmp_path, "s1", rows)
    assert rows[0]["media"] == [{"ref": "live", "mime": "image/png"}]


def test_attach_without_index_is_a_no_op(tmp_path: Path) -> None:
    rows = [{"role": "user", "content": "[image1]"}]
    attach_media_to_rows(tmp_path, "s1", rows)
    assert "media" not in rows[0]


def test_count_image_tokens_shapes() -> None:
    assert count_image_tokens("a [image1] b") == 1
    assert count_image_tokens([{"type": "text", "text": "[image2]"}]) == 1
    # The daemon mirror stores a sent picture as an image part with no bytes
    # ({"type": "image", "image": None}); the [imageN] text is only the
    # content_to_text rendering. Both shapes are one image (#1088).
    assert count_image_tokens(["plain", {"type": "image"}]) == 1
    assert count_image_tokens([{"type": "image", "image": None}]) == 1
    assert count_image_tokens(None) == 0
    assert count_image_tokens(42) == 0


def test_attach_binds_mirror_part_rows(tmp_path: Path) -> None:
    """Live daemon-mirror shape: text carries no token, the picture is a part."""
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG)]
    rows = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image", "image": None},
            ],
        },
        {"role": "assistant", "content": "ok"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "and this one"},
                {"type": "image", "image": None},
            ],
        },
    ]
    attach_media_to_rows(tmp_path, "s1", rows)
    assert [m["ref"] for m in rows[0]["media"]] == [refs[0]]
    assert [m["ref"] for m in rows[2]["media"]] == [refs[1]]


def test_attach_skip_counts_mirror_part_rows(tmp_path: Path) -> None:
    """Page offsets must count part-shaped rows, not only token text (#1088)."""
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG)]
    earlier = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image", "image": None},
            ],
        },
    ]
    assert count_image_tokens(earlier[0]["content"]) == 1
    page = [{"role": "user", "content": "second [image2]"}]
    attach_media_to_rows(tmp_path, "s1", page, skip=1)
    assert [m["ref"] for m in page[0]["media"]] == [refs[1]]


def test_ref_shape() -> None:
    assert REF_RE.match("20261114-211500-ab12")
    assert not REF_RE.match("20261114-211500-AB12")
    assert not REF_RE.match("20261114-211500-ab12cd34")
    assert not REF_RE.match("../../etc/passwd")
