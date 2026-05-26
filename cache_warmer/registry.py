import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

UPSTREAM_URL = "https://registry-1.docker.io"
AUTH_URL = "https://auth.docker.io/token"
HTTP_TIMEOUT = 60
MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)
USER_AGENT = "cache-warmer/1.0"


def scan_cache(registry_dir: str) -> dict:
    """Return {(image, tag): digest} for all cached image:tag pairs."""
    repos_dir = Path(registry_dir) / "docker/registry/v2/repositories"
    result = {}
    if not repos_dir.is_dir():
        return result
    for link_file in repos_dir.rglob("_manifests/tags/*/current/link"):
        parts = link_file.parts
        repos_idx = len(repos_dir.parts)
        try:
            mfst_idx = parts.index("_manifests", repos_idx)
        except ValueError:
            continue
        image = "/".join(parts[repos_idx:mfst_idx])
        tag = parts[mfst_idx + 2]
        try:
            digest = link_file.read_text().strip()
        except OSError:
            continue
        result[(image, tag)] = digest
    return result


def _get_token(image: str) -> str | None:
    scope = f"repository:{urllib.parse.quote(image, safe='/')}:pull"
    url = f"{AUTH_URL}?service=registry.docker.io&scope={scope}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.load(resp)
        return data.get("token") or data.get("access_token")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("token fetch failed for %s: %s", image, exc)
        return None


def get_upstream_digest(image: str, tag: str) -> str | None:
    """Return the current manifest digest from Docker Hub."""
    token = _get_token(image)
    if not token:
        return None
    url = f"{UPSTREAM_URL}/v2/{urllib.parse.quote(image, safe='/')}/manifests/{urllib.parse.quote(tag)}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": MANIFEST_ACCEPT,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
            return resp.headers.get("Docker-Content-Digest")
    except urllib.error.HTTPError as exc:
        logger.warning("upstream manifest failed %s:%s: %d", image, tag, exc.code)
        return None
    except urllib.error.URLError as exc:
        logger.warning("upstream manifest failed %s:%s: %s", image, tag, exc)
        return None


def _invalidate_tag(registry_dir: str, image: str, tag: str) -> bool:
    """Delete current/link so the pull-through cache re-fetches on the next request."""
    current_link = (
        Path(registry_dir)
        / "docker/registry/v2/repositories"
        / image
        / "_manifests/tags"
        / tag
        / "current/link"
    )
    try:
        current_link.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.error("could not invalidate %s:%s: %s", image, tag, exc)
        return False


def _pull_manifest(local_url: str, image: str, tag: str) -> str | None:
    """Pull manifest through the local registry, returning the digest it served."""
    url = (
        f"{local_url}/v2/{urllib.parse.quote(image, safe='/')}/"
        f"manifests/{urllib.parse.quote(tag)}"
    )
    req = urllib.request.Request(
        url, headers={"Accept": MANIFEST_ACCEPT, "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT * 5) as resp:
            resp.read()
            return resp.headers.get("Docker-Content-Digest")
    except urllib.error.URLError as exc:
        logger.error("pull-through failed %s:%s: %s", image, tag, exc)
        return None


def run(config) -> None:
    registry_dir = config.docker_registry.cache_dir
    local_url = config.docker_registry.url.rstrip("/")
    cached = scan_cache(registry_dir)
    logger.info("docker registry: %d cached image:tag pairs", len(cached))

    refreshed = skipped = failed = 0
    for (image, tag), cached_digest in sorted(cached.items()):
        upstream_digest = get_upstream_digest(image, tag)
        if upstream_digest is None:
            failed += 1
            continue
        if upstream_digest == cached_digest:
            skipped += 1
            continue
        logger.info(
            "update available %s:%s %s -> %s",
            image,
            tag,
            cached_digest[:19],
            upstream_digest[:19],
        )
        if not _invalidate_tag(registry_dir, image, tag):
            failed += 1
            continue
        new_digest = _pull_manifest(local_url, image, tag)
        if new_digest == upstream_digest:
            refreshed += 1
            logger.info("refreshed %s:%s %s", image, tag, new_digest[:19])
        else:
            logger.warning(
                "digest mismatch after refresh %s:%s got=%s want=%s",
                image,
                tag,
                new_digest,
                upstream_digest,
            )
            failed += 1

    logger.info(
        "docker registry done: refreshed=%d skipped=%d failed=%d",
        refreshed,
        skipped,
        failed,
    )
