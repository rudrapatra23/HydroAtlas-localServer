from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Sequence

import boto3
from botocore.client import Config

from core.config import get_settings
from domain.ports.storage_port import StoragePort


class S3StorageAdapter(StoragePort):
    def __init__(self):
        settings = get_settings()
        # Only pass explicit static credentials / a custom endpoint when
        # configured (local dev against LocalStack). In production
        # (ECS/Fargate) these are left unset and boto3 falls back to the
        # task's IAM role automatically via the default credential chain.
        client_kwargs: dict = {
            "region_name": settings.aws_region,
            "config": Config(signature_version="s3v4"),
        }
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            client_kwargs["aws_access_key_id"] = settings.aws_access_key_id
            client_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url
 
        self.client = boto3.client("s3", **client_kwargs)
        self.bucket_name = settings.s3_bucket_name

    def upload(self, key: str, data: BinaryIO | bytes | Path) -> None:
        if isinstance(data, Path):
            with open(data, "rb") as f:
                self.client.upload_fileobj(f, self.bucket_name, key)
        elif isinstance(data, bytes):
            self.client.put_object(Body=data, Bucket=self.bucket_name, Key=key)
        else:
            self.client.upload_fileobj(data, self.bucket_name, key)

    def download(self, key: str, target: BinaryIO | Path) -> None:
        if isinstance(target, Path):
            self.client.download_fileobj(self.bucket_name, key, open(target, "wb"))
        else:
            self.client.download_fileobj(self.bucket_name, key, target)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except self.client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket_name, Key=key)

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )

    def list(self, prefix: str = "") -> Sequence[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            if "Contents" in page:
                for obj in page["Contents"]:
                    keys.append(obj["Key"])
        return keys
