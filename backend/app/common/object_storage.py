"""Private R2 objects; business authorization stays in the calling services."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import closing
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from app.core.config import Settings


class ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.r2_configured:
            raise RuntimeError("R2 application credentials are required")
        self._settings = settings

    @staticmethod
    def key(tenant_id: uuid.UUID, kind: str, object_id: uuid.UUID) -> str:
        if kind not in {"imports", "exports", "payouts"}:
            raise ValueError("Invalid object category")
        return f"{kind}/{tenant_id}/{object_id}"

    def _client(self) -> Any:
        settings = self._settings
        assert settings.r2_access_key_id and settings.r2_secret_access_key  # noqa: S101
        return boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            region_name="auto",
            aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=20,
                retries={"max_attempts": 2, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    async def _request(self, method: str, key: str, **kwargs: Any) -> Any:
        def run() -> Any:
            with closing(self._client()) as client:
                result = getattr(client, method)(Bucket=self._settings.r2_bucket, Key=key, **kwargs)
                if method == "get_object":
                    with result["Body"] as body:
                        return body.read()
                return result

        try:
            return await asyncio.to_thread(run)
        except (BotoCoreError, ClientError):
            raise RuntimeError("R2 object operation failed") from None

    async def put(
        self, key: str, content: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        await self._request("put_object", key, Body=content, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        return bytes(await self._request("get_object", key))

    async def delete(self, key: str) -> None:
        await self._request("delete_object", key)

    async def delete_tenant(self, tenant_id: uuid.UUID) -> None:
        """Remove personal imports/exports; payout evidence follows financial retention."""

        def run() -> None:
            with closing(self._client()) as client:
                for kind in ("imports", "exports"):
                    pages = client.get_paginator("list_objects_v2").paginate(
                        Bucket=self._settings.r2_bucket, Prefix=f"{kind}/{tenant_id}/"
                    )
                    for page in pages:
                        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                        if objects:
                            result = client.delete_objects(
                                Bucket=self._settings.r2_bucket,
                                Delete={"Objects": objects, "Quiet": True},
                            )
                            if result.get("Errors"):
                                raise RuntimeError("R2 deletion incomplete; retry required")

        try:
            await asyncio.to_thread(run)
        except (BotoCoreError, ClientError):
            raise RuntimeError("R2 account deletion failed; retry required") from None
