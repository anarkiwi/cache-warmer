import urllib.error
from unittest.mock import MagicMock, patch

from packaging.version import Version

from cache_warmer import proxpi


def test_scan_cache_empty_dir(tmp_path):
    combos, sdists, files = proxpi.scan_cache(str(tmp_path))
    assert combos == {}
    assert sdists == set()
    assert files == set()


def test_scan_cache_nonexistent_dir():
    combos, sdists, files = proxpi.scan_cache("/nonexistent/path/xyz")
    assert combos == {}
    assert sdists == set()
    assert files == set()


def test_scan_cache_wheel(tmp_path):
    pkg_dir = tmp_path / "requests" / "packages"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requests-2.32.0-py3-none-any.whl").touch()

    combos, _, files = proxpi.scan_cache(str(tmp_path))
    assert "requests-2.32.0-py3-none-any.whl" in files
    assert len(combos) == 1
    key = ("requests", "py3", "none", "any")
    assert key in combos


def test_scan_cache_sdist(tmp_path):
    pkg_dir = tmp_path / "requests" / "packages"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requests-2.32.0.tar.gz").touch()

    _, sdists, files = proxpi.scan_cache(str(tmp_path))
    assert "requests" in sdists
    assert "requests-2.32.0.tar.gz" in files


def test_scan_cache_skips_metadata_and_tmp(tmp_path):
    pkg_dir = tmp_path / "requests" / "packages"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requests-2.32.0-py3-none-any.whl.metadata").touch()
    (pkg_dir / "requests-2.32.0-py3-none-any.whl.tmp").touch()
    (pkg_dir / ".hidden").touch()

    _, _, files = proxpi.scan_cache(str(tmp_path))
    assert files == set()


def _http_error_404(url):
    return urllib.error.HTTPError(url, 404, "Not Found", MagicMock(), None)


def test_discover_endpoints_only_index():
    def fake_urlopen(req, **_):
        raise _http_error_404(req.full_url)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        endpoints = proxpi._discover_endpoints("http://proxy:5001")
    assert endpoints == ["/index"]


def test_discover_endpoints_finds_extras():
    call_count = [0]

    def fake_urlopen(req, **_):
        n = call_count[0]
        call_count[0] += 1
        if n < 2:
            resp = MagicMock()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            resp.status = 200
            return resp
        raise _http_error_404(req.full_url)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        endpoints = proxpi._discover_endpoints("http://proxy:5001")
    assert "/extra/0/index" in endpoints
    assert "/extra/1/index" in endpoints
    assert "/extra/2/index" not in endpoints


def test_fetch_index_found():
    data = {"files": [{"filename": "requests-2.32.0-py3-none-any.whl"}]}
    with patch("cache_warmer.proxpi._http_get_json", return_value=data):
        ep, result = proxpi._fetch_index("http://proxy:5001", ["/index"], "requests")
    assert ep == "/index"
    assert result["files"][0]["filename"] == "requests-2.32.0-py3-none-any.whl"


def test_fetch_index_all_404():
    with patch(
        "cache_warmer.proxpi._http_get_json",
        side_effect=urllib.error.HTTPError(
            "http://proxy:5001/index/requests/", 404, "Not Found", MagicMock(), None
        ),
    ):
        ep, result = proxpi._fetch_index(
            "http://proxy:5001", ["/index", "/extra/0/index"], "requests"
        )
    assert ep is None
    assert result is None


def test_trigger_cache_success():
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(side_effect=[b"data", b""])

    with patch("urllib.request.urlopen", return_value=resp):
        result = proxpi._trigger_cache(
            "http://proxy:5001",
            "/index",
            "requests",
            "requests-2.32.0-py3-none-any.whl",
        )
    assert result is True


def test_trigger_cache_failure():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = proxpi._trigger_cache(
            "http://proxy:5001",
            "/index",
            "requests",
            "requests-2.32.0-py3-none-any.whl",
        )
    assert result is False


def test_run_no_cached_packages(tmp_path):
    cfg = MagicMock()
    cfg.proxpi.url = "http://proxy:5001"
    cfg.proxpi.cache_dir = str(tmp_path)
    cfg.proxpi.workers = 1

    with patch("cache_warmer.proxpi._discover_endpoints", return_value=["/index"]):
        with patch("cache_warmer.proxpi._warm_package", return_value=(0, 0)) as mock_wp:
            proxpi.run(cfg)
    mock_wp.assert_not_called()


def test_run_with_cached_wheel(tmp_path):
    pkg_dir = tmp_path / "requests" / "packages"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requests-2.31.0-py3-none-any.whl").touch()

    cfg = MagicMock()
    cfg.proxpi.url = "http://proxy:5001"
    cfg.proxpi.cache_dir = str(tmp_path)
    cfg.proxpi.workers = 1

    with patch("cache_warmer.proxpi._discover_endpoints", return_value=["/index"]):
        with patch("cache_warmer.proxpi._warm_package", return_value=(1, 1)) as mock_wp:
            proxpi.run(cfg)
    mock_wp.assert_called_once()


def test_warm_package_no_index():
    with patch("cache_warmer.proxpi._fetch_index", return_value=(None, None)):
        attempted, succeeded = proxpi._warm_package(
            "http://proxy:5001", ["/index"], "requests", [], False, set()
        )
    assert attempted == 0
    assert succeeded == 0


def test_warm_package_triggers_newer_wheel():
    index_data = {
        "files": [{"filename": "requests-2.32.0-py3-none-any.whl", "yanked": False}]
    }
    cached_combos = [(("py3", "none", "any"), Version("2.31.0"))]

    with patch("cache_warmer.proxpi._fetch_index", return_value=("/index", index_data)):
        with patch("cache_warmer.proxpi._trigger_cache", return_value=True) as mock_tc:
            attempted, succeeded = proxpi._warm_package(
                "http://proxy:5001", ["/index"], "requests", cached_combos, False, set()
            )
    assert attempted == 1
    assert succeeded == 1
    mock_tc.assert_called_once()


def test_warm_package_skips_already_cached_file():
    index_data = {
        "files": [{"filename": "requests-2.32.0-py3-none-any.whl", "yanked": False}]
    }
    cached_combos = [(("py3", "none", "any"), Version("2.31.0"))]
    cached_files = {"requests-2.32.0-py3-none-any.whl"}

    with patch("cache_warmer.proxpi._fetch_index", return_value=("/index", index_data)):
        with patch("cache_warmer.proxpi._trigger_cache") as mock_tc:
            attempted, _ = proxpi._warm_package(
                "http://proxy:5001",
                ["/index"],
                "requests",
                cached_combos,
                False,
                cached_files,
            )
    assert attempted == 0
    mock_tc.assert_not_called()


def test_warm_package_skips_yanked():
    index_data = {
        "files": [{"filename": "requests-2.32.0-py3-none-any.whl", "yanked": True}]
    }
    cached_combos = [(("py3", "none", "any"), Version("2.31.0"))]

    with patch("cache_warmer.proxpi._fetch_index", return_value=("/index", index_data)):
        with patch("cache_warmer.proxpi._trigger_cache") as mock_tc:
            proxpi._warm_package(
                "http://proxy:5001", ["/index"], "requests", cached_combos, False, set()
            )
    mock_tc.assert_not_called()


def test_warm_package_triggers_newer_sdist():
    index_data = {"files": [{"filename": "requests-2.32.0.tar.gz", "yanked": False}]}

    with patch("cache_warmer.proxpi._fetch_index", return_value=("/index", index_data)):
        with patch("cache_warmer.proxpi._trigger_cache", return_value=True) as mock_tc:
            attempted, succeeded = proxpi._warm_package(
                "http://proxy:5001", ["/index"], "requests", [], True, set()
            )
    assert attempted == 1
    assert succeeded == 1
    mock_tc.assert_called_once()
