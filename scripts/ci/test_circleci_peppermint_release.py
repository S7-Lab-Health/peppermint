import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


path = Path(__file__).with_name("circleci_peppermint_release.py")
spec = importlib.util.spec_from_file_location("peppermint_release", path)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

SHA = "a" * 40
TAG = f"circleci-production-{SHA}-42"
DIGEST = "sha256:" + "b" * 64
IMAGE = f"acraltairprod.azurecr.io/peppermint@{DIGEST}"
OLD = "acraltairprod.azurecr.io/peppermint:old"
OLD_PINNED = "acraltairprod.azurecr.io/peppermint@sha256:" + "c" * 64


class ReleaseTests(unittest.TestCase):
    def test_run_id_must_be_unique(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "build.log"
            log.write_text("Run ID: ch123\n")
            self.assertEqual(release.owned_run_id(log), "ch123")
            log.write_text("Run ID: ch123\nRun ID: ch124\n")
            with self.assertRaises(release.ReleaseError):
                release.owned_run_id(log)

    def test_digest_is_owned_and_exact(self):
        row = {"registry": release.LOGIN_SERVER, "repository": "peppermint",
               "tag": TAG, "digest": DIGEST}
        run = {"runId": "ch123", "status": "Succeeded", "outputImages": [row]}
        self.assertEqual(release.owned_digest(run, "ch123", TAG), DIGEST)
        for mutation in (
            {**run, "runId": "ch124"},
            {**run, "status": "Running"},
            {**run, "outputImages": [row, row]},
            {**run, "outputImages": [{**row, "registry": "other.azurecr.io"}]},
            {**run, "outputImages": [{**row, "digest": "bad"}]},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(release.ReleaseError):
                release.owned_digest(mutation, "ch123", TAG)

    def test_github_digest_is_tied_to_owned_sha_tag(self):
        row = {"registry": release.LOGIN_SERVER, "repository": "peppermint",
               "tag": SHA, "digest": DIGEST}
        run = {"runId": "ch123", "status": "Succeeded", "outputImages": [row]}
        self.assertEqual(release.owned_digest(run, "ch123", SHA), DIGEST)
        with self.assertRaises(release.ReleaseError):
            release.owned_digest(run, "ch123", "c" * 40)

    def test_lost_lease_refuses_production_update(self):
        old = {"image": OLD, "latest": "peppermint--41", "ready": "peppermint--41",
               "fqdn": "peppermint.example.azurecontainerapps.io"}
        with tempfile.TemporaryDirectory() as folder:
            import os
            saved = os.getcwd()
            try:
                os.chdir(folder)
                with patch.object(release, "app_state", side_effect=[old, old]), \
                     patch.object(release, "immutable_previous", return_value=OLD_PINNED), \
                     patch.object(release, "assert_release_lock", side_effect=release.ReleaseError("lost")), \
                     patch.object(release, "az") as az:
                    with self.assertRaises(release.ReleaseError):
                        release.deploy(IMAGE)
                    az.assert_not_called()
            finally:
                os.chdir(saved)

    def test_wait_requires_exact_ready_healthy_revision_and_http(self):
        state = {"image": IMAGE, "latest": "peppermint--42", "ready": "peppermint--42",
                 "fqdn": "peppermint.example.azurecontainerapps.io"}
        revision = {"properties": {"template": {"containers": [{"image": IMAGE}]},
                                   "provisioningState": "Provisioned", "healthState": "Healthy",
                                   "active": True}}
        with patch.object(release, "app_state", return_value=state), \
             patch.object(release, "az", return_value=revision), \
             patch.object(release, "health_probe", return_value=True) as probe:
            result = release.wait_ready(IMAGE)
        self.assertEqual(result["revision"], "peppermint--42")
        probe.assert_called_once()
        revision["properties"]["healthState"] = "Unhealthy"
        with patch.object(release, "app_state", return_value=state), \
             patch.object(release, "az", return_value=revision), \
             patch.object(release, "health_probe") as probe, \
             patch.object(release.time, "monotonic", side_effect=[0, 601]):
            with self.assertRaises(release.ReleaseError):
                release.wait_ready(IMAGE)
            probe.assert_not_called()

    def test_previous_tag_must_resolve_to_immutable_digest(self):
        with patch.object(release, "az", return_value={"digest": DIGEST}):
            self.assertEqual(release.immutable_previous(OLD), IMAGE)
        with patch.object(release, "az", return_value={"digest": "bad"}):
            with self.assertRaises(release.ReleaseError):
                release.immutable_previous(OLD)

    def test_failed_rollout_restores_previous_image_only_if_candidate_current(self):
        old = {"image": OLD, "latest": "peppermint--41", "ready": "peppermint--41",
               "fqdn": "peppermint.example.azurecontainerapps.io"}
        candidate = {**old, "image": IMAGE, "latest": "peppermint--42"}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(release, "app_state", side_effect=[old, old, candidate]), \
             patch.object(release, "immutable_previous", return_value=OLD_PINNED) as pin, \
             patch.object(release, "assert_release_lock") as lock, \
             patch.object(release, "az") as az, \
             patch.object(release, "wait_ready", side_effect=[release.ReleaseError("bad"),
                                                                {"revision": "peppermint--43"}]):
            # deploy() writes relative to cwd; use a temporary working dir.
            import os
            saved = os.getcwd()
            try:
                os.chdir(folder)
                with self.assertRaises(release.ReleaseError):
                    release.deploy(IMAGE)
            finally:
                os.chdir(saved)
            calls = [call.args for call in az.call_args_list if call.args[:2] == ("containerapp", "update")]
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][-1], OLD_PINNED)
            self.assertEqual(lock.call_count, 2)
            pin.assert_called_once_with(OLD)

    def test_concurrent_release_skips_rollback(self):
        old = {"image": OLD, "latest": "peppermint--41", "ready": "peppermint--41",
               "fqdn": "peppermint.example.azurecontainerapps.io"}
        other = {**old, "image": "other.azurecr.io/new"}
        with tempfile.TemporaryDirectory() as folder:
            import os
            saved = os.getcwd()
            try:
                os.chdir(folder)
                with patch.object(release, "app_state", side_effect=[old, old, other]), \
                     patch.object(release, "immutable_previous", return_value=OLD_PINNED), \
                     patch.object(release, "assert_release_lock") as lock, \
                     patch.object(release, "az") as az, \
                     patch.object(release, "wait_ready", side_effect=release.ReleaseError("bad")):
                    with self.assertRaises(release.ReleaseError):
                        release.deploy(IMAGE)
                    calls = [call.args for call in az.call_args_list if call.args[:2] == ("containerapp", "update")]
                    self.assertEqual(len(calls), 1)
                    lock.assert_called_once()
                receipt = (Path("artifacts") / "peppermint-production-release.json").read_text()
                self.assertIn("skipped: candidate image no longer current", receipt)
            finally:
                os.chdir(saved)


if __name__ == "__main__":
    unittest.main()
