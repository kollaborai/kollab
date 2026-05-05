completion checklist:

before closing out any feature or fix, run this audit.

robustness:
  [ ] identify 5 failure modes and implement fixes
  [ ] are errors visible to the user? clear success/failure feedback?
  [ ] did this remove or break existing functionality?
  [ ] are there pre-existing errors in nearby code? fix them too

testing:
  [ ] tests written for new logic?
  [ ] linter passes with 0 violations?
  [ ] all modified files compile/parse cleanly?
  [ ] existing test suite still green?

documentation:
  [ ] project docs updated for new components or patterns?
  [ ] README updated if user-facing feature?
  [ ] related docs cross-linked?

code quality:
  [ ] no hardcoded values that should be configurable?
  [ ] error messages are actionable (not just "something went wrong")?
  [ ] no print/stdout in library code (use proper logging)?
  [ ] follows existing patterns in the codebase (don't invent new ones)?

changelog:
  [ ] git commit with clear message?
  [ ] breaking changes documented?
