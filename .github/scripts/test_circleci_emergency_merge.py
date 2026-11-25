"""Safety checks for the CircleCI emergency merge entrypoint."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import circleci_emergency_merge as circle

BASE = "a" * 40
HEAD = "b" * 40
ENV = {
    "GITHUB_REPOSITORY": "S7-Lab-Health/peppermint",
    "CIRCLE_PROJECT_ID": "project-id",
    "EXPECTED_CIRCLE_PROJECT_ID": "project-id",
    "EXPECTED_EVENT_ACTION": "labeled",
    "EXPECTED_EVENT_LABEL": "emergency",
    "EXPECTED_BASE_REF": "preprod",
    "CIRCLE_BRANCH": "preprod",
    "EXPECTED_BASE_SHA": BASE,
    "EXPECTED_HEAD_SHA": HEAD,
    "PR_NUMBER": "7",
}


class Contract(unittest.TestCase):
    def test_native_config_is_scoped(self):
        config = (Path(__file__).resolve().parents[2] / '.circleci/emergency.yml').read_text()
        self.assertIn('EXPECTED_CIRCLE_PROJECT_ID: 0706e010-61eb-496b-a0e1-d7a0e96a7b6a', config)
        self.assertIn('GITHUB_REPOSITORY: S7-Lab-Health/peppermint', config)
        self.assertIn('context: altair-emergency-merge', config)
        self.assertIn('pipeline.event.action == "labeled"', config)
        self.assertIn('pipeline.event.github.label.name == "emergency"', config)

    def test_trusted_base_event(self):
        with (
            patch.dict(os.environ, ENV, clear=True),
            patch.object(circle.subprocess, "check_output", return_value=BASE + "\n"),
        ):
            self.assertEqual(
                circle.require_event(), (ENV["GITHUB_REPOSITORY"], "peppermint", "7")
            )

    def test_rejects_untrusted_event_and_checkout(self):
        changes = (
            ("EXPECTED_EVENT_ACTION", "synchronize"),
            ("EXPECTED_EVENT_LABEL", "run-ci"),
            ("CIRCLE_BRANCH", "feature"),
            ("EXPECTED_HEAD_SHA", "short"),
            ("CIRCLE_PROJECT_ID", "another-project"),
            ("PR_NUMBER", "7; echo bad"),
        )
        for key, value in changes:
            with (
                self.subTest(key=key),
                patch.dict(os.environ, ENV | {key: value}, clear=True),
                patch.object(circle.subprocess, "check_output", return_value=BASE + "\n"),
            ):
                with self.assertRaises(RuntimeError):
                    circle.require_event()
        with (
            patch.dict(os.environ, ENV, clear=True),
            patch.object(circle.subprocess, "check_output", return_value=HEAD + "\n"),
        ):
            with self.assertRaises(RuntimeError):
                circle.require_event()

    def test_disabled_probe_never_mints_token(self):
        with (
            patch.dict(os.environ, ENV | {"EMERGENCY_MERGE_ENABLED": "false"}, clear=True),
            patch.object(
                circle,
                "require_event",
                return_value=(ENV["GITHUB_REPOSITORY"], "peppermint", "7"),
            ),
            patch.object(circle, "installation_token") as mint,
        ):
            circle.main()
            mint.assert_not_called()

    def test_installation_token_is_scoped_to_one_repository(self):
        requests = []

        def fake_github(path, bearer, body=None):
            requests.append((path, body))
            return {"id": 42} if path.endswith("/installation") else {"token": "fake-token"}

        with (
            patch.dict(
                os.environ, {"GH_APP_ID": "123", "GH_APP_PRIVATE_KEY": "fake-key"}, clear=True
            ),
            patch.object(circle.subprocess, "run") as signing,
            patch.object(circle, "github_json", side_effect=fake_github),
        ):
            signing.return_value.stdout = b"signature"
            self.assertEqual(
                circle.installation_token("S7-Lab-Health/peppermint", "peppermint"),
                "fake-token",
            )
        self.assertEqual(requests[0][0], "/repos/S7-Lab-Health/peppermint/installation")
        self.assertEqual(
            requests[1],
            ("/app/installations/42/access_tokens", {"repositories": ["peppermint"]}),
        )


if __name__ == "__main__":
    unittest.main()
