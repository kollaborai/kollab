session context:
  time:              <trender>date '+%Y-%m-%d %H:%M:%S %Z'</trender>
  system:            <trender>uname -s</trender> <trender>uname -m</trender>
  user:              <trender>whoami</trender> @ <trender>hostname</trender>
  shell:             <trender>echo $SHELL</trender>
  working directory: <trender>pwd</trender>

git repository:
<trender>
if [ -d .git ]; then
  echo "  [ok] git repo detected"
  echo "       branch: $(git branch --show-current 2>/dev/null || echo 'unknown')"
  echo "       remote: $(git remote get-url origin 2>/dev/null || echo 'none')"
  echo "       status: $(git status --short 2>/dev/null | wc -l | tr -d ' ') files modified"
  echo "       last commit: $(git log -1 --format='%h - %s (%ar)' 2>/dev/null || echo 'none')"
else
  echo "  [warn] not a git repository"
fi
</trender>

docker environment:
<trender>
if [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
  echo "  [ok] docker compose detected"
  echo "       compose file: $(ls docker-compose.y*ml 2>/dev/null | head -1)"
  echo "       services: $(grep -E '^\s+\w+:' docker-compose.y*ml 2>/dev/null | wc -l | tr -d ' ')"
  if command -v docker &> /dev/null; then
    echo "       running: $(docker ps --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ') containers"
    if [ $(docker ps -q 2>/dev/null | wc -l) -gt 0 ]; then
      echo "       active containers:"
      docker ps --format '         - {{.Names}} ({{.Status}})' 2>/dev/null | head -5
    fi
  fi
elif [ -f "Dockerfile" ]; then
  echo "  [ok] dockerfile detected"
  if command -v docker &> /dev/null; then
    echo "       running: $(docker ps -q 2>/dev/null | wc -l | tr -d ' ') containers"
  fi
else
  echo "  [warn] no docker configuration found"
fi
</trender>

python environment:
<trender>
if [ -f "requirements.txt" ] || [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
  echo "  [ok] python project detected"
  echo "       version: $(python --version 2>&1 | cut -d' ' -f2)"
  if [ -n "$VIRTUAL_ENV" ]; then
    echo "       venv: $(basename $VIRTUAL_ENV) (active)"
  else
    echo "       [warn] venv: none (consider activating)"
  fi
  if [ -f "requirements.txt" ]; then
    echo "       requirements: $(wc -l < requirements.txt | tr -d ' ') packages"
  fi
  if [ -f "pyproject.toml" ]; then
    echo "       build: pyproject.toml detected"
  fi
else
  echo "  [warn] not a python project"
fi
</trender>

node/npm environment:
<trender>
if [ -f "package.json" ]; then
  # Check if this is actually a node.js project (has deps, source files, or node_modules)
  has_deps=$(grep -E '"(dependencies|devDependencies)"' package.json | wc -l | tr -d ' ')
  has_source=$(fd -e js -e ts -e jsx -e tsx . --max-depth 3 --exclude node_modules 2>/dev/null | wc -l | tr -d ' ')

  if [ "$has_deps" -gt 0 ] || [ -d "node_modules" ] || [ "$has_source" -gt 0 ]; then
    echo "  [ok] node.js project detected"
    if command -v node &> /dev/null; then
      echo "       node: $(node --version 2>/dev/null)"
      echo "       npm: $(npm --version 2>/dev/null)"
    fi
    if [ "$has_deps" -gt 0 ]; then
      echo "       dependencies section found"
    else
      echo "       dependencies: 0"
    fi
    if [ "$has_source" -gt 0 ]; then
      echo "       source files: $has_source"
    fi
    if [ -f "package-lock.json" ]; then
      echo "       lock: package-lock.json"
    elif [ -f "yarn.lock" ]; then
      echo "       lock: yarn.lock"
    fi
    if [ -d "node_modules" ]; then
      echo "       [ok] node_modules installed"
    else
      echo "       [warn] node_modules not installed (run npm install)"
    fi
  else
    echo "  [warn] package.json found but no node.js dependencies or source files"
  fi
else
  echo "  [warn] not a node.js project"
fi
</trender>

rust environment:
<trender>
if [ -f "Cargo.toml" ]; then
  echo "  [ok] rust project detected"
  if command -v rustc &> /dev/null; then
    echo "       rustc: $(rustc --version 2>/dev/null | cut -d' ' -f2)"
    echo "       cargo: $(cargo --version 2>/dev/null | cut -d' ' -f2)"
  fi
  echo "       targets: $(grep -c '\[\[bin\]\]' Cargo.toml 2>/dev/null || echo '1')"
else
  echo "  [warn] not a rust project"
fi
</trender>

go environment:
<trender>
if [ -f "go.mod" ]; then
  echo "  [ok] go project detected"
  if command -v go &> /dev/null; then
    echo "       version: $(go version 2>/dev/null | awk '{print $3}')"
  fi
  echo "       module: $(grep '^module' go.mod | awk '{print $2}')"
  echo "       deps: $(grep -c '^\s*require' go.mod 2>/dev/null || echo '0')"
else
  echo "  [warn] not a go project"
fi
</trender>

kubernetes/k8s:
<trender>
if [ -d "k8s" ] || [ -d "kubernetes" ] || ls *-deployment.yaml &>/dev/null 2>&1; then
  echo "  [ok] kubernetes configs detected"
  if command -v kubectl &> /dev/null; then
    echo "       context: $(kubectl config current-context 2>/dev/null || echo 'none')"
    echo "       namespaces: $(kubectl get namespaces --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  fi
else
  echo "  [warn] no kubernetes configuration"
fi
</trender>

database files:
<trender>
dbs=""
[ -f "*.db" ] || [ -f "*.sqlite" ] || [ -f "*.sqlite3" ] && dbs="$dbs SQLite"
[ -f "*.sql" ] && dbs="$dbs SQL"
if [ -n "$dbs" ]; then
  echo "  [ok] database files found:$dbs"
else
  echo "  [warn] no database files detected"
fi
</trender>

project files:
<trender>
echo "  key files present:"
[ -f "README.md" ] && echo "    [ok] README.md"
[ -f "LICENSE" ] && echo "    [ok] LICENSE"
[ -f ".gitignore" ] && echo "    [ok] .gitignore"
[ -f "Makefile" ] && echo "    [ok] Makefile"
[ -f ".env" ] && echo "    [warn] .env (contains secrets - be careful!)"
[ -f ".env.example" ] && echo "    [ok] .env.example"
true
</trender>

recent activity:
<trender>
if [ -d .git ]; then
  echo "  recent commits:"
  git log --oneline --format='    %h - %s (%ar)' -5 2>/dev/null || echo "    no commits yet"
else
  echo "  not a git repository"
fi
</trender>
