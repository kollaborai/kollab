engineering discipline

the core creed. it governs every change, no exceptions.

understand before you touch:
  read the code, its history, and its docs until you can trace the real
  execution path -- and assume your understanding is still wrong.

know how it fails before you change it:
  [1] edge cases
  [2] hidden dependencies
  [3] the rollback if you're wrong

change with discipline:
  make small, tested, atomic changes.
  record why, not just what.

prove it before you say done:
  prove the boring stuff works --
  [1] tests actually run
  [2] errors are handled
  [3] someone who isn't you could deploy and maintain it
