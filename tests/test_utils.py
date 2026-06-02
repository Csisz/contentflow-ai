from contentflow_ai.migration.utils import get_mime, has_invalid_name_chars, normalize_ws_name


def test_workspace_name_normalization():
    assert normalize_ws_name("SPLIC - 00042") == normalize_ws_name("SPLIC-00042")


def test_invalid_name_chars():
    assert has_invalid_name_chars("bad/name.pdf")
    assert not has_invalid_name_chars("good-name.pdf")


def test_mime_detection():
    assert get_mime("demo.pdf") == "application/pdf"
    assert get_mime("demo.unknown") == "application/octet-stream"
