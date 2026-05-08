### Structure
adls-gen2-lite/
  DESIGN.md
  AGENTS.md
  docker-compose.yml
  pyproject.toml or package.json
  src/
  tests/
  scripts/
    evaluate.sh
  examples/
    python_sdk_smoke.py

### MVP Scope
Filesystem operations:
- create filesystem
- list filesystems
- delete filesystem

Path operations:
- create directory
- create file
- append bytes to file
- flush file
- read file
- get file/directory properties
- list paths recursively and non-recursively
- rename file or directory
- delete file or directory

Runtime:
- Dockerized server
- persistent local volume
- in-memory mode for tests
- no-op auth or permissive SharedKey mode
- real Azure Python SDK smoke test