from unittest.mock import MagicMock, patch
from io import BytesIO
from infrastructure.storage.s3_storage_adapter import S3StorageAdapter


def test_s3_upload_bytes():
    with patch("boto3.client") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        adapter = S3StorageAdapter()
        adapter.upload("test/key", b"test data")
        mock_client.put_object.assert_called_once()


def test_s3_exists():
    with patch("boto3.client") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        adapter = S3StorageAdapter()
        adapter.exists("test/key")
        mock_client.head_object.assert_called_once()
