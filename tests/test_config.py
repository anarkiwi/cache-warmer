from cache_warmer import config


def test_load_config(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "schedule:\n"
        "  timezone: Pacific/Auckland\n"
        "  window_start: '01:00'\n"
        "  window_end: '06:00'\n"
        "apt_cacher_ng:\n"
        "  url: http://proxy:3142\n"
        "  matrix:\n"
        "    - ubuntu:noble:amd64\n"
        "  extras_dir: /config/extras\n"
        "proxpi:\n"
        "  url: http://proxy:5001\n"
        "  cache_dir: /var/cache/proxpi\n"
        "  workers: 4\n"
    )
    cfg = config.load(str(cfg_file))
    assert cfg.schedule.timezone == "Pacific/Auckland"
    assert cfg.schedule.window_start == "01:00"
    assert cfg.schedule.window_end == "06:00"
    assert cfg.apt_cacher_ng.url == "http://proxy:3142"
    assert cfg.apt_cacher_ng.matrix == ["ubuntu:noble:amd64"]
    assert cfg.apt_cacher_ng.extras_dir == "/config/extras"
    assert cfg.proxpi.url == "http://proxy:5001"
    assert cfg.proxpi.cache_dir == "/var/cache/proxpi"
    assert cfg.proxpi.workers == 4


def test_load_config_defaults(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "schedule:\n"
        "  timezone: UTC\n"
        "  window_start: '02:00'\n"
        "  window_end: '04:00'\n"
        "apt_cacher_ng:\n"
        "  url: http://proxy:3142\n"
        "proxpi:\n"
        "  url: http://proxy:5001\n"
        "  cache_dir: /tmp/proxpi\n"
    )
    cfg = config.load(str(cfg_file))
    assert not cfg.apt_cacher_ng.matrix
    assert cfg.apt_cacher_ng.extras_dir == "/config/acng-extras"
    assert cfg.proxpi.workers == 8
