"""Unit tests for the sent-image store (#1088)."""

from __future__ import annotations

from pathlib import Path

import pytest
from kollabor_engine.routes.messages import _save_sent_media
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


def test_non_ascii_digit_tokens_are_not_tokens_and_bind_nothing(tmp_path: Path) -> None:
    r"""#1140 R3: the image-token grammar is ASCII digits only.

    Python's Unicode \d used to count [image1\u0661] (Arabic-Indic one) as a
    token while the JS splitter treated it as literal text — the mismatch
    made attach_media_to_rows bind a stored image to a row whose text was
    plain prose and starve the real sender.
    """
    assert count_image_tokens("[image1\u0661]") == 0
    assert count_image_tokens("[[image1\u0661]]") == 0
    assert count_image_tokens("[image\u0661]") == 0

    refs = [save(tmp_path, "s1", PNG).ref]
    rows = [
        {"role": "user", "content": "plain prose [image1\u0661] tail"},
        {"role": "user", "content": "the real sender [image1]"},
    ]
    attach_media_to_rows(tmp_path, "s1", rows)
    assert "media" not in rows[0]
    assert [m["ref"] for m in rows[1]["media"]] == refs


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
    # Escaped literal [[imageN]] is text, not a media token (#1124 P2a):
    # it must not consume a slot, or binding and the page-offset skip
    # drift from what the frontend renders.
    assert count_image_tokens("see [[image1]] below") == 0
    assert count_image_tokens("[[image1]] and [image1]") == 1
    # Zero-valued and all-zero-padded tokens are text, matching the
    # frontend's near-miss grammar (value semantics, not leading digit).
    assert count_image_tokens("[image0] is text") == 0
    assert count_image_tokens("[image00] is text") == 0
    assert count_image_tokens("[image01] is a token") == 1
    # Malformed one-sided bracket runs count the inner token, matching the
    # frontend splitter (#1140 review): the stray bracket of the run stays
    # text, the well-formed token inside still binds its slot.
    assert count_image_tokens("[[image1]") == 1
    assert count_image_tokens("[image1]]") == 1
    assert count_image_tokens("[[image1] then [image2]") == 2


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


def test_mixed_row_counts_once(tmp_path: Path) -> None:
    """A row with a [imageN] token in text AND an image part is one image.

    _save_sent_media stores one slot per image part; the token text is the
    same image's rendering. Counting both made the row slice two refs,
    starving the row after it (#1124 clean-room review P2).
    """
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG)]
    rows = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look [image1]"},
                {"type": "image", "image": None},
            ],
        },
        {"role": "user", "content": "second [image1]"},
    ]
    assert count_image_tokens(rows[0]["content"]) == 1
    attach_media_to_rows(tmp_path, "s1", rows)
    assert [m["ref"] for m in rows[0]["media"]] == [refs[0]]
    assert [m["ref"] for m in rows[1]["media"]] == [refs[1]]


def test_page_after_mixed_row_skips_once(tmp_path: Path) -> None:
    """The older-page offset must count a mixed row once too.

    The F841 finding exposed that the older-row fixture was assigned but
    never checked; keep its mixed-row count tied to this pagination case.
    """
    refs = [save(tmp_path, "s1", url).ref for url in (PNG, JPEG)]
    earlier = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look [image1]"},
                {"type": "image", "image": None},
            ],
        },
    ]
    assert count_image_tokens(earlier[0]["content"]) == 1
    page = [{"role": "user", "content": "second [image1]"}]
    attach_media_to_rows(tmp_path, "s1", page, skip=1)
    assert [m["ref"] for m in page[0]["media"]] == [refs[1]]


def test_failed_part_does_not_starve_later_rows(tmp_path: Path, monkeypatch) -> None:
    """A part whose storage failed must not shift later rows' refs (#1124 P2).

    row1 sends two images, the first fails to store; row2 sends one that
    stores. Binding must give row1 its surviving ref and row2 the later
    one -- not let row1's count consume row2's ref.
    """
    dir_ = tmp_path / "conversations"
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path
    )
    saved1 = _save_sent_media(
        [
            {"type": "text", "text": "look"},
            {"type": "image", "image": "data:image/png;base64,!!!not-base64"},
            {"type": "image", "image": PNG},
        ],
        "s1",
    )
    saved2 = _save_sent_media(
        [{"type": "text", "text": "and"}, {"type": "image", "image": JPEG}],
        "s1",
    )
    assert len(saved1) == 2
    assert saved1[0] == {"ref": "", "mime": "", "failed": True}
    assert saved1[1]["ref"]
    rows = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image", "image": None},
                {"type": "image", "image": None},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "and"},
                {"type": "image", "image": None},
            ],
        },
    ]
    attach_media_to_rows(dir_, "s1", rows)
    # Dense slot union (#1124 P2a): row1's failed first part emits a
    # placeholder at ordinal 0; its surviving ref stays at ordinal 1 and
    # row2 still binds its own later ref.
    assert rows[0]["media"][0] == {"ref": "", "mime": "", "failed": True}
    assert [m["ref"] for m in rows[0]["media"][1:]] == [saved1[1]["ref"]]
    assert [m["ref"] for m in rows[1]["media"]] == [saved2[0]["ref"]]


def test_page_after_failed_part_binds_its_own_ref(tmp_path: Path, monkeypatch) -> None:
    """The page-offset skip must count a failed part's slot too."""
    dir_ = tmp_path / "conversations"
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path
    )
    _save_sent_media(
        [
            {"type": "text", "text": "look"},
            {"type": "image", "image": "data:image/png;base64,!!!not-base64"},
            {"type": "image", "image": PNG},
        ],
        "s1",
    )
    later = _save_sent_media(
        [
            {"type": "text", "text": "next"},
            {"type": "image", "image": JPEG},
        ],
        "s1",
    )
    page = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "next"},
                {"type": "image", "image": None},
            ],
        },
    ]
    attach_media_to_rows(dir_, "s1", page, skip=2)
    # The earlier row's surviving ref (slot 1) is outside this page; the
    # page row binds the later send's ref, not the earlier survivor.
    assert [m["ref"] for m in page[0]["media"]] == [later[0]["ref"]]
    real = list_for(dir_, "s1")
    assert [m["ref"] for m in page[0]["media"]] != [real[0]["ref"]]


def test_failure_matrix_binds_exactly_stored_refs(tmp_path: Path, monkeypatch) -> None:
    """For every valid/invalid part pattern, each row binds exactly the refs
    its own send stored, in order -- count-vs-store disagreement is the
    invariant under test, not one patched scenario (#1124 review bar)."""
    dir_ = tmp_path / "conversations"
    good = [PNG, JPEG]
    # (pattern, description): each row is a list of True(valid)/False(failed)
    # part slots; a trailing text part makes some rows mixed-shape.
    patterns = [
        ([], "no images"),
        ([[]], "one empty row"),
        ([[True]], "single valid"),
        ([[False]], "single failed"),
        ([[True, True]], "two valid one row"),
        ([[False, True]], "failed then valid"),
        ([[True, False]], "valid then failed"),
        ([[False, True], [True]], "failure then a later row"),
        ([[True, False], [False, True]], "failures interleaved"),
        ([[False, False, True]], "two failures then valid"),
        ([[True], [False], [True]], "alternating rows"),
    ]
    for i, (rows_pattern, name) in enumerate(patterns):
        session = f"matrix-{i}"
        if i == 0:
            monkeypatch.setattr(
                "kollabor_config.config_utils.get_project_data_dir",
                lambda: tmp_path,
            )
        for path in dir_.glob("media/*-index.json"):
            path.unlink()
        stored_per_row = []
        for j, row_pattern in enumerate(rows_pattern):
            parts = [{"type": "text", "text": f"row{j}"}]
            for ok in row_pattern:
                parts.append(
                    {"type": "image", "image": good[j % 2] if ok else "data:image/png;base64,bad"}
                )
            stored_per_row.append(_save_sent_media(parts, session))
        history_rows = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"row{j}"},
                    *[{"type": "image", "image": None} for _ in row_pattern],
                ],
            }
            for j, row_pattern in enumerate(rows_pattern)
        ]
        attach_media_to_rows(dir_, session, history_rows)
        for j, row in enumerate(history_rows):
            # Dense slot union (#1124 P2a): one entry per attempted slot;
            # valid slots bind their stored refs in order, failed slots emit
            # the placeholder that binds nothing.
            row_pattern = rows_pattern[j]
            bound = row.get("media") or []
            assert len(bound) == len(row_pattern), (
                f"{name} row{j}: {len(bound)} entries for {len(row_pattern)} slots"
            )
            assert len(stored_per_row[j]) == len(row_pattern), (
                f"{name} row{j}: sent state omitted an attempted slot"
            )
            assert bound == stored_per_row[j], f"{name} row{j}: live/history slots differ"
            stored = iter(
                [entry["ref"] for entry in stored_per_row[j] if entry.get("ref")]
            )
            for ok_flag, entry in zip(row_pattern, bound):
                if ok_flag:
                    assert entry["ref"] == next(stored), f"{name} row{j} valid slot"
                    assert "failed" not in entry, f"{name} row{j} valid slot"
                else:
                    assert entry == {"ref": "", "mime": "", "failed": True}, (
                        f"{name} row{j} failed slot"
                    )


def test_failed_slots_stay_out_of_listing(tmp_path: Path, monkeypatch) -> None:
    dir_ = tmp_path / "conversations"
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path
    )
    _save_sent_media(
        [
            {"type": "image", "image": "data:image/png;base64,!!!not-base64"},
            {"type": "image", "image": PNG},
        ],
        "s1",
    )
    listed = list_for(dir_, "s1")
    assert len(listed) == 1
    assert listed[0]["ref"]


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
