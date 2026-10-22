#!/usr/bin/env python3
"""Check the owned ACR image and the production ACA revision for the pilot."""

import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


REGISTRY = "acraltairprod"
LOGIN_SERVER = "acraltairprod.azurecr.io"
APP = "ca-peppermint-production"
RESOURCE_GROUP = "rg-altair-prod"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
IMAGE = re.compile(r"acraltairprod\.azurecr\.io/peppermint@sha256:[0-9a-f]{64}\Z")


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def az(*args):
    result = subprocess.run(["az", *args, "-o", "json"], check=True,
                            capture_output=True, text=True, timeout=180)
    return json.loads(result.stdout)


def owned_run_id(path):
    matches = re.findall(r"^Run ID: ([A-Za-z0-9_-]+)$", Path(path).read_text(), re.M)
    require(len(matches) == 1 and bool(RUN_ID.fullmatch(matches[0])),
            "shared ACR guard returned no unique run ID")
    return matches[0]


def owned_digest(run, run_id, sha):
    require(run.get("runId") == run_id and run.get("status") == "Succeeded",
            "ACR run is not the successful owned build")
    images = run.get("outputImages")
    require(isinstance(images, list), "ACR run has no output image list")
    matches = [row for row in images if isinstance(row, dict)
               and row.get("registry") == LOGIN_SERVER
               and row.get("repository") == "peppermint"
               and row.get("tag") == sha]
    require(len(matches) == 1, "ACR run did not identify exactly one commit image")
    digest = matches[0].get("digest")
    require(isinstance(digest, str) and bool(DIGEST.fullmatch(digest)),
            "ACR run image digest is invalid")
    return digest


def immutable_previous(image):
    prefix = f"{LOGIN_SERVER}/peppermint"
    require(image.startswith(prefix), "previous image is outside Peppermint ACR repository")
    suffix = image[len(prefix):]
    if suffix.startswith("@"):
        digest = suffix[1:]
    else:
        require(re.fullmatch(r":[A-Za-z0-9_.-]+", suffix) is not None,
                "previous image tag is invalid")
        metadata = az("acr", "manifest", "show-metadata", "--registry", REGISTRY,
                      "--name", f"peppermint{suffix}")
        digest = metadata.get("digest")
    require(isinstance(digest, str) and bool(DIGEST.fullmatch(digest)),
            "previous image digest could not be pinned")
    return f"{prefix}@{digest}"


def app_state():
    state = az("containerapp", "show", "--name", APP,
               "--resource-group", RESOURCE_GROUP)
    try:
        props = state["properties"]
        image = props["template"]["containers"][0]["image"]
        latest = props["latestRevisionName"]
        ready = props["latestReadyRevisionName"]
        config = props["configuration"]
        fqdn = config["ingress"]["fqdn"]
        mode = config["activeRevisionsMode"]
    except (KeyError, TypeError, IndexError) as exc:
        raise ReleaseError("Container App state is incomplete") from exc
    require(mode == "Single" and config["ingress"].get("external") is True,
            "Peppermint ingress or revision mode differs from reviewed production")
    require(isinstance(image, str) and image and latest and ready and fqdn,
            "Container App state is incomplete")
    require(re.fullmatch(r"[a-z0-9.-]+\.azurecontainerapps\.io", fqdn) is not None,
            "Container App ingress host differs from Azure-managed domain")
    return {"image": image, "latest": latest, "ready": ready, "fqdn": fqdn}


def health_probe(fqdn):
    request = urllib.request.Request(f"https://{fqdn}/", method="GET",
                                     headers={"User-Agent": "altair-circleci-peppermint-release"})
    with urllib.request.urlopen(request, timeout=10) as response:
        # Peppermint currently serves a public HTML page at `/`. A 2xx check
        # complements, but does not replace, ACA's exact revision health state.
        return 200 <= response.status < 300


def wait_ready(image, timeout=600, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = app_state()
        require(current["image"] == image, "another release changed the Container App image")
        revision = current["latest"]
        state = az("containerapp", "revision", "show", "--name", APP,
                   "--resource-group", RESOURCE_GROUP, "--revision", revision)
        props = state.get("properties", {})
        require(props.get("provisioningState") != "Failed", "new revision failed to provision")
        actual_image = (props.get("template", {}).get("containers") or [{}])[0].get("image")
        if (current["ready"] == revision and actual_image == image and
                props.get("provisioningState") == "Provisioned" and
                props.get("healthState") == "Healthy" and props.get("active") is True):
            try:
                if health_probe(current["fqdn"]):
                    return {"revision": revision, "image": image, "health": "Healthy",
                            "http": 200}
            except (urllib.error.URLError, TimeoutError):
                # Ingress can trail revision readiness for a short interval.
                pass
        sleep(10)
    raise ReleaseError("new revision did not become healthy within ten minutes")


def deploy(image):
    require(bool(IMAGE.fullmatch(image)), "candidate image must be an immutable Peppermint digest")
    previous = app_state()
    require(previous["latest"] == previous["ready"],
            "existing production revision is not ready")
    previous_digest_image = immutable_previous(previous["image"])
    # Capture the exact old image and revision immediately before the write.
    receipt = {"candidateImage": image, "previousImage": previous["image"],
               "previousDigestImage": previous_digest_image,
               "previousRevision": previous["latest"], "passed": False,
               "rollback": None}
    artifact = Path("artifacts/peppermint-production-release.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)

    def save():
        artifact.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    save()
    patched = False
    try:
        require(app_state() == previous, "production app changed before update")
        patched = True  # Azure may accept the update even if its CLI later fails.
        az("containerapp", "update", "--name", APP,
           "--resource-group", RESOURCE_GROUP, "--image", image)
        receipt["newRevision"] = wait_ready(image)
        receipt["passed"] = True
        save()
    except Exception:
        if patched:
            try:
                current = app_state()
                if current["image"] == image:
                    az("containerapp", "update", "--name", APP,
                       "--resource-group", RESOURCE_GROUP,
                       "--image", previous_digest_image)
                    receipt["rollback"] = {"restoredImage": previous_digest_image,
                                           "revision": wait_ready(previous_digest_image)}
                else:
                    receipt["rollback"] = "skipped: candidate image no longer current"
            except Exception as exc:
                receipt["rollback"] = f"failed: {type(exc).__name__}: {exc}"
        save()
        raise


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "run-id" and len(sys.argv) == 3:
        print(owned_run_id(sys.argv[2]))
    elif command == "digest" and len(sys.argv) == 4:
        run_id, sha = sys.argv[2:]
        require(bool(RUN_ID.fullmatch(run_id)), "invalid ACR run ID")
        require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "invalid source SHA")
        run = az("acr", "task", "show-run", "--registry", REGISTRY,
                 "--run-id", run_id)
        print(owned_digest(run, run_id, sha))
    elif command == "deploy" and len(sys.argv) == 3:
        deploy(sys.argv[2])
    else:
        raise ReleaseError("expected run-id LOG, digest RUN_ID SHA, or deploy IMAGE")


if __name__ == "__main__":
    try:
        main()
    except (ReleaseError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError, ValueError) as exc:
        print(f"Release refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
