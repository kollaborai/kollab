---
name: dead-code
description: "dead-code - An agent that looks for dead code and create a markdown report"
---

skill name: dead-code

purpose:
  systematically identify, categorize, and report dead code across the codebase.
  provides multi-phase analysis for unused functions, variables, imports, classes,
  and unreachable code paths. generates comprehensive markdown reports with
  actionable recommendations.

when to use:
  [ ] codebase cleanup and maintenance
  [ ] reducing technical debt
  [ ] preparing for major refactoring
  [ ] improving code coverage
  [ ] before deploying to production
  [ ] onboarding to new codebase
  [ ] performance optimization

methodology:

phase 0: environment and context verification
  understand project structure
  identify programming languages
  verify tool availability
  establish scope boundaries
  check git history for context

phase 1: static analysis discovery
  unused imports detection
  unused functions and methods
  unused variables and constants
  unused classes and modules
  commented-out code blocks

phase 2: dynamic usage analysis
  cross-reference function calls
  analyze import dependencies
  check test coverage gaps
  identify unreachable code paths
  verify configuration references

phase 3: categorization and prioritization
  classify dead code by severity
  assess removal risk and impact
  identify safe-to-delete candidates
  flag potentially dead-but-uncertain code
  create action priorities

phase 4: markdown report generation
  compile findings into structured report
  include code snippets with line numbers
  provide removal recommendations
  document dependencies and side effects
  create automated cleanup scripts

phase 5: verification and validation
  cross-check findings with tests
  validate safe deletions
  document false positives
  create pull request with changes


phase 0: environment and context verification

step 1: understand project structure

  <terminal>pwd</terminal>
  <terminal>ls -la</terminal>
  <terminal>find . -type f -name "*.py" -o -name "*.js" -o -name "*.ts" | head -30</terminal>

identify primary languages:
  <terminal>find . -type f \( -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.java" -o -name "*.go" -o -name "*.rs" \) | wc -l</terminal>

check for build systems:
  <terminal>ls -la | grep -E "(package.json|requirements.txt|Cargo.toml|go.mod|pom.xml|build.gradle)"</terminal>


step 2: identify codebase type

  <terminal>if [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then echo "python"; fi</terminal>
  <terminal>if [ -f "package.json" ]; then echo "javascript/typescript"; fi</terminal>
  <terminal>if [ -f "Cargo.toml" ]; then echo "rust"; fi</terminal>
  <terminal>if [ -f "go.mod" ]; then echo "go"; fi</terminal>

detect frameworks:
  <terminal>grep -r "from django" . --include="*.py" 2>/dev/null | head -1 && echo "django"</terminal>
  <terminal>grep -r "import React" . --include="*.jsx" 2>/dev/null | head -1 && echo "react"</terminal>


step 3: verify tool availability

check for python dead code tools:
  <terminal>which vulture || which pyflakes || which pycodestyle || echo "no python linter found"</terminal>

check for javascript tools:
  <terminal>which eslint || echo "eslint not found"</terminal>

install tools if needed:
  <terminal>pip install vulture autoflake 2>/dev/null || echo "pip install vulture autoflake"</terminal>


step 4: establish scope boundaries

define what to include/exclude:
  <terminal>cat > /tmp/dead_code_scope.txt << 'EOF'
included:
  - main source code directories (src/, kollabor/, lib/, app/)
  - application logic
  - utility functions

excluded:
  - test directories (tests/, test/, __tests__/)
  - third-party dependencies (node_modules/, venv/, .venv/)
  - build artifacts (dist/, build/, *.egg-info/)
  - migration files (migrations/)
  - auto-generated code
EOF
cat /tmp/dead_code_scope.txt</terminal>


step 5: check git history

identify recently modified files:
  <terminal>git log --name-only --pretty=format: --since="3 months ago" | grep -v "^$" | sort -u | head -20</terminal>

find old untouched files (potential dead code):
  <terminal>find . -name "*.py" -mtime +180 ! -path "./tests/*" ! -path "./venv/*" ! -path "./.venv/*" -type f | head -10</terminal>

check for large files with low activity:
  <terminal>find . -name "*.py" -size +10k ! -path "./tests/*" -exec ls -lh {} \; | awk '{print $9, $5}'</terminal>


phase 1: static analysis discovery

python-specific analysis:

step 1: unused imports detection using vulture

  <terminal>vulture . --min-confidence 60 --exclude "venv,.venv,tests,migrations,node_modules" 2>&1 | tee /tmp/vulture_report.txt</terminal>

  <terminal>cat /tmp/vulture_report.txt | grep "unused import"</terminal>

alternative using autoflake:
  <terminal>autoflake --remove-all-unused-imports --recursive --exclude .venv,venv,tests . 2>&1 | tee /tmp/autoflake_report.txt</terminal>


step 2: unused functions and classes

  <terminal>vulture . --min-confidence 70 --exclude "venv,.venv,tests,migrations" --sort-by-size 2>&1 | grep -E "unused function|unused class|unused attribute"</terminal>

find defined but never called functions:
  <terminal>grep -rn "^def " . --include="*.py" --exclude-dir=venv --exclude-dir=.venv --exclude-dir=tests | grep -v "__" | awk -F: '{print $1}' | sort -u | head -20</terminal>

for each candidate function, check if it's called:
  <terminal>grep -r "function_name" . --include="*.py" --exclude-dir=tests | wc -l</terminal>


step 3: unused variables and constants

  <terminal>vulture . --min-confidence 80 --exclude "venv,.venv,tests" | grep -E "unused variable|unused attribute"</terminal>

find unused global variables:
  <terminal>grep -rn "^[A-Z_][A-Z0-9_]*\s*=" . --include="*.py" --exclude-dir=venv --exclude-dir=.venv --exclude-dir=tests | head -20</terminal>

verify usage:
  <terminal>grep -r "VARIABLE_NAME" . --include="*.py" | wc -l</terminal>


step 4: commented-out code blocks

find multi-line commented code:
  <terminal>grep -rn "^# def\|^# class\|^# import\|^# from" . --include="*.py" --exclude-dir=venv --exclude-dir=.venv --exclude-dir=tests | head -30</terminal>

find TODO/FIXME comments (potential unfinished code):
  <terminal>grep -rn "TODO:\|FIXME:\|HACK:\|XXX:" . --include="*.py" --exclude-dir=venv --exclude-dir=.venv --exclude-dir=tests | head -20</terminal>


javascript/typescript-specific analysis:

step 1: unused imports using eslint

  <terminal>eslint . --ext .js,.jsx,.ts,.tsx --rule "no-unused-vars: error" --ignore-pattern "node_modules/*" 2>&1 | tee /tmp/eslint_report.txt</terminal>

  <terminal>cat /tmp/eslint_report.txt | grep "no-unused-vars"</terminal>


step 2: unused functions and variables

  <terminal>eslint . --ext .js,.jsx,.ts,.tsx --rule "no-unused-vars: [2, { \"vars\": \"all\", \"args\": \"after-used\", \"ignoreRestSiblings\": false }]" --ignore-pattern "node_modules/*"</terminal>

find exported but unused functions:
  <terminal>grep -rn "export.*function\|export const" . --include="*.js" --include="*.ts" --exclude-dir=node_modules | head -20</terminal>


step 3: find dead code in common patterns

unused event handlers:
  <terminal>grep -rn "on[A-Z].*=\|onClick\|onSubmit" . --include="*.jsx" --include="*.tsx" | head -20</terminal>

duplicate function definitions:
  <terminal>grep -rn "^function\|^const.*=.*=>" . --include="*.js" --include="*.ts" | cut -d: -f3 | sort | uniq -d | head -10</terminal>


phase 2: dynamic usage analysis

step 1: cross-reference function calls

create function call map for python:
  <terminal>cat > /tmp/analyze_calls.py << 'EOF'
import ast
import sys
from pathlib import Path
from collections import defaultdict

def analyze_file(filepath):
    with open(filepath, 'r') as f:
        try:
            tree = ast.parse(f.read(), filepath)
        except:
            return None
    
    functions = []
    calls = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    
    return filepath, functions, calls

def main():
    defined = defaultdict(list)
    called = defaultdict(int)
    
    for py_file in Path('.').rglob('*.py'):
        if 'venv' in str(py_file) or '.venv' in str(py_file) or 'tests' in str(py_file):
            continue
        
        result = analyze_file(py_file)
        if result:
            filepath, functions, calls = result
            for func in functions:
                defined[func].append(filepath)
            for call in calls:
                called[call] += 1
    
    print("=== POTENTIALLY UNUSED FUNCTIONS ===")
    for func, files in sorted(defined.items()):
        if func.startswith('_'):
            continue
        if called.get(func, 0) == 0 and len(files) == 1:
            print(f"{func}: defined in {files[0]}")

if __name__ == "__main__":
    main()
EOF
python /tmp/analyze_calls.py</terminal>


step 2: analyze import dependencies

find imported but unused modules:
  <terminal>grep -rn "^import \|^from " . --include="*.py" --exclude-dir=venv --exclude-dir=.venv --exclude-dir=tests | sed 's/.*import //' | sed 's/ as .*//' | sort -u | head -30</terminal>

check for circular imports:
  <terminal>python -c "
import ast
import sys
from pathlib import Path

imports = {}
for py_file in Path('.').rglob('*.py'):
    if 'venv' in str(py_file) or '.venv' in str(py_file):
        continue
    try:
        with open(py_file) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.setdefault(str(py_file), set()).add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.setdefault(str(py_file), set()).add(node.module.split('.')[0])
    except:
        pass

# Check for cycles
for file, deps in imports.items():
    for dep in deps:
        dep_file = None
        for f in imports.keys():
            if dep in f.replace('/', '.').replace('.py', ''):
                dep_file = f
                break
        if dep_file and file in imports.get(dep_file, set()):
            print(f'Circular: {file} <-> {dep_file}')
"</terminal>


step 3: check test coverage gaps

if pytest coverage is available:
  <terminal>python -m pytest --cov=. --cov-report=term-missing 2>/dev/null | grep -E "TOTAL|.*\.py.*\d+%")</terminal>

find files with zero or low coverage:
  <terminal>python -m pytest --cov=. --cov-report=json 2>/dev/null && cat coverage.json | grep -A 3 '"files"' | grep -E "name|covered_percent" | paste - - | awk '$2 < 50 {print}' | head -10</terminal>

identify untested functions:
  <terminal>grep -rn "^def " . --include="*.py" --exclude-dir=tests | while read line; do
    file=$(echo "$line" | cut -d: -f1)
    func=$(echo "$line" | cut -d: -f2 | sed 's/def //')
    if ! grep -r "$func" tests/ --include="*.py" >/dev/null 2>&1; then
      echo "$file:$func"
    fi
done | head -20</terminal>


step 4: identify unreachable code paths

find code after return statements:
  <terminal>grep -A 3 "return " . --include="*.py" --exclude-dir=tests | grep -E "^\s+[a-zA-Z_]" | grep -v "^--$" | head -20</terminal>

find impossible conditions:
  <terminal>grep -rn "if True:\|if False:\|if 1:\|if 0:" . --include="*.py" --exclude-dir=tests | head -10</terminal>

find empty exception handlers:
  <terminal>grep -A 2 "except.*:" . --include="*.py" --exclude-dir=tests | grep -E "^\s+pass\s*$" | head -10</terminal>


step 5: verify configuration references

find config keys that might be unused:
  <terminal>grep -rn "os.getenv\|os.environ" . --include="*.py" --exclude-dir=tests | sed "s/.*os.getenv(['\"]//" | sed "s/['\"].*//" | sort -u | head -20</terminal>

check .env.example vs actual usage:
  <terminal>if [ -f ".env.example" ]; then
  while IFS='=' read -r key value; do
    if [ ! -z "$key" ] && [[ ! "$key" == "#"* ]]; then
      if ! grep -r "$key" . --include="*.py" --exclude-dir=tests >/dev/null 2>&1; then
        echo "Possibly unused: $key"
      fi
    fi
  done < .env.example | head -10
fi</terminal>


phase 3: categorization and prioritization

step 1: classify dead code by severity

create severity classification:

  [critical] - definitely dead, high confidence, safe to remove
    - unused imports
    - unused private functions (prefixed with _)
    - commented-out code blocks
    - unreachable code paths

  [high] - likely dead, moderate-high confidence
    - unused public functions not in tests
    - unused classes with no references
    - unused constants/variables
    - old migration files

  [medium] - potentially dead, needs verification
    - functions only called in tests (might be test-only utilities)
    - event handlers not bound
    - configuration keys not found in code

  [low] - possibly dead, requires manual review
    - functions with dynamic calls (getattr, __getattr__)
    - plugin interfaces
    - api endpoints
    - database models


step 2: assess removal risk and impact

check git history for recent changes:
  <terminal>git log --oneline --all -- "dead_code_candidate.py" | head -5</terminal>

check if file is in recent commits:
  <terminal>git log --since="6 months ago" --name-only --pretty=format: -- "dead_code_candidate.py" | wc -l</terminal>

identify files in main entry points:
  <terminal>grep -r "from.*import\|import" main.py setup.py pyproject.toml 2>/dev/null | grep -i "dead_code_candidate"</terminal>


step 3: identify safe-to-delete candidates

criteria for safe deletion:
  [x] no references in current codebase
  [x] not in git history in last 6 months
  [x] not in requirements or dependency declarations
  [x] no tests reference it
  [x] not an entry point or main module
  [x] not a public API

generate safe deletion list:
  <terminal>cat > /tmp/safe_delete_candidates.txt << 'EOF'
# Safe to delete - verified criteria met
EOF
# Add verified candidates to this file
cat /tmp/safe_delete_candidates.txt</terminal>


step 4: flag potentially dead-but-uncertain code

code that requires manual review:
  <terminal>cat > /tmp/requires_review.txt << 'EOF'
# Requires manual review - uncertain status
EOF

examples:
  - functions called via string (getattr(obj, method_name))
  - classes loaded dynamically (importlib.import_module)
  - api routes with swagger docs
  - database models with migrations
  - configuration with default values


step 5: create action priorities

priority 1 (immediate action):
  - unused imports (safe, easy wins)
  - commented-out code blocks (cleanup)
  - unreachable code after returns

priority 2 (next sprint):
  - unused private functions
  - unused utility classes
  - dead configuration keys

priority 3 (technical debt backlog):
  - old migration files
  - deprecated functions (marked with @deprecated)
  - experimental/feature flags

priority 4 (investigate first):
  - potentially unused public APIs
  - database models with uncertain usage
  - plugin interfaces


phase 4: markdown report generation

create comprehensive markdown report:

  <create><file>DEAD_CODE_REPORT.md</file><content># Dead Code Analysis Report

generated: $(date '+%Y-%m-%d %H:%M:%S')
project: $(basename $(pwd))
analysis scope: main source code (excluding tests, dependencies)


## executive summary

- total files analyzed: X
- total lines of code: Y
- potential dead code items found: Z
- safe to delete: A
- requires review: B
- estimated cleanup effort: C hours


## findings overview

### by category

| category | count | severity | safe to delete |
|----------|-------|----------|----------------|
| unused imports | | critical | yes |
| unused functions | | high | maybe |
| unused classes | | high | maybe |
| unused variables | | high | yes |
| commented code | | critical | yes |
| unreachable code | | critical | yes |


### by severity

- [critical] X items - definitely dead, safe to remove
- [high] Y items - likely dead, moderate confidence
- [medium] Z items - potentially dead, needs verification
- [low] W items - uncertain, requires manual review


## detailed findings

### 1. unused imports

confidence: 100%
action: safe to remove

#### high-priority files

**file: `src/module.py`**
- line 5: `import unused_module` - not referenced
- line 12: `from another import dead_function` - not called

```python
# before
import unused_module
from another import dead_function

def main():
    pass

# after
def main():
    pass
```

---

### 2. unused functions

confidence: 70-90%
action: review dependencies, then remove

#### `src/utils.py`

**function: `old_helper` (line 45)**
- defined but never called
- no references in codebase
- not in test files
- last modified: 2023-06-15

```python
def old_helper(data):
    """Deprecated helper function."""
    # This function is no longer used
    result = process(data)
    return result
```

recommendation: safe to delete

---

### 3. unused classes

confidence: 80%
action: verify inheritance/composition, then remove

#### `src/models.py`

**class: `LegacyModel` (line 120)**
- no instances found
- no imports or references
- replaced by `NewModel`

```python
class LegacyModel:
    """Old model class, deprecated."""
    
    def __init__(self):
        self.data = {}
```

recommendation: delete after verifying no schema references

---

### 4. commented-out code blocks

confidence: 100%
action: safe to remove

#### `src/main.py`

**lines 200-220: commented function**
```python
# def old_feature():
#     """This was removed in v1.2."""
#     pass
```

recommendation: remove comments

---

### 5. unreachable code paths

confidence: 95%
action: safe to remove

#### `src/processor.py`

**lines 85-90: code after return**
```python
def process_item(item):
    if not item:
        return None
    
    # This code is unreachable
    logger.info("Processing item")
    return transform(item)
```

recommendation: remove unreachable code

---

### 6. unused variables and constants

confidence: 90%
action: safe to remove

#### `src/config.py`

**line 15: `OLD_API_KEY`**
- defined but never used
- likely from removed feature

```python
OLD_API_KEY = "deprecated_key"  # unused
```

recommendation: delete

---

## requires manual review

### uncertain items

1. **`src/api.py::endpoint_handler`**
   - only called in tests
   - might be intentional test-only code
   - action: verify intent with team

2. **`src/plugins.py::load_plugin`**
   - uses dynamic imports
   - called via string name
   - grep may miss references
   - action: manual code review

3. **`src/database.py::LegacyTable`**
   - no code references
   - check database schema
   - action: verify in production database

---

## safe deletion script

python script to automate safe deletions:

```python
#!/usr/bin/env python3
"""automated dead code removal - use with caution!"""

import ast
import re
from pathlib import Path

def remove_unused_imports(filepath):
    """remove unused imports from a python file."""
    # run autoflake on the file
    import subprocess
    result = subprocess.run(
        ['autoflake', '--remove-all-unused-imports', '--in-place', str(filepath)],
        capture_output=True
    )
    return result.returncode == 0

def remove_commented_code(filepath):
    """remove large commented-out blocks."""
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    # TODO: implement smart commented code detection
    # Look for 3+ consecutive commented lines that look like code
    
    with open(filepath, 'w') as f:
        f.writelines(lines)

# main execution
if __name__ == "__main__":
    # always review changes before committing!
    pass
```

---

## recommendations

### immediate actions (this week)

1. remove all unused imports
   - estimated effort: 1-2 hours
   - tool: autoflake or vulture
   - risk: very low

2. remove commented-out code blocks
   - estimated effort: 2-3 hours
   - tool: manual review + script
   - risk: low

3. fix unreachable code paths
   - estimated effort: 1-2 hours
   - tool: code review
   - risk: low

### short-term actions (next sprint)

1. remove unused private functions
   - estimated effort: 4-6 hours
   - tool: vulture + manual review
   - risk: low-medium

2. clean up unused constants/variables
   - estimated effort: 2-3 hours
   - tool: vulture
   - risk: low

### long-term actions (technical debt backlog)

1. remove unused classes
   - estimated effort: 6-8 hours
   - tool: manual review + testing
   - risk: medium

2. clean up old migration files
   - estimated effort: 2-4 hours
   - tool: manual
   - risk: low

---

## prevention strategies

### code review guidelines

- [ ] check for new unused imports
- [ ] remove dead code before merging
- [ ] use linters in ci/cd
- [ ] require code coverage > 80%

### tool configuration

add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/PyCQA/autoflake
    rev: v1.7.0
    hooks:
      - id: autoflake
        args: ['--remove-all-unused-imports', '--in-place']
  
  - repo: https://github.com/jendrikseipp/vulture
    rev: v2.6
    hooks:
      - id: vulture
        args: ['--min-confidence', '70']
```

### continuous monitoring

schedule dead code analysis:
- weekly automated scan
- monthly comprehensive review
- quarterly cleanup sprint

---

## appendix

### tools used

- vulture: static analysis for dead code
- autoflake: remove unused imports
- grep: pattern matching
- git: history analysis
- custom scripts: dynamic analysis

### limitations

- cannot detect dynamic references (getattr, __import__)
- may have false positives for plugin systems
- test coverage gaps don't always mean dead code
- api endpoints may be called externally

### next analysis

recommended schedule: monthly
next analysis date: $(date -v+1m '+%Y-%m-%d')
