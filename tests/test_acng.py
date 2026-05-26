from unittest.mock import MagicMock, patch

from cache_warmer import acng


def test_generate_sources_ubuntu_amd64():
    src = acng.generate_sources("ubuntu", "noble", "amd64")
    assert src is not None
    assert "archive.ubuntu.com/ubuntu" in src
    assert "noble-updates" in src
    assert "security.ubuntu.com" in src


def test_generate_sources_ubuntu_arm64():
    src = acng.generate_sources("ubuntu", "noble", "arm64")
    assert src is not None
    assert "ports.ubuntu.com" in src


def test_generate_sources_debian():
    src = acng.generate_sources("debian", "bookworm", "amd64")
    assert src is not None
    assert "deb.debian.org/debian" in src
    assert "bookworm-security" in src


def test_generate_sources_raspios_arm64():
    src = acng.generate_sources("raspios", "bookworm", "arm64")
    assert src is not None
    assert "archive.raspberrypi.com" in src
    assert "deb.debian.org" in src


def test_generate_sources_raspios_armhf():
    src = acng.generate_sources("raspios", "bookworm", "armhf")
    assert src is not None
    assert "raspbian.raspberrypi.com" in src


def test_generate_sources_unknown_distro():
    assert acng.generate_sources("notadistro", "release", "amd64") is None


def test_rewrite_sources_strips_existing_options():
    src = "deb [arch=amd64] http://example.com/repo release main\n"
    result = acng._rewrite_sources(src, "arm64")
    assert "[arch=arm64 trusted=yes]" in result
    assert "[arch=amd64]" not in result


def test_rewrite_sources_skips_comments_and_blanks():
    src = "# comment\n\ndeb http://example.com/repo release main\n"
    result = acng._rewrite_sources(src, "amd64")
    assert "# comment" not in result
    assert "deb [arch=amd64 trusted=yes] http://example.com/repo release main" in result


def test_prewarm_calls_apt_get(tmp_path):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        acng._prewarm(
            "ubuntu-noble",
            "amd64",
            "deb http://example.com r main\n",
            "http://proxy:3142",
            tmp_path,
        )
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "apt-get"
    assert "update" in cmd


def test_prewarm_logs_error_on_failure(tmp_path):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"error msg")
        acng._prewarm(
            "ubuntu-noble",
            "amd64",
            "deb http://example.com r main\n",
            "http://proxy:3142",
            tmp_path,
        )
    mock_run.assert_called_once()


def test_run_skips_bad_matrix_entry():
    cfg = MagicMock()
    cfg.apt_cacher_ng.matrix = ["bad-entry"]
    cfg.apt_cacher_ng.extras_dir = "/nonexistent"
    cfg.apt_cacher_ng.url = "http://proxy:3142"
    with patch("cache_warmer.acng._prewarm") as mock_pw:
        acng.run(cfg)
    mock_pw.assert_not_called()


def test_run_skips_unknown_distro():
    cfg = MagicMock()
    cfg.apt_cacher_ng.matrix = ["unknown:release:amd64"]
    cfg.apt_cacher_ng.extras_dir = "/nonexistent"
    cfg.apt_cacher_ng.url = "http://proxy:3142"
    with patch("cache_warmer.acng._prewarm") as mock_pw:
        acng.run(cfg)
    mock_pw.assert_not_called()


def test_run_processes_matrix_and_extras(tmp_path):
    extras = tmp_path / "extras"
    extras.mkdir()
    (extras / "myrepo.amd64.list").write_text("deb http://example.com r main\n")

    cfg = MagicMock()
    cfg.apt_cacher_ng.matrix = ["ubuntu:noble:amd64"]
    cfg.apt_cacher_ng.extras_dir = str(extras)
    cfg.apt_cacher_ng.url = "http://proxy:3142"

    calls = []
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        acng.run(cfg)
        calls = mock_run.call_args_list

    assert len(calls) == 2
