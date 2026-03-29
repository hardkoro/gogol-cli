"""Service to manage files via SSH."""

import asyncssh
import logging

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
