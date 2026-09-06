"""
Destination Service - Uploads completed backups to configured storage
Supports: Local, S3, Azure Blob, Google Cloud Storage, FTP/SFTP, Webhook
"""
import os
import logging
import requests
from pathlib import Path
from django.conf import settings
from .models import BackupDestination, BackupJob

logger = logging.getLogger(__name__)


class DestinationUploadError(Exception):
    pass


class DestinationService:
    """Routes a completed backup file to its configured destination"""

    def __init__(self, destination: BackupDestination):
        self.destination = destination

    def upload(self, job: BackupJob) -> bool:
        """Upload backup file to the configured destination. Returns True on success."""
        file_path = Path(job.backup_path)
        if not file_path.exists():
            raise DestinationUploadError(f"Backup file not found: {file_path}")

        handler = {
            'local': self._upload_local,
            's3': self._upload_s3,
            'azure': self._upload_azure,
            'gcs': self._upload_gcs,
            'ftp': self._upload_ftp,
            'webhook': self._upload_webhook,
        }.get(self.destination.destination_type)

        if not handler:
            raise DestinationUploadError(
                f"Unknown destination type: {self.destination.destination_type}"
            )

        try:
            handler(file_path, job)
            logger.info(
                f"Backup {job.id} uploaded to {self.destination.destination_type} "
                f"successfully"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to upload backup {job.id} to "
                f"{self.destination.destination_type}: {str(e)}",
                exc_info=True
            )
            raise DestinationUploadError(str(e))

    # ------------------------------------------------------------------
    # Local storage
    # ------------------------------------------------------------------
    def _upload_local(self, file_path: Path, job: BackupJob):
        import shutil
        target_dir = Path(self.destination.local_path or 'backups/local')
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_path.name
        if str(target_path) != str(file_path):
            shutil.copy2(file_path, target_path)

    # ------------------------------------------------------------------
    # AWS S3
    # ------------------------------------------------------------------
    def _upload_s3(self, file_path: Path, job: BackupJob):
        try:
            import boto3
        except ImportError:
            raise DestinationUploadError(
                "boto3 is required for S3 uploads. Install with: pip install boto3"
            )

        client = boto3.client(
            's3',
            region_name=self.destination.s3_region,
            aws_access_key_id=self.destination.s3_access_key,
            aws_secret_access_key=self.destination.s3_secret_key,
        )

        key = f"{self.destination.s3_prefix.rstrip('/')}/{file_path.name}"
        client.upload_file(str(file_path), self.destination.s3_bucket, key)

    # ------------------------------------------------------------------
    # Azure Blob Storage
    # ------------------------------------------------------------------
    def _upload_azure(self, file_path: Path, job: BackupJob):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            raise DestinationUploadError(
                "azure-storage-blob is required. "
                "Install with: pip install azure-storage-blob"
            )

        conn_str = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={self.destination.azure_account_name};"
            f"AccountKey={self.destination.azure_account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        service_client = BlobServiceClient.from_connection_string(conn_str)
        blob_client = service_client.get_blob_client(
            container=self.destination.azure_container,
            blob=file_path.name
        )

        with open(file_path, 'rb') as data:
            blob_client.upload_blob(data, overwrite=True)

    # ------------------------------------------------------------------
    # Google Cloud Storage
    # ------------------------------------------------------------------
    def _upload_gcs(self, file_path: Path, job: BackupJob):
        try:
            from google.cloud import storage as gcs_storage
            from google.oauth2 import service_account
        except ImportError:
            raise DestinationUploadError(
                "google-cloud-storage is required. "
                "Install with: pip install google-cloud-storage"
            )

        if self.destination.gcs_service_account:
            credentials = service_account.Credentials.from_service_account_info(
                self.destination.gcs_service_account
            )
            client = gcs_storage.Client(
                project=self.destination.gcs_project_id,
                credentials=credentials
            )
        else:
            client = gcs_storage.Client(project=self.destination.gcs_project_id)

        bucket = client.bucket(self.destination.gcs_bucket)
        blob = bucket.blob(file_path.name)
        blob.upload_from_filename(str(file_path))

    # ------------------------------------------------------------------
    # FTP / SFTP
    # ------------------------------------------------------------------
    def _upload_ftp(self, file_path: Path, job: BackupJob):
        if self.destination.ftp_use_sftp:
            self._upload_sftp(file_path)
        else:
            self._upload_plain_ftp(file_path)

    def _upload_sftp(self, file_path: Path):
        try:
            import paramiko
        except ImportError:
            raise DestinationUploadError(
                "paramiko is required for SFTP uploads. "
                "Install with: pip install paramiko"
            )

        transport = paramiko.Transport(
            (self.destination.ftp_host, self.destination.ftp_port or 22)
        )
        try:
            transport.connect(
                username=self.destination.ftp_username,
                password=self.destination.ftp_password
            )
            sftp = paramiko.SFTPClient.from_transport(transport)
            remote_path = os.path.join(
                self.destination.ftp_path or '/', file_path.name
            )
            sftp.put(str(file_path), remote_path)
            sftp.close()
        finally:
            transport.close()

    def _upload_plain_ftp(self, file_path: Path):
        from ftplib import FTP
        ftp = FTP()
        ftp.connect(self.destination.ftp_host, self.destination.ftp_port or 21)
        ftp.login(self.destination.ftp_username, self.destination.ftp_password)
        try:
            if self.destination.ftp_path:
                ftp.cwd(self.destination.ftp_path)
            with open(file_path, 'rb') as f:
                ftp.storbinary(f"STOR {file_path.name}", f)
        finally:
            ftp.quit()

    # ------------------------------------------------------------------
    # Webhook / HTTP upload
    # ------------------------------------------------------------------
    def _upload_webhook(self, file_path: Path, job: BackupJob):
        headers = {}
        if self.destination.webhook_auth_header:
            headers['Authorization'] = self.destination.webhook_auth_header

        with open(file_path, 'rb') as f:
            files = {'file': (file_path.name, f, 'application/octet-stream')}
            data = {
                'backup_job_id': job.id,
                'checksum_sha256': job.checksum_sha256,
                'backup_type': job.backup_type,
            }
            response = requests.post(
                self.destination.webhook_url,
                files=files,
                data=data,
                headers=headers,
                verify=self.destination.webhook_verify_ssl,
                timeout=300
            )

        if response.status_code not in (200, 201, 202, 204):
            raise DestinationUploadError(
                f"Webhook upload failed with status {response.status_code}: "
                f"{response.text[:500]}"
            )

    def test_connection(self) -> tuple:
        """Test the destination connection. Returns (success: bool, message: str)."""
        try:
            if self.destination.destination_type == 'local':
                target = Path(self.destination.local_path or 'backups/local')
                target.mkdir(parents=True, exist_ok=True)
                test_file = target / '.connection_test'
                test_file.write_text('test')
                test_file.unlink()
                return True, "Local path is writable"

            elif self.destination.destination_type == 's3':
                import boto3
                client = boto3.client(
                    's3',
                    region_name=self.destination.s3_region,
                    aws_access_key_id=self.destination.s3_access_key,
                    aws_secret_access_key=self.destination.s3_secret_key,
                )
                client.head_bucket(Bucket=self.destination.s3_bucket)
                return True, "S3 bucket accessible"

            elif self.destination.destination_type == 'azure':
                from azure.storage.blob import BlobServiceClient
                conn_str = (
                    f"DefaultEndpointsProtocol=https;"
                    f"AccountName={self.destination.azure_account_name};"
                    f"AccountKey={self.destination.azure_account_key};"
                    f"EndpointSuffix=core.windows.net"
                )
                service_client = BlobServiceClient.from_connection_string(conn_str)
                service_client.get_container_client(
                    self.destination.azure_container
                ).get_container_properties()
                return True, "Azure container accessible"

            elif self.destination.destination_type == 'gcs':
                from google.cloud import storage as gcs_storage
                client = gcs_storage.Client(project=self.destination.gcs_project_id)
                bucket = client.bucket(self.destination.gcs_bucket)
                bucket.exists()
                return True, "GCS bucket accessible"

            elif self.destination.destination_type == 'ftp':
                if self.destination.ftp_use_sftp:
                    import paramiko
                    transport = paramiko.Transport(
                        (self.destination.ftp_host, self.destination.ftp_port or 22)
                    )
                    transport.connect(
                        username=self.destination.ftp_username,
                        password=self.destination.ftp_password
                    )
                    transport.close()
                else:
                    from ftplib import FTP
                    ftp = FTP()
                    ftp.connect(
                        self.destination.ftp_host, self.destination.ftp_port or 21
                    )
                    ftp.login(
                        self.destination.ftp_username,
                        self.destination.ftp_password
                    )
                    ftp.quit()
                return True, "FTP/SFTP connection successful"

            elif self.destination.destination_type == 'webhook':
                headers = {}
                if self.destination.webhook_auth_header:
                    headers['Authorization'] = self.destination.webhook_auth_header
                response = requests.head(
                    self.destination.webhook_url,
                    headers=headers,
                    verify=self.destination.webhook_verify_ssl,
                    timeout=15
                )
                return True, f"Webhook reachable (status {response.status_code})"

        except Exception as e:
            return False, str(e)

        return False, "Unknown destination type"
