"""Run the trusted-base emergency contract from a CircleCI PR-label event."""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


def require_event():
    repo = os.environ["GITHUB_REPOSITORY"]
    owner, name = repo.split("/", 1)
    if repo != "S7-Lab-Health/peppermint":
        raise RuntimeError("Unexpected GitHub repository")
    if os.environ.get("CIRCLE_PROJECT_ID") != os.environ["EXPECTED_CIRCLE_PROJECT_ID"]:
        raise RuntimeError("CircleCI project ID does not match the release project")
    if os.environ.get("EXPECTED_EVENT_ACTION") != "labeled":
        raise RuntimeError("Only pull request label events are supported")
    if os.environ.get("EXPECTED_EVENT_LABEL") != "emergency":
        raise RuntimeError("Only the emergency label is supported")
    branch = os.environ["EXPECTED_BASE_REF"]
    if branch not in ("main", "preprod") or os.environ.get("CIRCLE_BRANCH") != branch:
        raise RuntimeError("CircleCI must check out the trusted PR base branch")
    sha = os.environ["EXPECTED_BASE_SHA"]
    head = os.environ["EXPECTED_HEAD_SHA"]
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (sha, head)):
        raise RuntimeError("Invalid PR revision")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise RuntimeError("CircleCI checkout differs from the PR base commit")
    number = os.environ["PR_NUMBER"]
    if not number.isdecimal() or int(number) < 1:
        raise RuntimeError("Invalid PR number")
    return repo, name, number


def b64url(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def github_json(path, bearer, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        "https://api.github.com" + path,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + bearer,
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def installation_token(repo, name):
    app_id = os.environ["GH_APP_ID"]
    if not app_id.isdecimal():
        raise RuntimeError("Invalid GitHub App ID")
    private_key = os.environ["GH_APP_PRIVATE_KEY"]
    now = int(time.time())
    header = b64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    signed = header + "." + payload
    with tempfile.TemporaryDirectory(prefix="circleci-emergency-") as directory:
        keyfile = Path(directory) / "app.pem"
        keyfile.write_text(private_key)
        keyfile.chmod(0o600)
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(keyfile)],
            input=signed.encode(),
            capture_output=True,
            check=True,
        ).stdout
    jwt = signed + "." + b64url(signature)
    installation = github_json(f"/repos/{repo}/installation", jwt)["id"]
    result = github_json(
        f"/app/installations/{installation}/access_tokens",
        jwt,
        {"repositories": [name]},
    )
    return result["token"]


def main():
    repo, name, _ = require_event()
    if os.environ.get("EMERGENCY_MERGE_ENABLED") != "true":
        print("Trusted emergency label event verified; merge remains disabled")
        return
    token = installation_token(repo, name)
    os.environ.pop("GH_APP_PRIVATE_KEY", None)
    os.environ["GH_READ_TOKEN"] = token
    os.environ["GH_MERGE_TOKEN"] = token
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import emergency_pr

    emergency_pr.main()


if __name__ == "__main__":
    main()
