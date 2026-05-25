"""Channel-directory normalizer: must accept the documented flat shape
AND the OpenClaw nested ``platforms.telegram`` shape transparently, so
users migrating from a hand-rolled OpenClaw-style forwarder don't have
to re-shape their file."""

from clawtell_core.daemon import _normalize_directory


def test_flat_shape_passes_through():
    assert _normalize_directory({"alice": "111", "_default": "222"}) == {
        "alice": "111",
        "_default": "222",
    }


def test_flat_shape_coerces_int_values_to_str():
    assert _normalize_directory({"alice": 111, "_default": 222}) == {
        "alice": "111",
        "_default": "222",
    }


def test_nested_openclaw_shape_extracts_default_and_named_entries():
    data = {
        "updated_at": "2026-05-25T00:00:00Z",
        "platforms": {
            "telegram": [
                {"id": "1884822199", "name": "stefan", "type": "private"},
            ],
        },
    }
    assert _normalize_directory(data) == {
        "_default": "1884822199",
        "stefan": "1884822199",
    }


def test_nested_first_telegram_entry_becomes_default():
    data = {
        "platforms": {
            "telegram": [
                {"id": "111", "name": "alice"},
                {"id": "222", "name": "bob"},
            ],
        },
    }
    out = _normalize_directory(data)
    assert out["_default"] == "111"
    assert out["alice"] == "111"
    assert out["bob"] == "222"


def test_nested_entry_without_name_only_sets_default():
    data = {"platforms": {"telegram": [{"id": "999"}]}}
    assert _normalize_directory(data) == {"_default": "999"}


def test_nested_entry_uses_chat_id_alias_when_id_missing():
    data = {"platforms": {"telegram": [{"chat_id": "777", "name": "carol"}]}}
    assert _normalize_directory(data) == {"_default": "777", "carol": "777"}


def test_nested_empty_telegram_list_returns_empty():
    assert _normalize_directory({"platforms": {"telegram": []}}) == {}


def test_nested_with_only_non_telegram_platforms_returns_empty():
    # Future channels (slack, discord, ...) aren't handled by this
    # normalizer yet; they shouldn't crash, just return empty so the
    # daemon falls back to --default-chat or single-bind.
    assert _normalize_directory({"platforms": {"slack": [{"id": "x"}]}}) == {}


def test_non_dict_input_returns_empty():
    assert _normalize_directory([{"alice": "111"}]) == {}
    assert _normalize_directory("not-a-dict") == {}
    assert _normalize_directory(None) == {}


def test_flat_shape_drops_nested_values():
    # If a flat file accidentally contains a dict value, drop it rather
    # than serialising it into a useless string.
    data = {"alice": "111", "metadata": {"comment": "ignore me"}}
    assert _normalize_directory(data) == {"alice": "111"}
