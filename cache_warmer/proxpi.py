import concurrent.futures
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion

logger = logging.getLogger(__name__)

USER_AGENT = "cache-warmer/1.0"
HTTP_TIMEOUT = 60
EXTRA_PROBE_LIMIT = 32


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.pypi.simple.v1+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def _discover_endpoints(base_url: str) -> list:
    endpoints = ["/index"]
    for n in range(EXTRA_PROBE_LIMIT):
        url = f"{base_url}/extra/{n}/index/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                if 200 <= resp.status < 400:
                    endpoints.append(f"/extra/{n}/index")
                    continue
                break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                break
            logger.warning("probe /extra/%d/ returned %s", n, exc.code)
            break
        except urllib.error.URLError as exc:
            logger.warning("probe /extra/%d/ failed: %s", n, exc)
            break
    return endpoints


def scan_cache(cache_dir: str) -> tuple:
    wheel_combos: dict = {}
    sdist_packages: set = set()
    cached_filenames: set = set()

    root = Path(cache_dir)
    if not root.is_dir():
        return wheel_combos, sdist_packages, cached_filenames

    for packages_dir in root.glob("*/packages"):
        if not packages_dir.is_dir():
            continue
        for path in packages_dir.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if (
                name.endswith(".metadata")
                or name.endswith(".tmp")
                or name.startswith(".")
            ):
                continue
            cached_filenames.add(name)
            try:
                if name.endswith(".whl"):
                    pkg, ver, _, tags = parse_wheel_filename(name)
                    cn = canonicalize_name(pkg)
                    for tag in tags:
                        key = (cn, tag.interpreter, tag.abi, tag.platform)
                        cur = wheel_combos.get(key)
                        if cur is None or ver > cur:
                            wheel_combos[key] = ver
                elif name.endswith((".tar.gz", ".zip")):
                    pkg, _ = parse_sdist_filename(name)
                    sdist_packages.add(canonicalize_name(pkg))
            except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion):
                continue

    return wheel_combos, sdist_packages, cached_filenames


def _fetch_index(base_url: str, endpoints: list, name: str) -> tuple:
    for ep in endpoints:
        url = f"{base_url}{ep}/{urllib.parse.quote(name)}/"
        try:
            data = _http_get_json(url)
            if data.get("files"):
                return ep, data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            logger.warning("index fetch error for %s: %s", name, exc)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            logger.warning("index fetch error for %s: %s", name, exc)
    return None, None


def _trigger_cache(base_url: str, endpoint: str, name: str, filename: str) -> bool:
    url = f"{base_url}{endpoint}/{urllib.parse.quote(name)}/{urllib.parse.quote(filename)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT * 5) as resp:
            while resp.read(1 << 16):
                pass
        return True
    except urllib.error.URLError as exc:
        logger.error("download failed %s: %s", filename, exc)
        return False


def _warm_package(
    base_url: str,
    endpoints: list,
    name: str,
    cached_combos: list,
    has_sdist: bool,
    cached_filenames: set,
) -> tuple:
    endpoint, idx = _fetch_index(base_url, endpoints, name)
    if not idx or not endpoint:
        return 0, 0

    allow_pre = any(v.is_prerelease for _, v in cached_combos)
    by_tag: dict = {}
    latest_sdist: tuple | None = None

    for f in idx.get("files", []):
        if f.get("yanked"):
            continue
        fname = f["filename"]
        try:
            if fname.endswith(".whl"):
                _, ver, _, tags = parse_wheel_filename(fname)
                if ver.is_prerelease and not allow_pre:
                    continue
                for tag in tags:
                    key = (tag.interpreter, tag.abi, tag.platform)
                    cur = by_tag.get(key)
                    if cur is None or ver > cur[0]:
                        by_tag[key] = (ver, fname)
            elif fname.endswith((".tar.gz", ".zip")):
                _, ver = parse_sdist_filename(fname)
                if ver.is_prerelease and not allow_pre:
                    continue
                if latest_sdist is None or ver > latest_sdist[0]:
                    latest_sdist = (ver, fname)
        except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion):
            continue

    attempted = succeeded = 0
    seen: set = set()

    for tag_key, cached_ver in cached_combos:
        match = by_tag.get(tag_key)
        if not match:
            continue
        latest_ver, fname = match
        if fname in seen or fname in cached_filenames or latest_ver <= cached_ver:
            continue
        seen.add(fname)
        attempted += 1
        if _trigger_cache(base_url, endpoint, name, fname):
            succeeded += 1
            logger.info("warmed %s %s tag=%s-%s-%s", name, latest_ver, *tag_key)

    if has_sdist and latest_sdist is not None:
        sdist_ver, fname = latest_sdist
        if fname not in cached_filenames and fname not in seen:
            attempted += 1
            if _trigger_cache(base_url, endpoint, name, fname):
                succeeded += 1
                logger.info("warmed sdist %s %s", name, sdist_ver)

    return attempted, succeeded


def run(config) -> None:
    base_url = config.proxpi.url.rstrip("/")
    endpoints = _discover_endpoints(base_url)
    logger.info("proxpi endpoints: %s", endpoints)

    wheel_combos, sdist_packages, cached_filenames = scan_cache(config.proxpi.cache_dir)
    per_package: dict = {}
    for (cn, py, abi, plat), ver in wheel_combos.items():
        per_package.setdefault(cn, []).append(((py, abi, plat), ver))
    for cn in sdist_packages:
        per_package.setdefault(cn, [])

    logger.info(
        "cache: %d packages, %d wheel combos, %d sdist packages, %d files",
        len(per_package),
        len(wheel_combos),
        len(sdist_packages),
        len(cached_filenames),
    )

    total_a = total_s = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.proxpi.workers) as ex:
        futs = {
            ex.submit(
                _warm_package,
                base_url,
                endpoints,
                pkg,
                combos,
                pkg in sdist_packages,
                cached_filenames,
            ): pkg
            for pkg, combos in sorted(per_package.items())
        }
        for fut in concurrent.futures.as_completed(futs):
            pkg = futs[fut]
            try:
                attempted, succeeded = fut.result()
                total_a += attempted
                total_s += succeeded
            except Exception:
                logger.exception("worker error for %s", pkg)

    logger.info("proxpi done: attempted=%d succeeded=%d", total_a, total_s)
