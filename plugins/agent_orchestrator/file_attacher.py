"""Read and format files for attachment to agent tasks."""

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class FileAttacher:
    """Read and format files for attachment to agent tasks."""

    def attach(self, file_paths: List[str]) -> str:
        """Read files and format with delimiters.

        Args:
            file_paths: List of file paths to attach

        Returns:
            Formatted string with all file contents
        """
        parts = []

        for file_path in file_paths:
            content = self._read_file(file_path)
            if content is not None:
                parts.append(self._format_file(file_path, content))
            else:
                logger.warning(f"Could not read file: {file_path}")

        return "\n\n".join(parts)

    def _read_file(self, file_path: str) -> Optional[str]:
        """Read file contents.

        Args:
            file_path: Path to file

        Returns:
            File contents or None if file cannot be read
        """
        path = Path(file_path)

        if not path.exists():
            logger.warning(f"File does not exist: {file_path}")
            return None

        if not path.is_file():
            logger.warning(f"Path is not a file: {file_path}")
            return None

        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Try with different encoding
            try:
                return path.read_text(encoding="latin-1")
            except Exception as e:
                logger.error(f"Failed to read file {file_path}: {e}")
                return None
        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return None

    def _format_file(self, file_path: str, content: str) -> str:
        """Format file with delimiters.

        Args:
            file_path: Original file path
            content: File contents

        Returns:
            Formatted file block
        """
        return f"""--file_start {file_path}--
{content}
--file_end--"""
