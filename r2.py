"""
r2.py — Cloudflare R2 (S3-compatible) storage helpers.

Required env vars:
  R2_ENDPOINT_URL      https://<account_id>.r2.cloudflarestorage.com
  R2_ACCESS_KEY_ID     R2 API token key ID
  R2_SECRET_ACCESS_KEY R2 API token secret
  R2_BUCKET_NAME       Bucket name
  R2_PUBLIC_URL        Public bucket URL base (e.g. https://pub-xxx.r2.dev)
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

R2_ENDPOINT_URL      = os.environ.get("R2_ENDPOINT_URL", "")
R2_ACCESS_KEY_ID     = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME       = os.environ.get("R2_BUCKET_NAME", "")
R2_PUBLIC_URL        = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

_CONTENT_TYPES = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
}


def is_configured() -> bool:
    return bool(
        R2_ENDPOINT_URL
        and R2_ACCESS_KEY_ID
        and R2_SECRET_ACCESS_KEY
        and R2_BUCKET_NAME
    )


def _client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def object_url(key: str) -> str:
    """Return the public URL for an R2 object key."""
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{key}"
    return ""


def upload_file(local_path: Path, key: str) -> bool:
    """Upload a local file to R2. Returns True on success."""
    content_type = _CONTENT_TYPES.get(local_path.suffix.lower(), "application/octet-stream")
    try:
        _client().upload_file(
            str(local_path),
            R2_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        log.info("R2 upload OK: %s", key)
        return True
    except Exception as exc:
        log.error("R2 upload failed for %s: %s", key, exc)
        return False


def list_objects(prefix: str = "") -> list:
    """
    Return list of dicts: {key, last_modified, size}.
    Excludes the name_map.json meta file.
    """
    try:
        client = _client()
        results = []
        kwargs = {"Bucket": R2_BUCKET_NAME}
        if prefix:
            kwargs["Prefix"] = prefix
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if k == "name_map.json":
                    continue
                results.append({
                    "key": k,
                    "last_modified": obj["LastModified"],
                    "size": obj["Size"],
                })
        return results
    except Exception as exc:
        log.error("R2 list_objects failed: %s", exc)
        return []


def delete_object(key: str) -> bool:
    try:
        _client().delete_object(Bucket=R2_BUCKET_NAME, Key=key)
        log.info("R2 delete: %s", key)
        return True
    except Exception as exc:
        log.error("R2 delete failed for %s: %s", key, exc)
        return False


def delete_folder(prefix: str) -> bool:
    """Delete all objects under a folder prefix."""
    # Ensure prefix ends with / so we don't accidentally match unrelated keys
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    objects = list_objects(prefix)
    if not objects:
        return True
    try:
        client = _client()
        client.delete_objects(
            Bucket=R2_BUCKET_NAME,
            Delete={"Objects": [{"Key": obj["key"]} for obj in objects]},
        )
        log.info("R2 delete_folder: %s (%d objects)", prefix, len(objects))
        return True
    except Exception as exc:
        log.error("R2 delete_folder failed for %s: %s", prefix, exc)
        return False


def get_object_bytes(key: str) -> Optional[bytes]:
    try:
        resp = _client().get_object(Bucket=R2_BUCKET_NAME, Key=key)
        return resp["Body"].read()
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            return None
        log.error("R2 get_object failed for %s: %s", key, exc)
        return None
    except Exception as exc:
        log.error("R2 get_object failed for %s: %s", key, exc)
        return None


# ── name_map stored as name_map.json in R2 root ──────────────────────────────

def get_name_map() -> dict:
    data = get_object_bytes("name_map.json")
    if data:
        try:
            return json.loads(data)
        except Exception:
            pass
    return {}


def put_name_map(name_map: dict) -> None:
    data = json.dumps(name_map, indent=2).encode()
    try:
        _client().put_object(
            Bucket=R2_BUCKET_NAME,
            Key="name_map.json",
            Body=data,
            ContentType="application/json",
        )
    except Exception as exc:
        log.error("R2 put_name_map failed: %s", exc)
