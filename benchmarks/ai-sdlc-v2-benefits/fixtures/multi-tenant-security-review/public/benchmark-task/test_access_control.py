from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from access_control import Actor, ApprovalRequest, approve_request


def make_request() -> ApprovalRequest:
    return ApprovalRequest(
        request_id="REQ-1001",
        tenant_id="tenant-a",
        requester_id="requester-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


class AccessControlVisibleTests(unittest.TestCase):
    def test_same_tenant_approver_can_approve(self) -> None:
        request = make_request()
        actor = Actor("approver-1", "tenant-a", frozenset({"approver"}))
        result = approve_request(request, actor, audit_log=[])
        self.assertTrue(result.allowed)
        self.assertEqual(request.status, "approved")

    def test_viewer_cannot_approve(self) -> None:
        request = make_request()
        actor = Actor("viewer-1", "tenant-a", frozenset({"viewer"}))
        result = approve_request(request, actor, audit_log=[])
        self.assertFalse(result.allowed)
        self.assertEqual(request.status, "pending")


if __name__ == "__main__":
    unittest.main()
