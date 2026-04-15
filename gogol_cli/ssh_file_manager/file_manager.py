"""Service to manage files via SSH."""

import logging

import asyncssh

from gogol_cli.schemas import File
from gogol_cli.ssh_file_manager.schemas import SSHConfig

LOGGER = logging.getLogger(__name__)


class SSHFileManager:
    """Service to manage files via SSH."""

    def __init__(self, config: SSHConfig) -> None:
        """Initialize with SSH config.

        Args:
            config: The SSH config to use.
        """
        self._config = config

    async def copy_file(
        self,
        file: File,
        dst_path: str,
    ) -> None:
        """Copy a file via SSH.

        Args:
            file: The file to copy.
            dst_path: The destination path to copy to.
        """
        remote_src = f"{self._config.base_path}/{file.subdir}/{file.file_name}"
        remote_dst = f"{self._config.base_path}/{dst_path}/{file.file_name}"

        LOGGER.info(f"Copying file from {remote_src} to {remote_dst}")

        async with asyncssh.connect(
            self._config.host,
            username=self._config.username,
            client_keys=[self._config.key_path],
        ) as conn:
            await conn.run(f'mkdir -p "{self._config.base_path}/{dst_path}"')
            await conn.run(f'cp "{remote_src}" "{remote_dst}"')

        LOGGER.info("Finished copying file")

    async def upload_file(self, data: bytes, subdir: str, filename: str) -> None:
        """Upload raw bytes as a new file to the remote server via SFTP.

        Args:
            data: The file contents to upload.
            subdir: The subdirectory path relative to base_path.
            filename: The destination filename.
        """
        remote_dir = f"{self._config.base_path}/{subdir}"
        remote_path = f"{remote_dir}/{filename}"

        LOGGER.info("Uploading file to %s ...", remote_path)

        async with asyncssh.connect(
            self._config.host,
            username=self._config.username,
            client_keys=[self._config.key_path],
        ) as conn:
            await conn.run(f'mkdir -p "{remote_dir}"')
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(remote_path, "wb") as remote_file:
                    await remote_file.write(data)

        LOGGER.info("Finished uploading file to %s", remote_path)
