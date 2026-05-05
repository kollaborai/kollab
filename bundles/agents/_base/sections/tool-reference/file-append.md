file append and insert operations:

append to files:
  <append>
  <file>path/to/file.py</file>
  <content>

  def additional_function():
      pass
  </content>
  </append>

insert (pattern must be UNIQUE):
  <insert_after>
  <file>path/to/file.py</file>
  <pattern>class MyClass:</pattern>
  <content>
      """Class docstring."""
  </content>
  </insert_after>

key rules:
  [1] <insert_after>/<insert_before> require UNIQUE pattern (errors if 0 or 2+)
  [2] whitespace in patterns must match exactly
