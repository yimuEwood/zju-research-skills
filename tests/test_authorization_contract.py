from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "zju-research-director" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class AuthorizationReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_module("director_authorization_contract_test", SCRIPTS / "authorization_contract.py")

    def receipt_and_decision(self):
        decision = {
            "decision_id": "DEC-01",
            "approval_receipt_id": "APR-01",
            "authorized_by": "Example PI",
            "authority_role": "principal_investigator",
            "target": "submission package",
            "action": "release",
            "scope": {"mission_id": "M-01", "artifact_ids": ["A-01"]},
            "state_sha256": "a" * 64,
            "issued_at": "2026-08-11T10:00:00+08:00",
            "expires_at": "2099-08-11T10:00:00+08:00",
        }
        receipt = {
            "schema_version": "1.0",
            "receipt_id": "APR-01",
            "decision_id": "DEC-01",
            "gate": "submission_release",
            "mission_id": "M-01",
            "authorized_by": "Example PI",
            "authority_role": "principal_investigator",
            "target": "submission package",
            "action": "release",
            "scope": {"mission_id": "M-01", "artifact_ids": ["A-01"]},
            "state_sha256": "a" * 64,
            "issued_at": "2026-08-11T10:00:00+08:00",
            "expires_at": "2099-08-11T10:00:00+08:00",
            "source_channel": "user_confirmed_input",
            "source_reference": "interactive-confirmation-01",
        }
        receipt["payload_sha256"] = self.contract.receipt_sha256(receipt)
        return receipt, decision

    def test_mission_decision_cannot_approve_itself(self):
        _, decision = self.receipt_and_decision()
        accepted, issues = self.contract.verify_trusted_receipt(
            decision,
            mission_id="M-01",
            gate="submission_release",
            expected_state_sha256="a" * 64,
            trusted_receipts=None,
            expected_artifact_ids=["A-01"],
            now=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
        )
        self.assertIsNone(accepted)
        self.assertTrue(any("trusted receipt channel" in item for item in issues))

    def test_receipt_is_bound_to_decision_state_and_release_scope(self):
        receipt, decision = self.receipt_and_decision()
        accepted, issues = self.contract.verify_trusted_receipt(
            decision,
            mission_id="M-01",
            gate="submission_release",
            expected_state_sha256="a" * 64,
            trusted_receipts=[receipt],
            expected_artifact_ids=["A-01"],
            now=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(issues, [])
        self.assertEqual(accepted["receipt_id"], "APR-01")

        tampered = copy.deepcopy(receipt)
        tampered["scope"]["artifact_ids"] = ["A-02"]
        rejected, issues = self.contract.verify_trusted_receipt(
            decision,
            mission_id="M-01",
            gate="submission_release",
            expected_state_sha256="a" * 64,
            trusted_receipts=[tampered],
            expected_artifact_ids=["A-01"],
            now=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
        )
        self.assertIsNone(rejected)
        self.assertTrue(any("payload_sha256" in item or "scope" in item for item in issues))

    def test_wrong_gate_role_and_future_issuance_are_rejected(self):
        receipt, decision = self.receipt_and_decision()
        receipt["authority_role"] = "ethics_committee"
        decision["authority_role"] = "ethics_committee"
        receipt["issued_at"] = "2098-01-01T00:00:00+00:00"
        decision["issued_at"] = receipt["issued_at"]
        receipt["payload_sha256"] = self.contract.receipt_sha256(receipt)
        accepted, issues = self.contract.verify_trusted_receipt(
            decision,
            mission_id="M-01",
            gate="submission_release",
            expected_state_sha256="a" * 64,
            trusted_receipts=[receipt],
            expected_artifact_ids=["A-01"],
            now=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
        )
        self.assertIsNone(accepted)
        self.assertTrue(any("not allowed" in item for item in issues))
        self.assertTrue(any("future" in item for item in issues))


if __name__ == "__main__":
    unittest.main()
