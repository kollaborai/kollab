"""File operations executor for LLM-driven file manipulation.

Provides safe file operations with automatic backups, validation, and
comprehensive error handling. Implements 11 file operation types:
- edit: Find/replace in files (replaces ALL occurrences)
- create: Create new files
- create_overwrite: Create/overwrite files
- delete: Delete files with safety checks
- move: Move/rename files
- copy: Copy files
- copy_overwrite: Copy with overwrite
- append: Append to files
- insert_after: Insert content after pattern (exact match required)
- insert_before: Insert content before pattern (exact match required)
- mkdir: Create directories
- rmdir: Remove empty directories
- read: Read file content
"""

import ast
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kollabor_config.config_utils import get_config_directory_candidates

logger = logging.getLogger(__name__)

# Global kollab config directories - allowed for read operations
KOLLAB_CONFIG_DIRS = get_config_directory_candidates()


class PathAccessMode:
    """Path access modes for file operations."""

    PROJECT_ONLY = "project_only"
    KOLLAB_CONFIG = "kollabor_config"
    ANYWHERE = "anywhere"


class FileOperationsExecutor:
    """Execute file operations with comprehensive safety features.

    Features:
    - Automatic backups before destructive operations
    - Protected path checking
    - Path traversal prevention
    - Binary file detection
    - Optional Python syntax validation
    - File size limits
    - Multi-occurrence handling (edit vs insert operations)
    """

    def __init__(self, config=None, workspace: Optional[Path] = None):
        """Initialize file operations executor.

        Args:
            config: Configuration manager (optional)
            workspace: Base directory for relative project paths
        """
        self.config = config
        self.event_bus = None  # Set by tool_executor
        self.path_access_mode = PathAccessMode.PROJECT_ONLY
        self.project_root = (
            Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
        )

        # Default configuration values
        self.enabled = self._get_config("file_operations.enabled", True)
        self.automatic_backups = self._get_config(
            "file_operations.automatic_backups", True
        )
        self.validate_python_syntax = self._get_config(
            "file_operations.validate_python_syntax", True
        )
        self.rollback_on_syntax_error = self._get_config(
            "file_operations.rollback_on_syntax_error", True
        )
        self.max_edit_size_mb = self._get_config("file_operations.max_edit_size_mb", 10)
        self.max_create_size_mb = self._get_config(
            "file_operations.max_create_size_mb", 5
        )
        self.max_read_size_mb = self._get_config("file_operations.max_read_size_mb", 10)
        # Cap a single read's RETURNED output so one big file can't blow the
        # model context window. A full dump of a large file (e.g. a ~9.5k-line
        # module ≈ 95k tokens) plus the system prompt, tool schemas, and the
        # max_tokens budget can exceed the provider window — the call then comes
        # back empty (stop_reason=model_context_window_exceeded). These bound the
        # text injected into conversation history, independent of the on-disk
        # size limit above. Set either to 0 to disable that bound.
        self.max_read_lines = self._get_config("file_operations.max_read_lines", 2000)
        self.max_read_output_chars = self._get_config(
            "file_operations.max_read_output_chars", 80000
        )
        self.create_parent_directories = self._get_config(
            "file_operations.create_parent_directories", True
        )
        self.allow_binary_operations = self._get_config(
            "file_operations.allow_binary_operations", False
        )

        # Protected paths (cannot delete/modify)
        self.protected_files = self._get_config(
            "file_operations.protected_files", ["core/application.py", "main.py"]
        )

        self.protected_patterns = self._get_config(
            "file_operations.protected_patterns",
            [".git/**", "venv/**", ".venv/**", "node_modules/**"],
        )

        logger.info(f"File operations executor initialized (enabled={self.enabled})")

    def _get_config(self, key: str, default: Any) -> Any:
        """Get configuration value with fallback.

        Args:
            key: Config key in dot notation
            default: Default value if not found

        Returns:
            Configuration value or default
        """
        if self.config:
            return self.config.get(key, default)
        return default

    def set_path_access_mode(self, mode: str) -> None:
        """Set path access mode for file operations."""
        self.path_access_mode = mode

    def validate_file_path(
        self, filepath: str, allow_kollabor_config: bool = False
    ) -> Tuple[bool, str, str]:
        """Validate file path for security and correctness.

        Args:
            filepath: File path to validate
            allow_kollabor_config: If True, allow absolute paths within Kollab config dirs.

        Returns:
            (is_valid, error_message, normalized_path)
            normalized_path will be the filepath with tilde expanded if applicable
        """
        if not filepath:
            return False, "Empty file path", filepath

        # Security: No path traversal
        if ".." in filepath:
            return False, f"Path traversal detected: {filepath}", filepath

        # Expand tilde if present
        normalized_path = filepath
        if filepath.startswith("~"):
            normalized_path = str(Path(filepath).expanduser())

        path_obj = Path(normalized_path)
        is_absolute = path_obj.is_absolute() or (
            len(normalized_path) > 1 and normalized_path[1] == ":"
        )

        if not is_absolute:
            try:
                normalized_path = str((self.project_root / normalized_path).resolve())
            except (ValueError, OSError):
                return False, f"Invalid path: {filepath}", normalized_path
            path_obj = Path(normalized_path)
            is_absolute = True

        # Security: control absolute path access by mode
        if is_absolute:
            if self.path_access_mode == PathAccessMode.ANYWHERE:
                pass
            elif self._is_within_project(path_obj):
                pass
            elif allow_kollabor_config:
                try:
                    resolved = path_obj.resolve()
                    if any(
                        resolved.is_relative_to(config_dir)
                        for config_dir in KOLLAB_CONFIG_DIRS
                    ):
                        pass
                    else:
                        return (
                            False,
                            "Absolute paths only allowed within "
                            f"{self.project_root} or Kollab config dirs: {filepath}",
                            normalized_path,
                        )
                except (ValueError, OSError):
                    return False, f"Invalid absolute path: {filepath}", normalized_path
            else:
                return (
                    False,
                    "Absolute paths not allowed outside workspace "
                    f"{self.project_root}: {filepath}",
                    normalized_path,
                )

        # Practical: Path length limit
        if len(normalized_path) > 255:
            return (
                False,
                f"Path too long: {len(normalized_path)} chars (max 255)",
                normalized_path,
            )

        # Security: No null bytes
        if "\x00" in normalized_path:
            return False, "Null byte in file path", normalized_path

        return True, "", normalized_path

    def _is_within_project(self, path: Path) -> bool:
        """Return True when path resolves inside the configured workspace."""
        try:
            path.resolve().relative_to(self.project_root)
            return True
        except (ValueError, OSError):
            return False

    def is_protected_path(self, filepath: str) -> bool:
        """Check if file path is protected from deletion/modification.

        Args:
            filepath: File path to check

        Returns:
            True if path is protected
        """
        candidates = self._protected_path_candidates(filepath)

        # Check exact matches
        if any(candidate in self.protected_files for candidate in candidates):
            return True

        # Check pattern matches
        for pattern in self.protected_patterns:
            # Simple wildcard matching
            if pattern.endswith("/**"):
                prefix = pattern[:-3]
                if any(
                    candidate == prefix or candidate.startswith(f"{prefix}/")
                    for candidate in candidates
                ):
                    return True
            elif pattern.endswith("/*"):
                prefix = pattern[:-2]
                if any(
                    str(Path(candidate).parent) == prefix
                    or str(Path(candidate).parent).startswith(f"{prefix}/")
                    for candidate in candidates
                ):
                    return True

        return False

    def _protected_path_candidates(self, filepath: str) -> set[str]:
        """Return absolute and workspace-relative path strings for protection checks."""
        candidates = {filepath}
        path = Path(filepath).expanduser()
        candidates.add(path.as_posix())

        try:
            resolved = path.resolve()
            candidates.add(str(resolved))
            candidates.add(resolved.as_posix())

            relative = resolved.relative_to(self.project_root)
            candidates.add(str(relative))
            candidates.add(relative.as_posix())
        except (ValueError, OSError):
            pass

        return candidates

    def is_text_file(self, filepath: str) -> bool:
        """Check if file is text (not binary).

        Args:
            filepath: File path to check

        Returns:
            True if file is text
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                f.read(1024)  # Try reading first 1KB as text
            return True
        except (UnicodeDecodeError, FileNotFoundError):
            return False

    def check_file_size(self, filepath: str, max_size_mb: float) -> Tuple[bool, str]:
        """Check if file size is within limits.

        Args:
            filepath: File path to check
            max_size_mb: Maximum size in MB

        Returns:
            (is_valid, error_message)
        """
        try:
            size_bytes = os.path.getsize(filepath)
            size_mb = size_bytes / (1024 * 1024)

            if size_mb > max_size_mb:
                return False, f"File too large: {size_mb:.1f}MB (max {max_size_mb}MB)"

            return True, ""
        except FileNotFoundError:
            return True, ""  # File doesn't exist yet, allow operation

    def create_backup(self, filepath: str, suffix: str = ".bak") -> Optional[str]:
        """Create backup of file before modification.

        Args:
            filepath: File to backup
            suffix: Backup file suffix

        Returns:
            Backup file path or None if failed
        """
        if not self.automatic_backups:
            return None

        if not os.path.exists(filepath):
            return None

        backup_path = f"{filepath}{suffix}"

        try:
            shutil.copy2(filepath, backup_path)
            logger.debug(f"Created backup: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return None

    def validate_python_syntax_file(self, filepath: str) -> Tuple[bool, str]:
        """Validate Python file syntax.

        Args:
            filepath: Python file to validate

        Returns:
            (is_valid, error_message)
        """
        if not self.validate_python_syntax:
            return True, ""

        if not filepath.endswith(".py"):
            return True, ""

        try:
            with open(filepath, "r") as f:
                content = f.read()
            ast.parse(content)
            return True, ""
        except SyntaxError as e:
            return False, f"Syntax error at line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, f"Validation error: {str(e)}"

    def find_pattern_occurrences(self, content: str, pattern: str) -> List[int]:
        """Find all line numbers where pattern occurs.

        Args:
            content: File content
            pattern: Pattern to search for

        Returns:
            List of line numbers (1-indexed) where pattern appears
        """
        lines = content.split("\n")
        occurrences = []

        # For multi-line patterns
        pattern_lines = pattern.split("\n")
        pattern_len = len(pattern_lines)

        for i in range(len(lines) - pattern_len + 1):
            # Check if pattern matches starting at line i
            matches = True
            for j in range(pattern_len):
                if pattern_lines[j] != lines[i + j]:
                    matches = False
                    break

            if matches:
                occurrences.append(i + 1)  # 1-indexed

        # Also try simple substring search if no multi-line matches
        if not occurrences and pattern in content:
            for i, line in enumerate(lines, 1):
                if pattern in line:
                    occurrences.append(i)

        return occurrences

    def execute_operation(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a file operation.

        Args:
            operation: Operation dictionary from parser

        Returns:
            Result dictionary with success, output, error
        """
        if not self.enabled:
            return {
                "success": False,
                "error": "File operations are disabled in configuration",
            }

        op_type = operation.get("type", "unknown")
        op_id = operation.get("id", "unknown")

        logger.info(f"Executing file operation: {op_type} ({op_id})")

        # Malformed operations - provide helpful error message
        if op_type == "malformed_file_op":
            op_name = operation.get("operation", "unknown")
            error_msg = operation.get("error", "Unknown error")
            expected = operation.get("expected_format", "")
            preview = operation.get("content_preview", "")

            error_lines = [
                f"Malformed <{op_name}> operation: {error_msg}",
                "",
                "Expected format:",
                expected,
            ]
            if preview:
                error_lines.extend(
                    [
                        "",
                        "Received:",
                        preview[:200] + "..." if len(preview) > 200 else preview,
                    ]
                )

            return {"success": False, "error": "\n".join(error_lines)}

        # Route to specific operation handler
        handlers = {
            "file_edit": self._execute_edit,
            "file_create": self._execute_create,
            "file_create_overwrite": self._execute_create_overwrite,
            "file_delete": self._execute_delete,
            "file_move": self._execute_move,
            "file_copy": self._execute_copy,
            "file_copy_overwrite": self._execute_copy_overwrite,
            "file_append": self._execute_append,
            "file_insert_after": self._execute_insert_after,
            "file_insert_before": self._execute_insert_before,
            "file_mkdir": self._execute_mkdir,
            "file_rmdir": self._execute_rmdir,
            "file_read": self._execute_read,
            "file_grep": self._execute_grep,
        }

        handler = handlers.get(op_type)

        if not handler:
            return {"success": False, "error": f"Unknown operation type: {op_type}"}

        try:
            return handler(operation)
        except Exception as e:
            import traceback

            error_trace = traceback.format_exc()
            logger.error(f"Operation {op_type} failed: {error_trace}")
            return {"success": False, "error": f"Operation failed: {str(e)}"}

    def _execute_edit(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file edit operation (find/replace).

        Behavior: Replaces ALL occurrences, reports count.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")
        find_content = operation.get("find")
        replace_content = operation.get("replace")

        # Validation — include what we received so the model can self-correct
        if not filepath or not find_content or replace_content is None:
            got = {
                "file": repr(filepath)[:120] if filepath else "MISSING",
                "find": repr(find_content)[:120] if find_content else "MISSING",
                "replace": "present" if replace_content is not None else "MISSING",
            }
            keys_sent = list(operation.keys())
            return {
                "success": False,
                "error": (
                    f"Malformed file_edit: required fields are 'file', 'find', 'replace'. "
                    f"You sent keys={keys_sent}. "
                    f"Received: file={got['file']}, find={got['find']}, "
                    f"replace={got['replace']}. "
                    f"Check that each parameter is a separate JSON key."
                ),
            }

        # Allow editing files in ~/.kollab/ (skill files for global agents)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check file size
        is_valid, error = self.check_file_size(filepath, self.max_edit_size_mb)
        if not is_valid:
            return {"success": False, "error": error}

        # Check if text file
        if not self.is_text_file(filepath):
            return {"success": False, "error": f"Cannot edit binary file: {filepath}"}

        # Read file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {str(e)}"}

        # Check pattern exists
        count = content.count(find_content)
        if count == 0:
            return {"success": False, "error": f"Pattern not found in {filepath}"}

        # Find line numbers for reporting
        line_numbers = self.find_pattern_occurrences(content, find_content)

        # Create backup
        backup_path = self.create_backup(filepath)

        # Perform replacement (REPLACE ALL)
        new_content = content.replace(find_content, replace_content)

        # Write back
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            # Restore from backup if write fails
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, filepath)
            return {"success": False, "error": f"Failed to write file: {str(e)}"}

        # Optional: Validate Python syntax
        if filepath.endswith(".py") and self.validate_python_syntax:
            is_valid, error = self.validate_python_syntax_file(filepath)
            if not is_valid and self.rollback_on_syntax_error:
                # Rollback
                if backup_path and os.path.exists(backup_path):
                    shutil.copy2(backup_path, filepath)
                return {
                    "success": False,
                    "error": f"Syntax validation failed: {error}. Edit rolled back.",
                }

        # Build success message with diff info
        if count == 1:
            output = f"✓ Replaced 1 occurrence in {filepath}"
        else:
            lines_str = ", ".join(str(ln) for ln in line_numbers[:10])
            if len(line_numbers) > 10:
                lines_str += f" (+{len(line_numbers) - 10} more)"
            output = f"✓ Replaced {count} occurrences in {filepath}\nLocations: lines {lines_str}"

        if backup_path:
            output += f"\nBackup: {backup_path}"

        return {
            "success": True,
            "output": output,
            "diff_info": {
                "find": find_content,
                "replace": replace_content,
                "count": count,
                "lines": line_numbers[:5],  # First 5 line numbers for context
            },
        }

    def _execute_create(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file create operation.

        Fails if file already exists.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")
        content = operation.get("content", "")

        # Validation
        if not filepath:
            return {"success": False, "error": "Missing file path"}

        # Allow creating files in ~/.kollab/ (skill files for global agents)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if os.path.exists(filepath):
            return {
                "success": False,
                "error": f"File already exists: {filepath}. Use <edit> to modify or <create_overwrite> to replace.",
            }

        # Check content size
        size_mb = len(content.encode("utf-8")) / (1024 * 1024)
        if size_mb > self.max_create_size_mb:
            return {
                "success": False,
                "error": f"Content too large: {size_mb:.1f}MB (max {self.max_create_size_mb}MB)",
            }

        # Create parent directories if needed
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir):
            if self.create_parent_directories:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                    logger.debug(f"Created parent directories: {parent_dir}")
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to create parent directories: {str(e)}",
                    }
            else:
                return {
                    "success": False,
                    "error": f"Parent directory does not exist: {parent_dir}",
                }

        # Write file
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            # Set permissions (644 = rw-r--r--)
            os.chmod(filepath, 0o644)

            size_bytes = len(content.encode("utf-8"))
            return {
                "success": True,
                "output": f"✓ Created {filepath} ({size_bytes} bytes)",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create file: {str(e)}"}

    def _execute_create_overwrite(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file create with overwrite.

        Creates backup if file exists.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")
        content = operation.get("content", "")

        # Validation
        if not filepath:
            return {"success": False, "error": "Missing file path"}

        # Allow overwriting files in ~/.kollab/ (skill files for global agents)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        # Create backup if file exists
        backup_path = None
        if os.path.exists(filepath):
            backup_path = self.create_backup(filepath)

        # Check content size
        size_mb = len(content.encode("utf-8")) / (1024 * 1024)
        if size_mb > self.max_create_size_mb:
            return {
                "success": False,
                "error": f"Content too large: {size_mb:.1f}MB (max {self.max_create_size_mb}MB)",
            }

        # Create parent directories if needed
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir):
            if self.create_parent_directories:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to create parent directories: {str(e)}",
                    }

        # Write file
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            os.chmod(filepath, 0o644)

            size_bytes = len(content.encode("utf-8"))
            output = f"✓ Created/overwrote {filepath} ({size_bytes} bytes)"
            if backup_path:
                output += f"\nBackup: {backup_path}"

            return {"success": True, "output": output}
        except Exception as e:
            # Restore from backup if failed
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, filepath)
            return {"success": False, "error": f"Failed to write file: {str(e)}"}

    def _execute_delete(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file delete operation.

        Creates backup before deletion.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")

        # Validation
        if not filepath:
            return {"success": False, "error": "Missing file path"}

        is_valid, error, filepath = self.validate_file_path(filepath)
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check protected paths
        if self.is_protected_path(filepath):
            return {
                "success": False,
                "error": f"Cannot delete protected file: {filepath}",
            }

        # Create backup with .deleted suffix
        backup_path = self.create_backup(filepath, suffix=".deleted")

        # Delete file
        try:
            os.remove(filepath)

            output = f"✓ Deleted {filepath}"
            if backup_path:
                output += f"\nBackup: {backup_path}"

            return {"success": True, "output": output}
        except Exception as e:
            return {"success": False, "error": f"Failed to delete file: {str(e)}"}

    def _execute_move(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file move operation.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        from_path = operation.get("from")
        to_path = operation.get("to")

        # Validation
        if not from_path or not to_path:
            return {"success": False, "error": "Missing from/to paths"}

        is_valid, error, from_path = self.validate_file_path(from_path)
        if not is_valid:
            return {"success": False, "error": f"Source: {error}"}

        is_valid, error, to_path = self.validate_file_path(to_path)
        if not is_valid:
            return {"success": False, "error": f"Destination: {error}"}

        if not os.path.exists(from_path):
            return {"success": False, "error": f"Source file not found: {from_path}"}

        if os.path.exists(to_path):
            return {
                "success": False,
                "error": f"Destination already exists: {to_path}. Delete it first or choose different destination.",
            }

        if from_path == to_path:
            return {
                "success": False,
                "error": f"Source and destination are the same: {from_path}",
            }

        # Create parent directories for destination if needed
        parent_dir = os.path.dirname(to_path)
        if parent_dir and not os.path.exists(parent_dir):
            if self.create_parent_directories:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to create destination directory: {str(e)}",
                    }

        # Move file
        try:
            shutil.move(from_path, to_path)

            return {"success": True, "output": f"✓ Moved {from_path} → {to_path}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to move file: {str(e)}"}

    def _execute_copy(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file copy operation.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        from_path = operation.get("from")
        to_path = operation.get("to")

        # Validation
        if not from_path or not to_path:
            return {"success": False, "error": "Missing from/to paths"}

        is_valid, error, from_path = self.validate_file_path(from_path)
        if not is_valid:
            return {"success": False, "error": f"Source: {error}"}

        is_valid, error, to_path = self.validate_file_path(to_path)
        if not is_valid:
            return {"success": False, "error": f"Destination: {error}"}

        if not os.path.exists(from_path):
            return {"success": False, "error": f"Source file not found: {from_path}"}

        if os.path.exists(to_path):
            return {
                "success": False,
                "error": f"Destination already exists: {to_path}. Use <copy_overwrite> to replace.",
            }

        # Create parent directories for destination if needed
        parent_dir = os.path.dirname(to_path)
        if parent_dir and not os.path.exists(parent_dir):
            if self.create_parent_directories:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to create destination directory: {str(e)}",
                    }

        # Copy file with metadata
        try:
            shutil.copy2(from_path, to_path)

            return {"success": True, "output": f"✓ Copied {from_path} → {to_path}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to copy file: {str(e)}"}

    def _execute_copy_overwrite(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file copy with overwrite.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        from_path = operation.get("from")
        to_path = operation.get("to")

        # Validation
        if not from_path or not to_path:
            return {"success": False, "error": "Missing from/to paths"}

        is_valid, error, from_path = self.validate_file_path(from_path)
        if not is_valid:
            return {"success": False, "error": f"Source: {error}"}

        is_valid, error, to_path = self.validate_file_path(to_path)
        if not is_valid:
            return {"success": False, "error": f"Destination: {error}"}

        if not os.path.exists(from_path):
            return {"success": False, "error": f"Source file not found: {from_path}"}

        # Create backup if destination exists
        backup_path = None
        if os.path.exists(to_path):
            backup_path = self.create_backup(to_path)

        # Create parent directories for destination if needed
        parent_dir = os.path.dirname(to_path)
        if parent_dir and not os.path.exists(parent_dir):
            if self.create_parent_directories:
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    return {
                        "success": False,
                        "error": f"Failed to create destination directory: {str(e)}",
                    }

        # Copy file with metadata
        try:
            shutil.copy2(from_path, to_path)

            output = f"✓ Copied {from_path} → {to_path}"
            if backup_path:
                output += f"\nBackup: {backup_path}"

            return {"success": True, "output": output}
        except Exception as e:
            # Restore backup if failed
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, to_path)
            return {"success": False, "error": f"Failed to copy file: {str(e)}"}

    def _execute_append(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file append operation.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")
        content = operation.get("content", "")

        # Validation
        if not filepath:
            return {"success": False, "error": "Missing file path"}

        if not content:
            return {"success": False, "error": "Empty content"}

        # Allow appending to files in ~/.kollab/ (skill files for global agents)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check if text file
        if not self.is_text_file(filepath):
            return {
                "success": False,
                "error": f"Cannot append to binary file: {filepath}",
            }

        # Create backup
        backup_path = self.create_backup(filepath)

        # Append content
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(content)

            output = f"✓ Appended content to {filepath}"
            if backup_path:
                output += f"\nBackup: {backup_path}"

            return {"success": True, "output": output}
        except Exception as e:
            # Restore backup if failed
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, filepath)
            return {"success": False, "error": f"Failed to append to file: {str(e)}"}

    def _execute_insert_after(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute insert after pattern operation.

        Pattern must appear exactly once (fails on 0 or 2+ matches).

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")
        pattern = operation.get("pattern")
        content = operation.get("content", "")

        # Validation
        if not filepath or not pattern:
            return {"success": False, "error": "Missing file or pattern"}

        # Allow inserting in files in ~/.kollab/ (skill files for global agents)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check if text file
        if not self.is_text_file(filepath):
            return {
                "success": False,
                "error": f"Cannot insert into binary file: {filepath}",
            }

        # Read file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                file_content = f.read()
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {str(e)}"}

        # Find pattern occurrences
        count = file_content.count(pattern)
        line_numbers = self.find_pattern_occurrences(file_content, pattern)

        # Validate exact match
        if count == 0:
            return {"success": False, "error": f"Pattern not found: '{pattern}'"}
        elif count > 1:
            lines_str = ", ".join(str(ln) for ln in line_numbers[:10])
            return {
                "success": False,
                "error": f"Ambiguous pattern: '{pattern}' appears {count} times at lines {lines_str}. "
                f"Pattern must be unique for insert operations. Use <edit> with full context instead.",
            }

        # Create backup
        backup_path = self.create_backup(filepath)

        # Insert content after pattern
        new_content = file_content.replace(pattern, f"{pattern}\n{content}", 1)

        # Write back
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

            output = f"✓ Inserted content after pattern in {filepath} (line {line_numbers[0]})"
            if backup_path:
                output += f"\nBackup: {backup_path}"

            return {"success": True, "output": output}
        except Exception as e:
            # Restore backup if failed
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, filepath)
            return {"success": False, "error": f"Failed to write file: {str(e)}"}

    def _execute_insert_before(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute insert before pattern operation.

        Pattern must appear exactly once (fails on 0 or 2+ matches).

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        filepath = operation.get("file")
        pattern = operation.get("pattern")
        content = operation.get("content", "")

        # Validation
        if not filepath or not pattern:
            return {"success": False, "error": "Missing file or pattern"}

        # Allow inserting in files in ~/.kollab/ (skill files for global agents)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check if text file
        if not self.is_text_file(filepath):
            return {
                "success": False,
                "error": f"Cannot insert into binary file: {filepath}",
            }

        # Read file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                file_content = f.read()
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {str(e)}"}

        # Find pattern occurrences
        count = file_content.count(pattern)
        line_numbers = self.find_pattern_occurrences(file_content, pattern)

        # Validate exact match
        if count == 0:
            return {"success": False, "error": f"Pattern not found: '{pattern}'"}
        elif count > 1:
            lines_str = ", ".join(str(ln) for ln in line_numbers[:10])
            return {
                "success": False,
                "error": f"Ambiguous pattern: '{pattern}' appears {count} times at lines {lines_str}. "
                f"Pattern must be unique for insert operations. Use <edit> with full context instead.",
            }

        # Create backup
        backup_path = self.create_backup(filepath)

        # Insert content before pattern
        new_content = file_content.replace(pattern, f"{content}\n{pattern}", 1)

        # Write back
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

            output = f"✓ Inserted content before pattern in {filepath} (line {line_numbers[0]})"
            if backup_path:
                output += f"\nBackup: {backup_path}"

            return {"success": True, "output": output}
        except Exception as e:
            # Restore backup if failed
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, filepath)
            return {"success": False, "error": f"Failed to write file: {str(e)}"}

    def _execute_mkdir(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute create directory operation.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        dir_path = operation.get("path")

        # Validation
        if not dir_path:
            return {"success": False, "error": "Missing directory path"}

        is_valid, error, dir_path = self.validate_file_path(dir_path)
        if not is_valid:
            return {"success": False, "error": error}

        if os.path.exists(dir_path):
            if os.path.isdir(dir_path):
                return {
                    "success": False,
                    "error": f"Directory already exists: {dir_path}",
                }
            else:
                return {"success": False, "error": f"Path exists as a file: {dir_path}"}

        # Create directory with parents
        try:
            os.makedirs(dir_path, mode=0o755, exist_ok=False)

            return {"success": True, "output": f"✓ Created directory: {dir_path}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to create directory: {str(e)}"}

    def _execute_rmdir(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute remove directory operation.

        Only removes empty directories.

        Args:
            operation: Operation data

        Returns:
            Result dictionary
        """
        dir_path = operation.get("path")

        # Validation
        if not dir_path:
            return {"success": False, "error": "Missing directory path"}

        is_valid, error, dir_path = self.validate_file_path(dir_path)
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(dir_path):
            return {"success": False, "error": f"Directory not found: {dir_path}"}

        if not os.path.isdir(dir_path):
            return {"success": False, "error": f"Path is not a directory: {dir_path}"}

        # Check protected paths
        if self.is_protected_path(dir_path):
            return {
                "success": False,
                "error": f"Cannot delete protected directory: {dir_path}",
            }

        # Check if directory is empty
        try:
            if os.listdir(dir_path):
                return {
                    "success": False,
                    "error": f"Directory not empty: {dir_path}. Only empty directories can be removed.",
                }
        except Exception as e:
            return {"success": False, "error": f"Failed to check directory: {str(e)}"}

        # Remove directory
        try:
            os.rmdir(dir_path)

            return {"success": True, "output": f"✓ Removed directory: {dir_path}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to remove directory: {str(e)}"}

    def _get_context_service(self):
        """Get the ContextService from the event bus.

        Returns:
            ContextService instance or None if not available.
        """
        if self.event_bus is None:
            return None
        return self.event_bus.get_service("context_service")

    def _cap_read_output(
        self, content: str, filepath: str, total_lines: int, start_line: int = 0
    ) -> Tuple[str, str]:
        """Cap a read's returned text so one file can't blow the context window.

        Reading a whole large file dumps its full content into conversation
        history. Combined with the system prompt, tool schemas, and the
        max_tokens budget that can exceed the provider context window, and the
        next API call returns empty (stop_reason=model_context_window_exceeded),
        bricking the session. Truncate here and tell the agent exactly which
        commands to use to fetch the rest surgically.

        Args:
            content: Text about to be returned to the model.
            filepath: File being read (used in the guidance message).
            total_lines: Total line count of the file on disk.
            start_line: 0-indexed line the returned slice starts at, so the
                guidance can suggest the correct next offset.

        Returns:
            (possibly_truncated_content, guidance_note). guidance_note is ""
            when nothing was truncated.
        """
        max_lines = self.max_read_lines
        max_chars = self.max_read_output_chars
        full_chars = len(content)
        truncated = False

        if max_lines and content.count("\n") + 1 > max_lines:
            content = "\n".join(content.split("\n")[:max_lines])
            truncated = True

        if max_chars and len(content) > max_chars:
            content = content[:max_chars]
            newline = content.rfind("\n")
            if newline > 0:
                content = content[:newline]
            truncated = True

        if not truncated:
            return content, ""

        shown = content.count("\n") + 1
        next_offset = start_line + shown
        full_tokens_k = max(1, full_chars // 3500)
        # Describe the next action by tool + params rather than a literal tag,
        # so it reads correctly whether the agent calls tools natively
        # (file_read / file_grep) or via XML (<read> / <grep>).
        note = (
            f"\n\n[truncated — showing {shown} of {total_lines} lines]\n"
            f"The full file is ~{full_tokens_k}K tokens; loading it whole would "
            f"consume that much of your context window, so it was capped here. "
            f"To continue, read this file again with offset={next_offset}, "
            f"limit={max_lines} — or grep it for the specific part you need."
        )
        return content, note

    def _execute_read(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute read file operation.

        Args:
            operation: Operation data

        Returns:
            Result dictionary with file content
        """
        filepath = operation.get("file")
        lines_spec = operation.get("lines")  # Optional: "10-20"
        offset = operation.get("offset")  # Optional: line offset (0-indexed)
        limit = operation.get("limit")  # Optional: number of lines to read

        # Validation
        if not filepath:
            return {"success": False, "error": "Missing file path"}

        # Allow reading from ~/.kollab/ (agent/skill files)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check file size
        is_valid, error = self.check_file_size(filepath, self.max_read_size_mb)
        if not is_valid:
            return {"success": False, "error": error}

        # Check if text file
        if not self.is_text_file(filepath):
            return {"success": False, "error": f"Cannot read binary file: {filepath}"}

        # Read file as bytes (needed for ContextService hash dedup)
        try:
            with open(filepath, "rb") as f:
                disk_content_bytes = f.read()
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {str(e)}"}

        # Consult ContextService for dedup before returning content
        context_svc = self._get_context_service()
        force = operation.get("force", False)
        if context_svc is not None:
            hook_result = context_svc.file_read_hook(
                path=filepath,
                disk_content=disk_content_bytes,
                lines_spec=lines_spec,
                force=force,
            )

            if hook_result["action"] == "stale":
                return {
                    "success": True,
                    "output": hook_result["content"].decode("utf-8"),
                }

            if hook_result["action"] == "diff":
                return {
                    "success": True,
                    "output": hook_result["content"].decode("utf-8"),
                }

            # fresh or force_fresh: fall through to normal read path

        # Decode bytes to text for normal processing
        try:
            content = disk_content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "success": False,
                "error": f"Cannot read binary file: {filepath}",
            }

        # Count total lines for display
        all_lines = content.split("\n")
        total_lines = len(all_lines)

        # Handle offset/limit if specified (Claude Code style)
        if offset is not None or limit is not None:
            start_line = offset if offset is not None else 0
            end_line = start_line + limit if limit is not None else total_lines
            end_line = min(end_line, total_lines)

            selected_lines = all_lines[start_line:end_line]
            content = "\n".join(selected_lines)
            line_count = len(selected_lines)

            # Calculate 1-indexed display range
            display_start = start_line + 1
            display_end = start_line + line_count

            content, note = self._cap_read_output(
                content, filepath, total_lines, start_line=start_line
            )
            return {
                "success": True,
                "output": (
                    f"✓ Read {line_count} lines from {filepath} "
                    f"(lines {display_start}-{display_end}):\n\n{content}{note}"
                ),
            }

        # Handle line range if specified (lines="10-20" style)
        if lines_spec:
            try:
                if "-" in lines_spec:
                    start_str, end_str = lines_spec.split("-")
                    start_line = int(start_str.strip()) - 1  # Convert to 0-indexed
                    end_line = int(end_str.strip())
                else:
                    start_line = int(lines_spec.strip()) - 1
                    end_line = start_line + 1

                selected_lines = all_lines[start_line:end_line]
                content = "\n".join(selected_lines)
                line_count = len(selected_lines)

                content, note = self._cap_read_output(
                    content, filepath, total_lines, start_line=start_line
                )
                return {
                    "success": True,
                    "output": f"✓ Read {line_count} lines from {filepath} (lines {lines_spec}):\n\n{content}{note}",
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Invalid line specification '{lines_spec}': {str(e)}",
                }

        content, note = self._cap_read_output(content, filepath, total_lines)
        if note:
            # Truncated: report what was actually returned, not the file's full
            # length, so the UI summary and the model both see the real size.
            # Keep "Read <n> lines from <path>" contiguous for path extraction.
            shown = content.count("\n") + 1
            header = (
                f"✓ Read {shown} lines from {filepath} "
                f"(lines 1-{shown}, truncated from {total_lines}):"
            )
        else:
            header = f"✓ Read {total_lines} lines from {filepath}:"
        return {
            "success": True,
            "output": f"{header}\n\n{content}{note}",
        }

    def _execute_grep(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        """Execute grep file operation (search for pattern in file).

        Args:
            operation: Operation data

        Returns:
            Result dictionary with matching lines
        """
        filepath = operation.get("file")
        pattern = operation.get("pattern")
        case_insensitive = operation.get("case_insensitive", False)

        # Validation
        if not filepath:
            return {"success": False, "error": "Missing file path"}

        if not pattern:
            return {"success": False, "error": "Missing search pattern"}

        # Allow grepping from ~/.kollab/ (agent/skill files)
        is_valid, error, filepath = self.validate_file_path(
            filepath, allow_kollabor_config=True
        )
        if not is_valid:
            return {"success": False, "error": error}

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        # Check file size
        is_valid, error = self.check_file_size(filepath, self.max_read_size_mb)
        if not is_valid:
            return {"success": False, "error": error}

        # Check if text file
        if not self.is_text_file(filepath):
            return {"success": False, "error": f"Cannot grep binary file: {filepath}"}

        # Read file and search for pattern
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            return {"success": False, "error": f"Failed to read file: {str(e)}"}

        # Search for pattern in each line
        import re

        matches = []
        flags = re.IGNORECASE if case_insensitive else 0

        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"success": False, "error": f"Invalid regex pattern: {str(e)}"}

        for line_num, line in enumerate(lines, start=1):
            if regex.search(line):
                # Remove trailing newline for cleaner display
                matches.append((line_num, line.rstrip("\n")))

        # Build result output
        if not matches:
            return {
                "success": True,
                "output": f"✓ No matches found for '{pattern}' in {filepath}",
            }

        match_count = len(matches)
        result_lines = [
            f"✓ Found {match_count} match{'es' if match_count != 1 else ''} for '{pattern}' in {filepath}:\n"
        ]

        # Show up to 50 matches
        for line_num, line_content in matches[:50]:
            result_lines.append(f"{line_num}: {line_content}")

        if len(matches) > 50:
            result_lines.append(f"\n... ({len(matches) - 50} more matches)")

        return {"success": True, "output": "\n".join(result_lines)}
