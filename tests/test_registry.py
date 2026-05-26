import urllib.error
from unittest.mock import MagicMock, patch

from cache_warmer import registry


def _make_repos(tmp_path, image, tag, digest):
    tag_dir = (
        tmp_path / "docker/registry/v2/repositories" / image / "_manifests/tags" / tag
    )
    (tag_dir / "current").mkdir(parents=True)
    (tag_dir / "current/link").write_text(digest)
    return tag_dir


def test_scan_cache_nonexistent():
    result = registry.scan_cache("/nonexistent/path")
    assert not result


def test_scan_cache_empty(tmp_path):
    result = registry.scan_cache(str(tmp_path))
    assert not result


def test_scan_cache_single_image(tmp_path):
    _make_repos(tmp_path, "library/ubuntu", "22.04", "sha256:abc123")
    result = registry.scan_cache(str(tmp_path))
    assert result == {("library/ubuntu", "22.04"): "sha256:abc123"}


def test_scan_cache_multiple(tmp_path):
    _make_repos(tmp_path, "library/ubuntu", "22.04", "sha256:aaa")
    _make_repos(tmp_path, "library/ubuntu", "latest", "sha256:bbb")
    _make_repos(tmp_path, "myorg/myimage", "1.0", "sha256:ccc")
    result = registry.scan_cache(str(tmp_path))
    assert len(result) == 3
    assert result[("library/ubuntu", "22.04")] == "sha256:aaa"
    assert result[("myorg/myimage", "1.0")] == "sha256:ccc"


def test_get_token_success():
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=resp):
        with patch("json.load", return_value={"token": "mytoken"}):
            token = registry._get_token("library/ubuntu")
    assert token == "mytoken"


def test_get_token_failure():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        token = registry._get_token("library/ubuntu")
    assert token is None


def test_get_upstream_digest_success():
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=b"manifest-data")
    resp.headers = {"Docker-Content-Digest": "sha256:newdigest"}
    with patch("cache_warmer.registry._get_token", return_value="tok"):
        with patch("urllib.request.urlopen", return_value=resp):
            digest = registry.get_upstream_digest("library/ubuntu", "22.04")
    assert digest == "sha256:newdigest"


def test_get_upstream_digest_no_token():
    with patch("cache_warmer.registry._get_token", return_value=None):
        digest = registry.get_upstream_digest("library/ubuntu", "22.04")
    assert digest is None


def test_get_upstream_digest_404():
    with patch("cache_warmer.registry._get_token", return_value="tok"):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "http://x", 404, "Not Found", MagicMock(), None
            ),
        ):
            digest = registry.get_upstream_digest("library/ubuntu", "22.04")
    assert digest is None


def test_invalidate_tag(tmp_path):
    tag_dir = _make_repos(tmp_path, "library/ubuntu", "22.04", "sha256:abc")
    assert (tag_dir / "current/link").exists()
    result = registry._invalidate_tag(str(tmp_path), "library/ubuntu", "22.04")
    assert result is True
    assert not (tag_dir / "current/link").exists()


def test_invalidate_tag_already_gone(tmp_path):
    result = registry._invalidate_tag(str(tmp_path), "library/ubuntu", "22.04")
    assert result is True


def test_pull_manifest_success():
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=b"manifest")
    resp.headers = {"Docker-Content-Digest": "sha256:newdigest"}
    with patch("urllib.request.urlopen", return_value=resp):
        digest = registry._pull_manifest(
            "http://registry:5000", "library/ubuntu", "22.04"
        )
    assert digest == "sha256:newdigest"


def test_pull_manifest_failure():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        digest = registry._pull_manifest(
            "http://registry:5000", "library/ubuntu", "22.04"
        )
    assert digest is None


def test_run_skips_up_to_date(tmp_path):
    _make_repos(tmp_path, "library/ubuntu", "22.04", "sha256:same")
    cfg = MagicMock()
    cfg.docker_registry.cache_dir = str(tmp_path)
    cfg.docker_registry.url = "http://registry:5000"
    with patch("cache_warmer.registry.get_upstream_digest", return_value="sha256:same"):
        with patch("cache_warmer.registry._pull_manifest") as mock_pull:
            registry.run(cfg)
    mock_pull.assert_not_called()


def test_run_refreshes_updated_image(tmp_path):
    _make_repos(tmp_path, "library/ubuntu", "22.04", "sha256:old")
    cfg = MagicMock()
    cfg.docker_registry.cache_dir = str(tmp_path)
    cfg.docker_registry.url = "http://registry:5000"
    with patch("cache_warmer.registry.get_upstream_digest", return_value="sha256:new"):
        with patch(
            "cache_warmer.registry._pull_manifest", return_value="sha256:new"
        ) as mock_pull:
            registry.run(cfg)
    mock_pull.assert_called_once_with("http://registry:5000", "library/ubuntu", "22.04")


def test_run_handles_upstream_failure(tmp_path):
    _make_repos(tmp_path, "library/ubuntu", "22.04", "sha256:old")
    cfg = MagicMock()
    cfg.docker_registry.cache_dir = str(tmp_path)
    cfg.docker_registry.url = "http://registry:5000"
    with patch("cache_warmer.registry.get_upstream_digest", return_value=None):
        with patch("cache_warmer.registry._pull_manifest") as mock_pull:
            registry.run(cfg)
    mock_pull.assert_not_called()
