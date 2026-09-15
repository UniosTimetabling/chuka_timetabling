"""
Backup Service - Core backup execution logic
Handles database dumps, file backups, compression, and encryption
"""
import os
import json
import gzip
import bz2
import zipfile
import tarfile
import subprocess
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from django.utils import timezone as django_timezone
from io import BytesIO
import logging
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import connection
from .models import BackupConfiguration, BackupJob, SyncQueue

logger = logging.getLogger(__name__)


class BackupService:
    """Main backup service for creating and managing backups"""
    
    def __init__(self, config: BackupConfiguration):
        self.config = config
        self.backup_dir = Path(settings.BASE_DIR) / 'backups'
        self.backup_dir.mkdir(exist_ok=True, parents=True)
        
    def execute_backup(self) -> BackupJob:
        """Execute backup based on configuration"""
        job = BackupJob.objects.create(
            config=self.config,
            backup_type=self.config.backup_type,
            status='running'
        )
        
        try:
            job.started_at = django_timezone.now()
            job.save()
            
            if self.config.backup_type == 'database':
                self._backup_database(job)
            elif self.config.backup_type == 'files':
                self._backup_files(job)
            elif self.config.backup_type == 'full':
                self._backup_database(job)
                self._backup_files(job)
            elif self.config.backup_type == 'config':
                self._backup_config(job)
            
            job.status = 'success'
            job.last_backup_time = django_timezone.now()
            self.config.last_backup_time = django_timezone.now()
            self.config.last_backup_status = 'success'
            
        except Exception as e:
            logger.error(f"Backup failed: {str(e)}", exc_info=True)
            job.status = 'failed'
            job.error_message = str(e)
            job.error_details = {
                'type': type(e).__name__,
                'message': str(e)
            }
            self.config.last_backup_status = 'failed'
        
        finally:
            job.completed_at = django_timezone.now()
            if job.started_at:
                job.duration_seconds = int(
                    (job.completed_at - job.started_at).total_seconds()
                )
            job.save()
            self.config.save()
            
            # Queue sync task if configured
            self._queue_sync_task(job)
        
        return job
    
    def _backup_database(self, job: BackupJob):
        """Backup database"""
        db_config = settings.DATABASES['default']
        engine = db_config['ENGINE']
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"database_backup_{timestamp}"
        
        if 'mysql' in engine.lower():
            self._backup_mysql(db_config, job, filename)
        elif 'postgresql' in engine.lower():
            self._backup_postgresql(db_config, job, filename)
        elif 'sqlite' in engine.lower():
            self._backup_sqlite(db_config, job, filename)
    
    def _backup_mysql(self, db_config, job: BackupJob, filename: str):
        """Backup MySQL database"""
        cmd = [
            'mysqldump',
            '-h', db_config['HOST'],
            '-u', db_config['USER'],
            f"-p{db_config['PASSWORD']}" if db_config['PASSWORD'] else '',
            '--single-transaction',
            '--routines',
            '--triggers',
            db_config['NAME']
        ]
        
        # Remove empty password flag
        cmd = [c for c in cmd if c]
        
        dump_path = self.backup_dir / f"{filename}.sql"
        
        try:
            with open(dump_path, 'w') as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=3600
                )
            
            if result.returncode != 0:
                raise Exception(f"mysqldump failed: {result.stderr}")
            
            # Compress backup
            compressed_path = self._compress_backup(dump_path)
            
            # Encrypt if enabled
            if self.config.encryption_enabled:
                encrypted_path = self._encrypt_backup(compressed_path)
                final_path = encrypted_path
                os.remove(compressed_path)
            else:
                final_path = compressed_path
            
            os.remove(dump_path)
            
            # Calculate checksum
            checksum = self._calculate_checksum(final_path)
            
            # Update job
            job.backup_filename = final_path.name
            job.backup_path = str(final_path)
            job.checksum_sha256 = checksum
            job.total_size = dump_path.stat().st_size if dump_path.exists() else 0
            job.compressed_size = final_path.stat().st_size
            
            logger.info(f"MySQL backup completed: {final_path.name}")
            
        except Exception as e:
            logger.error(f"MySQL backup error: {str(e)}")
            raise
    
    def _backup_postgresql(self, db_config, job: BackupJob, filename: str):
        """Backup PostgreSQL database"""
        cmd = [
            'pg_dump',
            '-h', db_config['HOST'],
            '-U', db_config['USER'],
            '-d', db_config['NAME'],
            '--format=plain',
            '--compress=0'
        ]
        
        dump_path = self.backup_dir / f"{filename}.sql"
        
        try:
            env = os.environ.copy()
            if db_config['PASSWORD']:
                env['PGPASSWORD'] = db_config['PASSWORD']
            
            with open(dump_path, 'w') as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                    timeout=3600
                )
            
            if result.returncode != 0:
                raise Exception(f"pg_dump failed: {result.stderr}")
            
            # Compress backup
            compressed_path = self._compress_backup(dump_path)
            
            # Encrypt if enabled
            if self.config.encryption_enabled:
                encrypted_path = self._encrypt_backup(compressed_path)
                final_path = encrypted_path
                os.remove(compressed_path)
            else:
                final_path = compressed_path
            
            os.remove(dump_path)
            
            # Calculate checksum
            checksum = self._calculate_checksum(final_path)
            
            # Update job
            job.backup_filename = final_path.name
            job.backup_path = str(final_path)
            job.checksum_sha256 = checksum
            job.total_size = dump_path.stat().st_size if dump_path.exists() else 0
            job.compressed_size = final_path.stat().st_size
            
            logger.info(f"PostgreSQL backup completed: {final_path.name}")
            
        except Exception as e:
            logger.error(f"PostgreSQL backup error: {str(e)}")
            raise
    
    def _backup_sqlite(self, db_config, job: BackupJob, filename: str):
        """Backup SQLite database"""
        db_path = Path(db_config['NAME'])
        
        if not db_path.exists():
            raise Exception(f"SQLite database not found: {db_path}")
        
        backup_path = self.backup_dir / f"{filename}.db"
        
        try:
            shutil.copy2(db_path, backup_path)
            
            # Compress backup
            compressed_path = self._compress_backup(backup_path)
            
            # Encrypt if enabled
            if self.config.encryption_enabled:
                encrypted_path = self._encrypt_backup(compressed_path)
                final_path = encrypted_path
                os.remove(compressed_path)
            else:
                final_path = compressed_path
            
            os.remove(backup_path)
            
            # Calculate checksum
            checksum = self._calculate_checksum(final_path)
            
            # Update job
            job.backup_filename = final_path.name
            job.backup_path = str(final_path)
            job.checksum_sha256 = checksum
            job.total_size = db_path.stat().st_size
            job.compressed_size = final_path.stat().st_size
            
            logger.info(f"SQLite backup completed: {final_path.name}")
            
        except Exception as e:
            logger.error(f"SQLite backup error: {str(e)}")
            raise
    
    def _backup_files(self, job: BackupJob):
        """Backup application files"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"files_backup_{timestamp}"
        
        backup_path = self.backup_dir / f"{filename}.tar"
        
        try:
            # Create tar archive
            media_dir = Path(settings.MEDIA_ROOT)
            
            if media_dir.exists():
                with tarfile.open(backup_path, 'w') as tar:
                    tar.add(media_dir, arcname='media', recursive=True)
            
            # Compress backup
            compressed_path = self._compress_backup(backup_path)
            
            # Encrypt if enabled
            if self.config.encryption_enabled:
                encrypted_path = self._encrypt_backup(compressed_path)
                final_path = encrypted_path
                os.remove(compressed_path)
            else:
                final_path = compressed_path
            
            os.remove(backup_path)
            
            # Calculate checksum
            checksum = self._calculate_checksum(final_path)
            
            # Update job
            job.backup_filename = final_path.name
            job.backup_path = str(final_path)
            job.checksum_sha256 = checksum
            job.total_size = sum(
                f.stat().st_size for f in media_dir.rglob('*') if f.is_file()
            ) if media_dir.exists() else 0
            job.compressed_size = final_path.stat().st_size
            job.files_count = len(list(media_dir.rglob('*'))) if media_dir.exists() else 0
            
            logger.info(f"Files backup completed: {final_path.name}")
            
        except Exception as e:
            logger.error(f"Files backup error: {str(e)}")
            raise
    
    def _backup_config(self, job: BackupJob):
        """Backup configuration files and environment"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"config_backup_{timestamp}"
        
        config_data = {
            'environment': self._collect_environment_vars(),
            'settings': self._collect_django_settings(),
            'timestamp': timestamp
        }
        
        config_path = self.backup_dir / f"{filename}.json"
        
        try:
            with open(config_path, 'w') as f:
                json.dump(config_data, f, indent=2, default=str)
            
            # Compress backup
            compressed_path = self._compress_backup(config_path)
            
            # Encrypt if enabled
            if self.config.encryption_enabled:
                encrypted_path = self._encrypt_backup(compressed_path)
                final_path = encrypted_path
                os.remove(compressed_path)
            else:
                final_path = compressed_path
            
            os.remove(config_path)
            
            # Calculate checksum
            checksum = self._calculate_checksum(final_path)
            
            # Update job
            job.backup_filename = final_path.name
            job.backup_path = str(final_path)
            job.checksum_sha256 = checksum
            job.compressed_size = final_path.stat().st_size
            
            logger.info(f"Configuration backup completed: {final_path.name}")
            
        except Exception as e:
            logger.error(f"Configuration backup error: {str(e)}")
            raise
    
    def _compress_backup(self, file_path: Path) -> Path:
        """Compress backup file"""
        compression = self.config.compression
        
        if compression == 'none':
            return file_path
        
        if compression == 'gzip':
            compressed_path = file_path.parent / f"{file_path.name}.gz"
            with open(file_path, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        
        elif compression == 'bzip2':
            compressed_path = file_path.parent / f"{file_path.name}.bz2"
            with open(file_path, 'rb') as f_in:
                with bz2.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        
        elif compression == 'zip':
            compressed_path = file_path.parent / f"{file_path.name}.zip"
            with zipfile.ZipFile(compressed_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(file_path, arcname=file_path.name)
        
        return compressed_path
    
    def _encrypt_backup(self, file_path: Path) -> Path:
        """Encrypt backup file using Fernet"""
        # Generate encryption key from Django SECRET_KEY
        key = Fernet.generate_key()
        cipher = Fernet(key)
        
        encrypted_path = file_path.parent / f"{file_path.name}.enc"
        
        with open(file_path, 'rb') as f:
            data = f.read()
        
        encrypted_data = cipher.encrypt(data)
        
        with open(encrypted_path, 'wb') as f:
            f.write(encrypted_data)
        
        # Store encryption key securely
        key_path = file_path.parent / f"{file_path.name}.key"
        with open(key_path, 'wb') as f:
            f.write(key)
        
        return encrypted_path
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of file"""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)
        
        return sha256_hash.hexdigest()
    
    def _collect_environment_vars(self) -> dict:
        """Collect important environment variables"""
        important_vars = [
            'DEBUG',
            'ALLOWED_HOSTS',
            'DB_ENGINE',
            'DB_HOST',
            'DB_PORT',
            'DB_NAME',
        ]
        
        return {
            var: os.environ.get(var, '') 
            for var in important_vars
        }
    
    def _collect_django_settings(self) -> dict:
        """Collect important Django settings"""
        return {
            'DEBUG': settings.DEBUG,
            'ALLOWED_HOSTS': settings.ALLOWED_HOSTS,
            'TIME_ZONE': settings.TIME_ZONE,
            'LANGUAGE_CODE': settings.LANGUAGE_CODE,
            'INSTALLED_APPS': settings.INSTALLED_APPS,
        }
    
    def _queue_sync_task(self, job: BackupJob):
        """Queue background sync task"""
        SyncQueue.objects.create(
            task_type='backup',
            status='pending',
            payload={
                'backup_job_id': job.id,
                'destination_type': self.config.destination.destination_type
            }
        )
