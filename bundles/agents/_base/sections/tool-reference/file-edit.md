file editing, creation, and deletion:

edit files (replaces ALL occurrences):
  <edit>
  <file>path/to/file.py</file>
  <find>old_code_here</find>
  <replace>new_code_here</replace>
  </edit>

create files:
  <create>
  <file>path/to/new_file.py</file>
  <content>
  """New file content."""
  import logging

  def new_function():
      pass
  </content>
  </create>

delete files:
  <delete><file>path/to/old_file.py</file></delete>

safety features:
  [ok] auto backups: .bak before edits, .deleted before deletion
  [ok] protected files: kollabor/, main.py, .git/, venv/
  [ok] python syntax validation with automatic rollback on errors
  [ok] file size limits: 10MB edit, 5MB create

key rules:
  [1] <edit> replaces ALL matches (use context to make pattern unique)
  [2] whitespace in <find> must match exactly
  [3] use file operations for code changes, terminal for git/pip/pytest

use <edit> instead of:
  <terminal>sed -i 's/old/new/' file.py</terminal>  // WRONG
  <edit><file>file.py</file><find>old</find><replace>new</replace></edit>  // CORRECT

use <create> instead of:
  <terminal>cat > file.py << 'EOF'
  content
  EOF</terminal>  // WRONG
  <create><file>file.py</file><content>content</content></create>  // CORRECT
