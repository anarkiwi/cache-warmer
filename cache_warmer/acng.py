import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_sources(distro: str, release: str, arch: str) -> str | None:
    if distro == "ubuntu":
        if arch in ("amd64", "i386"):
            main_host = "archive.ubuntu.com/ubuntu"
            sec_host = "security.ubuntu.com/ubuntu"
        else:
            main_host = "ports.ubuntu.com/ubuntu-ports"
            sec_host = "ports.ubuntu.com/ubuntu-ports"
        return (
            f"deb http://{main_host} {release} main restricted universe multiverse\n"
            f"deb http://{main_host} {release}-updates main restricted universe multiverse\n"
            f"deb http://{main_host} {release}-backports main restricted universe multiverse\n"
            f"deb http://{sec_host} {release}-security main restricted universe multiverse\n"
        )
    if distro == "debian":
        return (
            f"deb http://deb.debian.org/debian {release} main contrib non-free non-free-firmware\n"
            f"deb http://deb.debian.org/debian {release}-updates main contrib non-free non-free-firmware\n"
            f"deb http://deb.debian.org/debian-security {release}-security main contrib non-free non-free-firmware\n"
        )
    if distro == "raspios":
        if arch == "arm64":
            return (
                f"deb http://deb.debian.org/debian {release} main contrib non-free non-free-firmware\n"
                f"deb http://deb.debian.org/debian {release}-updates main contrib non-free non-free-firmware\n"
                f"deb http://deb.debian.org/debian-security {release}-security main contrib non-free non-free-firmware\n"
                f"deb http://archive.raspberrypi.com/debian {release} main\n"
            )
        return (
            f"deb http://raspbian.raspberrypi.com/raspbian {release} main contrib non-free rpi\n"
            f"deb http://archive.raspberrypi.com/debian {release} main\n"
        )
    return None


def _rewrite_sources(sources: str, arch: str) -> str:
    lines = []
    for line in sources.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("deb"):
            after = line[3:].lstrip()
            if after.startswith("["):
                after = after[after.index("]") + 1 :].lstrip()
            lines.append(f"deb [arch={arch} trusted=yes] {after}")
    return "\n".join(lines) + "\n"


def _prewarm(tag: str, arch: str, sources: str, proxy_url: str, work_dir: Path) -> None:
    root = work_dir / f"{tag}.{arch}"
    try:
        for sub in (
            "etc/apt/apt.conf.d",
            "etc/apt/preferences.d",
            "etc/apt/trusted.gpg.d",
            "etc/apt/sources.list.d",
            "var/lib/apt/lists/partial",
            "var/cache/apt/archives/partial",
            "var/lib/dpkg",
            "var/log/apt",
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
        (root / "var/lib/dpkg/status").touch()
        (root / "etc/apt/sources.list").write_text(
            _rewrite_sources(sources, arch), encoding="utf-8"
        )
        (root / "etc/apt/apt.conf.d/99prewarm").write_text(
            f'APT::Architecture "{arch}";\n'
            f'APT::Architectures "{arch}";\n'
            f'Acquire::http::Proxy "{proxy_url}";\n'
            f'Acquire::https::Proxy "{proxy_url}";\n'
            'Acquire::Check-Valid-Until "false";\n'
            'Acquire::AllowInsecureRepositories "true";\n'
            'Acquire::AllowDowngradeToInsecureRepositories "true";\n'
            'APT::Get::AllowUnauthenticated "true";\n'
            'APT::Sandbox::User "root";\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "apt-get",
                f"-o Dir={root}",
                f"-o Dir::State={root}/var/lib/apt",
                f"-o Dir::State::status={root}/var/lib/dpkg/status",
                f"-o Dir::Cache={root}/var/cache/apt",
                f"-o Dir::Etc={root}/etc/apt",
                f"-o Dir::Etc::sourcelist={root}/etc/apt/sources.list",
                f"-o Dir::Etc::sourceparts={root}/etc/apt/sources.list.d",
                f"-o Dir::Etc::trusted={root}/etc/apt/trusted.gpg",
                f"-o Dir::Etc::trustedparts={root}/etc/apt/trusted.gpg.d",
                f"-o Dir::Log={root}/var/log/apt",
                "update",
                "--allow-insecure-repositories",
            ],
            capture_output=True,
            check=False,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("ok tag=%s arch=%s", tag, arch)
        else:
            logger.error("failed tag=%s arch=%s: %s", tag, arch, result.stderr.decode())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run(config) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="acng-prewarm-"))
    try:
        for entry in config.apt_cacher_ng.matrix:
            parts = entry.split(":")
            if len(parts) != 3:
                logger.error("bad matrix entry: %s", entry)
                continue
            distro, release, arch = parts
            sources = generate_sources(distro, release, arch)
            if sources is None:
                logger.error("unknown distro: %s", distro)
                continue
            _prewarm(
                f"{distro}-{release}", arch, sources, config.apt_cacher_ng.url, work_dir
            )

        extras_dir = Path(config.apt_cacher_ng.extras_dir)
        if extras_dir.is_dir():
            for list_file in sorted(extras_dir.glob("*.list")):
                stem = list_file.stem
                arch = stem.rsplit(".", 1)[-1]
                tag = stem.rsplit(".", 1)[0]
                sources = list_file.read_text(encoding="utf-8")
                _prewarm(tag, arch, sources, config.apt_cacher_ng.url, work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
