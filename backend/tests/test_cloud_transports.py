"""Focused FCM delivery and private R2 lifecycle checks; no live messages."""

from __future__ import annotations

import io
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.common.object_storage import ObjectStorage
from app.core.clock import utc_now
from app.core.security import SecretHasher
from app.exports.models import ExportJob, ExportKind, ExportStatus
from app.exports.service import ExportService
from app.notifications.transport import DeliveryOutcome, FcmPushTransport, PushMessage


@pytest.mark.parametrize(
    "status,code,outcome",
    [
        (200, None, DeliveryOutcome.SENT),
        (404, "UNREGISTERED", DeliveryOutcome.REJECTED),
        (429, "QUOTA_EXCEEDED", DeliveryOutcome.FAILED),
        (401, "THIRD_PARTY_AUTH_ERROR", DeliveryOutcome.FAILED),
    ],
)
async def test_fcm_http_and_failure_classification(settings, monkeypatch, status, code, outcome):
    monkeypatch.setattr(settings, "fcm_project_id", "test-project")
    monkeypatch.setattr(
        settings,
        "fcm_credentials_json",
        SecretStr(
            json.dumps(
                {
                    "type": "service_account",
                    "project_id": "test-project",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            )
        ),
    )
    credentials = SimpleNamespace(valid=True, token="test-oauth-token")
    monkeypatch.setattr(
        "app.notifications.transport.service_account.Credentials.from_service_account_info",
        lambda *a, **kw: credentials,
    )

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-oauth-token"
        body = json.loads(request.content)["message"]
        assert body["data"]["deep_link"] == "/orders/test"
        assert body["token"] == "a-valid-test-registration-token"
        payload = (
            {"name": "projects/test-project/messages/test"}
            if status == 200
            else {
                "error": {
                    "message": "private provider diagnostics",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                            "errorCode": code,
                        }
                    ],
                }
            }
        )
        return httpx.Response(status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = FcmPushTransport(settings, client=client)
        result = await transport.send(
            PushMessage(
                token="a-valid-test-registration-token",
                title="Order updated",
                body="Open the app",
                deep_link="/orders/test",
            )
        )
    assert result.outcome == outcome
    assert "private" not in (result.detail or "")


def test_fcm_malformed_config_fails_safely(settings, monkeypatch):
    monkeypatch.setattr(settings, "fcm_credentials_json", SecretStr('{"private_key":"do-not-log"}'))
    with pytest.raises(ValueError, match="valid matching service account") as caught:
        FcmPushTransport(settings)
    assert "do-not-log" not in str(caught.value)


@pytest.fixture
def r2(settings, monkeypatch):
    for key, value in {
        "r2_bucket": "test-bucket",
        "r2_endpoint_url": "https://test.r2.cloudflarestorage.com",
        "r2_access_key_id": SecretStr("test-access-key"),
        "r2_secret_access_key": SecretStr("test-secret"),
    }.items():
        monkeypatch.setattr(settings, key, value)
    return settings


async def test_r2_private_roundtrip_and_tenant_deletion(r2, monkeypatch):
    client = MagicMock()
    client.get_object.return_value = {"Body": io.BytesIO(b"content")}
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "imports/tenant/file"}]}
    ]
    client.delete_objects.return_value = {}
    monkeypatch.setattr(ObjectStorage, "_client", lambda self: client)
    store = ObjectStorage(r2)
    tenant = uuid.uuid4()
    key = store.key(tenant, "exports", uuid.uuid4())
    await store.put(key, b"content")
    assert await store.get(key) == b"content"
    await store.delete(key)
    assert "ACL" not in client.put_object.call_args.kwargs
    client.delete_object.assert_called_once_with(Bucket="test-bucket", Key=key)
    await store.delete_tenant(tenant)
    prefixes = [
        c.kwargs["Prefix"] for c in client.get_paginator.return_value.paginate.call_args_list
    ]
    assert prefixes == [f"imports/{tenant}/", f"exports/{tenant}/"]
    assert client.close.call_count == 4


async def test_export_uses_r2_and_expiry_removes_object(r2, monkeypatch):
    db = AsyncMock()
    hasher = SecretHasher(r2)
    service = ExportService(db, settings=r2, hasher=hasher)
    service._collect = AsyncMock(return_value=(["name"], [["Test"]]))
    monkeypatch.setattr("app.exports.service.record_audit", AsyncMock())
    put, get, delete = AsyncMock(), AsyncMock(return_value=b"csv"), AsyncMock()
    monkeypatch.setattr(ObjectStorage, "put", put)
    monkeypatch.setattr(ObjectStorage, "get", get)
    monkeypatch.setattr(ObjectStorage, "delete", delete)
    tenant = uuid.uuid4()
    job = ExportJob(id=uuid.uuid4(), tenant_id=tenant, kind=ExportKind.ORDERS, download_count=0)
    token = await service.build(job, tenant_id=tenant)
    key = job.storage_key
    assert job.content is None and key and job.is_downloadable()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    result.scalars.return_value.all.return_value = [job]
    db.execute.return_value = result
    _, content = await service.download(tenant_id=tenant, job_id=job.id, token=token)
    assert content == b"csv"
    job.expires_at = utc_now() - timedelta(seconds=1)
    assert await service.expire_stale() == 1
    delete.assert_awaited_once_with(key)
    assert job.status == ExportStatus.EXPIRED and job.storage_key is None
