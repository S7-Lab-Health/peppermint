#!/usr/bin/env python3
"""Shared ACR admission/cooldown policy. No automatic recovery of uncertain writes."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

SCHEMA = 1
# Deliberately a source gate, not a caller-controlled input. Enable only after
# consumer migration, effective RBAC review, and an isolated Azure rehearsal.
SHUTDOWN_ENABLED = False
ACTIVE = {"Queued", "Started", "Running"}
TERMINAL = {"Succeeded", "Failed", "Canceled", "Error", "Timeout"}
API_VERSION = "2025-03-01-preview"
CALLER_FIELDS = (
    "GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
    "GITHUB_JOB", "GITHUB_SHA", "CIRCLE_PROJECT_USERNAME", "CIRCLE_PROJECT_REPONAME",
    "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM", "CIRCLE_JOB", "CIRCLE_SHA1",
    "ACR_MAINTENANCE_OPERATION",
)


class Refused(RuntimeError):
    pass


class AzureError(Refused):
    def __init__(self, message, busy=False, conflict=False):
        super().__init__(message)
        self.busy = busy
        self.conflict = conflict


class Clock:
    monotonic = staticmethod(time.monotonic)
    now = staticmethod(time.time)
    sleep = staticmethod(time.sleep)


def timestamp(value):
    if not isinstance(value, str):
        raise Refused("Missing completion timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.timestamp()
    except ValueError as exc:
        raise Refused("Invalid completion timestamp") from exc


def record(value):
    if not isinstance(value, dict):
        raise Refused("Invalid Azure object")
    result = value.get("properties", value)
    if not isinstance(result, dict):
        raise Refused("Invalid Azure properties")
    return result


def run_id(value):
    value = record(value).get("runId")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise Refused("Azure returned no identifiable run; do not retry submission")
    return value


class CLI:
    """Capture, bound and redact CLI output. Never log commands containing secrets."""
    def __init__(self, timeout=60):
        self.timeout = timeout

    def call(self, *args, timeout=None, queued_build=False):
        if queued_build and (args[:2] != ("acr", "build") or "--no-wait" not in args):
            raise Refused("Queued receipt capture is restricted to asynchronous ACR builds")
        # Azure CLI's supports_no_wait handler discards the returned run object.
        # Capture its exact receipt privately; never infer ownership from listing
        # the latest run, and never print raw output that may contain secrets.
        output_flags = ["-o", "json"] if queued_build else ["--only-show-errors", "-o", "json"]
        try:
            result = subprocess.run(
                ["az", *args, *output_flags],
                capture_output=True, text=True, timeout=timeout or self.timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise AzureError("Azure CLI failed or timed out; reconcile any pending operation") from exc
        if result.returncode:
            # The raw output can contain SAS URLs, build arguments or tokens.
            busy = any(code in result.stderr for code in (
                "LeaseAlreadyPresent", "LeaseIsBreakingAndCannotBeAcquired"))
            raise AzureError("Azure CLI rejected the operation", busy=busy,
                             conflict="RegistryStatusConflict" in result.stderr)
        if queued_build:
            receipts = re.findall(r"(?m)^(?:WARNING: )?Queued a build with ID: ([A-Za-z0-9_-]+)[ \t]*$",
                                  result.stderr)
            if len(receipts) != 1:
                raise AzureError("Missing or ambiguous ACR build receipt; reconcile pending submission")
            return {"runId": receipts[0]}
        try:
            return json.loads(result.stdout) if result.stdout.strip() else None
        except ValueError as exc:
            raise AzureError("Azure CLI returned malformed JSON") from exc


class Azure:
    def __init__(self, cli, registry, pool):
        self.cli, self.registry, self.pool = cli, registry, pool
        identity = record(cli.call("acr", "show", "--name", registry)).get("id", "")
        if not re.fullmatch(r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/"
                            r"Microsoft.ContainerRegistry/registries/[^/]+", identity, re.I):
            raise Refused("Invalid registry resource identity")
        self.registry_id = identity
        self.pool_id = identity + "/agentPools/" + pool
        self.base = "https://management.azure.com" + identity

    def show_pool(self):
        return self.cli.call("acr", "agentpool", "show", "--registry", self.registry, "--name", self.pool)

    def scale(self, count):
        return self.cli.call("acr", "agentpool", "update", "--registry", self.registry,
                             "--name", self.pool, "--count", str(count), "--no-wait")

    def probe(self):
        task = {"version": "v1.1.0", "steps": [{
            "cmd": "mcr.microsoft.com/azure-cli:latest echo agent-pool-ready",
            "disableWorkingDirectoryOverride": True,
        }]}
        body = {"type": "EncodedTaskRunRequest", "encodedTaskContent":
                base64.b64encode(json.dumps(task).encode()).decode(),
                "platform": {"os": "Linux", "architecture": "amd64"},
                "agentPoolName": self.pool, "timeout": 300, "isArchiveEnabled": False}
        return self.cli.call("rest", "--method", "post", "--url",
                             self.base + "/scheduleRun?api-version=" + API_VERSION,
                             "--body", json.dumps(body))

    def submit(self, args):
        # The CLI suppresses JSON in --no-wait mode; require its exact queued receipt.
        return self.cli.call("acr", "build", "--registry", self.registry,
                             "--agent-pool", self.pool, "--no-wait", *args, timeout=600, queued_build=True)

    def show_run(self, identity):
        return self.cli.call("acr", "task", "show-run", "--registry", self.registry, "--run-id", identity)

    def cancel(self, identity):
        return self.cli.call("acr", "task", "cancel-run", "--registry", self.registry, "--run-id", identity)

    def runs(self):
        # Registry-wide blocking is intentionally conservative if pool attribution
        # is absent. No --top cap, status filtering, or age heuristic.
        url = self.base + "/runs?api-version=" + API_VERSION
        seen, rows = set(), []
        while url:
            if url in seen or not url.startswith(self.base + "/runs?"):
                raise Refused("Invalid/repeated pagination link")
            seen.add(url)
            page = self.cli.call("rest", "--method", "get", "--url", url)
            if not isinstance(page, dict) or not isinstance(page.get("value"), list):
                raise Refused("Incomplete Azure run page")
            rows.extend(page["value"])
            url = page.get("nextLink")
            if url is not None and (not isinstance(url, str) or not url):
                raise Refused("Invalid pagination continuation")
        return rows


class BlobStore:
    """One pre-provisioned journal blob per pool; infinite lease, no auto-break.

    Use explicit bootstrap, never create an empty journal during ordinary builds:
    an accidentally missing journal must not turn a used pool into an idle pool.
    """
    def __init__(self, cli, account, container, pool_id):
        if not account or not container:
            raise Refused("Coordination account/container required; cooldown must remain disabled")
        self.cli = cli
        self.pool_id = pool_id.lower()
        self.name = hashlib.sha256(self.pool_id.encode()).hexdigest() + ".json"
        self.args = ["--account-name", account, "--container-name", container, "--auth-mode", "login"]
        self.lease = None

    def acquire(self):
        candidate = str(uuid.uuid4())
        self.cli.call("storage", "blob", "lease", "acquire", *self.args,
                      "--blob-name", self.name, "--lease-duration", "-1",
                      "--proposed-lease-id", candidate)
        self.lease = candidate

    def release(self):
        self.cli.call("storage", "blob", "lease", "release", *self.args,
                      "--blob-name", self.name, "--lease-id", self.lease)
        self.lease = None

    def assert_owned(self):
        if not self.lease:
            raise Refused("No mutation ownership")
        self.cli.call("storage", "blob", "lease", "renew", *self.args,
                      "--blob-name", self.name, "--lease-id", self.lease)

    def load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            self.cli.call("storage", "blob", "download", *self.args,
                          "--name", self.name, "--file", str(path), "--no-progress")
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise Refused("Unreadable coordination journal") from exc

    def save(self, data):
        if not self.lease:
            raise Refused("Journal write requires ownership")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            path.write_text(json.dumps(data))
            self.cli.call("storage", "blob", "upload", *self.args,
                          "--name", self.name, "--file", str(path), "--overwrite", "true",
                          "--lease-id", self.lease, "--no-progress")

    def initialize(self, now):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            path.write_text(json.dumps(new_journal(self.pool_id, now)))
            self.cli.call("storage", "blob", "upload", *self.args,
                          "--name", self.name, "--file", str(path), "--overwrite", "false",
                          "--if-none-match", "*", "--no-progress")


class RestBlobStore:
    """The same journal protocol for runners without Azure CLI.

    request(method, url, headers, bytes) must return bytes and raise AzureError
    on every failed request. Only a definite lease-already-present error may
    set busy=True. Authentication and private routing belong to the transport.
    """
    def __init__(self, request, account, container, pool_id):
        if not re.fullmatch(r"[a-z0-9]{3,24}", account or "") or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])", container or ""):
            raise Refused("Invalid coordination account/container")
        self.request = request
        self.pool_id = pool_id.lower()
        self.name = hashlib.sha256(self.pool_id.encode()).hexdigest() + ".json"
        self.url = "https://" + account + ".blob.core.windows.net/" + container + "/" + self.name
        self.lease = None

    def call(self, method, headers=None, data=None, lease_action=None):
        headers = dict(headers or {})
        headers["x-ms-version"] = "2023-11-03"
        url = self.url
        if lease_action:
            headers["x-ms-lease-action"] = lease_action
            url += "?comp=lease"
        return self.request(method, url, headers, data)

    def acquire(self):
        candidate = str(uuid.uuid4())
        self.call("PUT", {"x-ms-proposed-lease-id": candidate,
                          "x-ms-lease-duration": "-1"}, b"", "acquire")
        self.lease = candidate

    def release(self):
        self.assert_lease()
        self.call("PUT", {"x-ms-lease-id": self.lease}, b"", "release")
        self.lease = None

    def assert_lease(self):
        if not self.lease:
            raise Refused("No journal ownership")

    def assert_owned(self):
        self.assert_lease()
        self.call("PUT", {"x-ms-lease-id": self.lease}, b"", "renew")

    def load(self):
        try:
            return json.loads(self.call("GET"))
        except (ValueError, TypeError) as exc:
            raise Refused("Unreadable coordination journal") from exc

    def save(self, data):
        self.assert_lease()
        self.call("PUT", {"x-ms-lease-id": self.lease, "x-ms-blob-type": "BlockBlob",
                          "Content-Type": "application/json"}, json.dumps(data).encode())

    def initialize(self, now):
        self.call("PUT", {"If-None-Match": "*", "x-ms-blob-type": "BlockBlob",
                          "Content-Type": "application/json"},
                  json.dumps(new_journal(self.pool_id, now)).encode())


def new_journal(pool_id, now):
    return {"schema": SCHEMA, "pool_id": pool_id.lower(), "pending": None,
            "last_activity": now, "requests": {}, "events": []}


def validate_journal(data, pool_id):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("pool_id") != pool_id.lower():
        raise Refused("Journal identity or policy version mismatch")
    if not isinstance(data.get("requests"), dict) or "pending" not in data:
        raise Refused("Incomplete journal")
    if not isinstance(data.get("events"), list):
        raise Refused("Missing operation audit history")
    value = data.get("last_activity")
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise Refused("Invalid last activity")
    return data


def validate_build_args(args):
    """Keep registry, pool and structured output under our control.

    Allow known build switches only; no shell evaluation. Values may be secrets,
    so do not include arguments in errors, journal or normal output.
    """
    valued = {"--image", "-t", "--file", "-f", "--build-arg", "--secret-build-arg",
              "--platform", "--target", "--timeout", "--auth-mode", "--source-acr-auth-id"}
    flags = {"--no-push"}
    if not isinstance(args, list) or not args or any(not isinstance(a, str) or not a for a in args):
        raise Refused("Build arguments must be a nonempty JSON string array")
    context = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in valued:
            i += 1
            if i >= len(args) or args[i].startswith("--"):
                raise Refused("Missing build option value")
        elif arg in flags:
            pass
        elif arg.startswith("-") or context:
            raise Refused("Unsupported build option or extra context")
        else:
            context = True
        i += 1
    if not context:
        raise Refused("Build context required")
    return args


class CompatibilityStore:
    """Readiness-only compatibility while shutdown is disabled at source.

    This store deliberately does not claim cross-process coordination. It must
    never be used for build admission or enabled cooldown.
    """
    def __init__(self, pool_id):
        self.data = new_journal(pool_id, time.time())
    def acquire(self):
        pass
    def release(self):
        pass
    def assert_owned(self):
        pass
    def load(self):
        return self.data
    def save(self, data):
        self.data = data


class Guard:
    def __init__(self, azure, store, clock=None, wait=600, interval=15, idle=3600,
                 shutdown_enabled=False):
        self.azure, self.store = azure, store
        self.clock = clock or Clock()
        self.wait, self.interval, self.idle = wait, interval, idle
        self.shutdown_enabled = shutdown_enabled

    @contextmanager
    def locked(self):
        deadline = self.clock.monotonic() + self.wait
        while True:
            try:
                self.store.acquire()
                break
            except AzureError as exc:
                if not exc.busy:
                    raise
                self.pause(deadline)
        try:
            data = validate_journal(self.store.load(), self.azure.pool_id)
            yield data
        finally:
            self.store.release()

    def pause(self, deadline):
        left = deadline - self.clock.monotonic()
        if left <= 0:
            raise Refused("Deadline exceeded; unresolved operations remain blocking")
        self.clock.sleep(min(self.interval, left))

    def save(self, data):
        self.store.save(data)

    def begin(self, data, kind, request=None, **details):
        if data["pending"] is not None:
            raise Refused("Unresolved Azure operation requires explicit recovery")
        data["pending"] = {"kind": kind, "request": request, "at": self.clock.now(),
                           "operation_id": str(uuid.uuid4()),
                           "initiator": {k: os.getenv(k, "") for k in CALLER_FIELDS}, **details}
        data["last_activity"] = self.clock.now()
        self.save(data)
        # If ownership is lost after this check, the persisted barrier still
        # forbids successors from issuing mutations. Never auto-clear it.
        self.store.assert_owned()

    def settled(self, data):
        if data["pending"] is None:
            raise Refused("No operation to settle")
        data["events"].append({**data["pending"], "settled_at": self.clock.now()})
        data["pending"] = None
        data["last_activity"] = self.clock.now()
        self.save(data)

    def outstanding_runs(self, data):
        """Reserve a slot for every outstanding run, including invisible admissions.

        Azure run listings do not reliably attribute runs to a pool. Count all
        registry work conservatively, as cooldown does, and deduplicate by ID.
        Never interpret a failed/incomplete read as spare capacity.
        """
        outstanding = set()
        for row in self.azure.runs():
            row = record(row)
            status = row.get("status")
            if status not in ACTIVE | TERMINAL:
                raise Refused("Cannot determine available workers: unknown run status")
            if status in ACTIVE:
                outstanding.add(run_id(row))
        for request in data["requests"].values():
            if not isinstance(request, dict) or request.get("status") not in ACTIVE | TERMINAL:
                raise Refused("Cannot determine available workers: invalid admission")
            identity = run_id({"runId": request.get("run_id")})
            if request["status"] in ACTIVE:
                row = record(self.azure.show_run(identity))
                if run_id(row) != identity or row.get("status") not in ACTIVE | TERMINAL:
                    raise Refused("Cannot determine available workers: uncertain tracked run")
                if row["status"] in ACTIVE:
                    outstanding.add(identity)
        return len(outstanding)

    def ensure_warm(self, data, deadline, admission=False):
        while True:
            pool = record(self.azure.show_pool())
            count, state = pool.get("count"), pool.get("provisioningState")
            if type(count) is not int or count < 0:
                raise Refused("Invalid pool count")
            if state == "Succeeded":
                # Admission and submission share the lease. A queued build
                # reserves capacity before the next caller checks for a slot.
                outstanding = self.outstanding_runs(data) if admission else 0
                if count >= 1 and (not admission or outstanding < count):
                    return
                target = count + 1
                self.begin(data, "scale-up", previous_count=count, requested_count=target,
                           outstanding_runs=outstanding)
                try:
                    self.azure.scale(target)
                except AzureError as exc:
                    if not exc.conflict:
                        raise
                    # Explicit rejection means this request was not accepted.
                    # Compatibility callers may race while shutdown is disabled.
                    self.settled(data)
                    self.pause(deadline)
                    continue
                self.wait_pool(target, deadline)
                self.settled(data)
                return
            if state not in {"Queued", "Creating", "Updating"}:
                raise Refused("Uncertain pool state")
            self.pause(deadline)

    def wait_pool(self, expected, deadline):
        while True:
            snapshot = record(self.azure.show_pool())
            count, state = snapshot.get("count"), snapshot.get("provisioningState")
            if type(count) is not int or count < 0:
                raise Refused("Invalid pool count")
            if state == "Succeeded" and count == expected:
                return
            if state not in {"Succeeded", "Queued", "Creating", "Updating"}:
                raise Refused("Capacity transition is unresolved")
            self.pause(deadline)

    def wait_run(self, identity, deadline):
        while True:
            result = record(self.azure.show_run(identity))
            status = result.get("status")
            if status in TERMINAL:
                return status
            if status not in ACTIVE:
                raise Refused("Unknown ACR run status")
            self.pause(deadline)

    def probe(self, data, deadline):
        self.begin(data, "probe-submit")
        identity = run_id(self.azure.probe())
        data["pending"].update(kind="probe-running", run_id=identity)
        self.save(data)
        try:
            status = self.wait_run(identity, deadline)
        except (Refused, KeyboardInterrupt):
            # Only this probe is owned. Cancellation is a request, not proof of
            # completion; retain the barrier if cleanup cannot settle.
            data["pending"]["kind"] = "probe-cancel"
            self.save(data)
            self.store.assert_owned()
            self.azure.cancel(identity)
            self.wait_run(identity, self.clock.monotonic() + min(self.wait, 60))
            self.settled(data)
            raise
        self.settled(data)
        if status != "Succeeded":
            raise Refused("Readiness probe failed")

    def ready(self):
        deadline = self.clock.monotonic() + self.wait
        with self.locked() as data:
            if data["pending"] is not None:
                raise Refused("Unresolved Azure operation requires explicit recovery")
            self.ensure_warm(data, deadline)
            self.probe(data, deadline)

    def build(self, key, args, caller, monitor=True):
        args = validate_build_args(args)
        if not key or len(key) > 512:
            raise Refused("An explicit bounded idempotency key is required")
        # Hash, never journal secret values. Key must include repository/run/
        # attempt/job/purpose; context must be immutable for a given key.
        fingerprint = hashlib.sha256(json.dumps(args).encode()).hexdigest()
        deadline = self.clock.monotonic() + self.wait
        with self.locked() as data:
            if data["pending"] is not None:
                raise Refused("Unresolved Azure operation requires explicit recovery")
            previous = data["requests"].get(key)
            if previous:
                if previous.get("fingerprint") != fingerprint or previous.get("caller") != caller:
                    raise Refused("Idempotency key reused for a different request")
                identity = run_id({"runId": previous.get("run_id")})
            else:
                self.ensure_warm(data, deadline, admission=True)
                self.probe(data, deadline)
                self.begin(data, "build-submit", key)
                data["requests"][key] = {"fingerprint": fingerprint, "caller": caller,
                                         "status": "Submitting", "run_id": None}
                self.save(data)
                self.store.assert_owned()
                identity = run_id(self.azure.submit(args))
                data["requests"][key].update(run_id=identity, status="Queued")
                self.settled(data)
        print("Run ID: " + identity, flush=True)
        if monitor:
            try:
                status = self.wait_run(identity, deadline)
            except (Refused, KeyboardInterrupt):
                # A runner timeout is not a terminal ACR result. Only the owned
                # run can be cancelled; settle it before clearing the journal.
                self.cancel(key)
                raise
            self.finish(key, status)
            if status != "Succeeded":
                raise Refused("Build completed without success")
        return identity

    def finish(self, key, status):
        if status not in TERMINAL:
            raise Refused("Nonterminal completion")
        with self.locked() as data:
            data["requests"][key]["status"] = status
            data["last_activity"] = self.clock.now()
            self.save(data)

    def cancel(self, key):
        deadline = self.clock.monotonic() + self.wait
        with self.locked() as data:
            request = data["requests"].get(key)
            if not request:
                raise Refused("Cannot cancel an unowned run")
            identity = run_id({"runId": request.get("run_id")})
            status = record(self.azure.show_run(identity)).get("status")
            if status not in TERMINAL:
                if status not in ACTIVE:
                    raise Refused("Unknown ACR run status")
                self.begin(data, "cancel", key)
                self.azure.cancel(identity)
                status = self.wait_run(identity, deadline)
                request["status"] = status
                self.settled(data)
            else:
                request["status"] = status
                data["last_activity"] = self.clock.now()
                self.save(data)

    def idle_check(self, data):
        if data["pending"] is not None:
            raise Refused("Unresolved operation")
        latest = data["last_activity"]
        # Direct reads protect tracked requests from run-list visibility lag.
        for request in data["requests"].values():
            if not isinstance(request, dict) or request.get("status") not in TERMINAL | ACTIVE:
                raise Refused("Pending or invalid admission")
            identity = run_id({"runId": request.get("run_id")})
            if request["status"] not in TERMINAL:
                result = record(self.azure.show_run(identity))
                if result.get("status") not in TERMINAL:
                    raise Refused("Tracked run is active or uncertain")
                latest = max(latest, timestamp(result.get("finishTime")))
        # All registry runs, including unknown pool attribution, block safely.
        for row in self.azure.runs():
            row = record(row)
            if row.get("status") not in TERMINAL:
                raise Refused("Azure run is active or uncertain")
            latest = max(latest, timestamp(row.get("finishTime")))
        if latest > self.clock.now() or self.clock.now() - latest < self.idle:
            raise Refused("Pool has not completed the idle interval")
        pool = record(self.azure.show_pool())
        if pool.get("provisioningState") != "Succeeded" or type(pool.get("count")) is not int or pool["count"] < 0:
            raise Refused("Pool capacity is uncertain")
        return pool["count"]

    def cooldown(self, inspect=False):
        if inspect:
            data = validate_journal(self.store.load(), self.azure.pool_id)
            self.idle_check(data)
            return "eligible-observation-only"
        # Keep schedule operational without a configured coordination store.
        # CLI handles the source gate even before creating Azure/Blob clients.
        with self.locked() as data:
            count = self.idle_check(data)
            if inspect or not self.shutdown_enabled:
                return "eligible-observation-only"
            if count == 0:
                return "already-zero"
            self.begin(data, "scale-down", previous_count=count, requested_count=0)
            self.azure.scale(0)
            self.wait_pool(0, self.clock.monotonic() + self.wait)
            self.settled(data)
            return "scaled-to-zero"


def main():
    # Shell callers pass an argv array after --; never evaluate a command string
    # or serialize credentials through workflow outputs.
    command = sys.argv[1:]
    arguments = None
    if "--" in command:
        boundary = command.index("--")
        command, arguments = command[:boundary], command[boundary + 1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["ready", "build", "cooldown", "inspect", "initialize", "cancel"])
    parser.add_argument("--registry", default=os.getenv("ACR_REGISTRY", "acraltairprod"))
    parser.add_argument("--pool", default=os.getenv("ACR_AGENT_POOL", "agentpool-production"))
    parser.add_argument("--account", default=os.getenv("ACR_COORDINATION_ACCOUNT"))
    parser.add_argument("--container", default=os.getenv("ACR_COORDINATION_CONTAINER"))
    parser.add_argument("--key", default=os.getenv("ACR_REQUEST_KEY", ""))
    parser.add_argument("--args-file", default=os.getenv("ACR_BUILD_ARGS_FILE"))
    parser.add_argument("--wait", type=int, default=int(os.getenv("MAX_WAIT_SECONDS", "7200")))
    parser.add_argument("--interval", type=int, default=int(os.getenv("POLL_INTERVAL_SECONDS", "15")))
    args = parser.parse_args(command)
    if arguments is not None and (args.operation != "build" or args.args_file):
        parser.error("Use either --args-file or build arguments after --")
    if args.wait <= 0 or args.interval <= 0:
        parser.error("wait and interval must be positive")
    if args.operation == "cooldown" and not SHUTDOWN_ENABLED:
        print("Cooldown disabled: consumer migration, effective RBAC and Azure rehearsal required")
        return 0
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    try:
        azure = Azure(CLI(), args.registry, args.pool)
        if args.operation == "ready" and not args.account and not args.container:
            if SHUTDOWN_ENABLED:
                raise Refused("Uncoordinated readiness is disabled with cooldown enabled")
            print("::warning::Readiness-only compatibility mode; this does not protect later builds")
            store = CompatibilityStore(azure.pool_id)
        else:
            store = BlobStore(azure.cli, args.account, args.container, azure.pool_id)
        guard = Guard(azure, store, wait=args.wait, interval=args.interval,
                      shutdown_enabled=SHUTDOWN_ENABLED)
        if args.operation == "ready":
            guard.ready()
        elif args.operation == "initialize":
            store.initialize(time.time())
            print("Coordination journal initialized; shutdown remains disabled")
        elif args.operation == "build":
            if arguments is None and not args.args_file:
                raise Refused("Build requires --args-file containing a JSON argument array")
            if arguments is None:
                arguments = json.loads(Path(args.args_file).read_text())
            caller = {k: os.getenv(k, "") for k in CALLER_FIELDS}
            identity = guard.build(args.key, arguments, caller)
            if os.getenv("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                    output.write("run-id=" + identity + "\n")
        elif args.operation == "cancel":
            guard.cancel(args.key)
        else:
            print(guard.cooldown(inspect=args.operation == "inspect"))
        return 0
    except (Refused, OSError, ValueError, KeyboardInterrupt) as exc:
        # Exception messages from our boundary are safe, but avoid raw file/JSON
        # errors that could disclose input. No raw Azure output is printed.
        print("::error::" + (str(exc) if isinstance(exc, Refused) else "Operation interrupted or input unreadable"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
