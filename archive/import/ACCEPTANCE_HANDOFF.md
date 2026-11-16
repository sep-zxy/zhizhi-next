# Acceptance scenario source handoff

The local root C:/Code/growth/acceptance does not exist. Genuine source was found at C:/Code/growth/.local/archive/2026-10-08/acceptance/scenarios. This package restores that source to projects/growth/acceptance/scenarios without modifying the local project or inventing replacements.

Extract over the main snapshot root. All 169 scenario source paths are new relative to the main snapshot. The original scripts are copied byte-for-byte; no actual run state is supplied.

The required m7_local_cloud_server.py exports load_identity(credentials, dsn, port, applied). Its bytes match historical commit 16a828ae0e68501a821e37ef7fcd8756ce1f7c5e. Required ahadiff cloud api/auth/db files and PyJWT/uvicorn/cryptography dependency declarations are already in the main package. No extra fabricated __init__.py files were added; this tree uses Python namespace packages.

EXCLUDED: locks, releases, runs, __pycache__, credential records, private keys, runtime databases, generated acceptance results and user knowledge documents. These data are not necessary to import the scenario helper. Create fresh isolated fixtures for cloud regression.

Credential scanning found only two deterministic sequential-character test fixtures in m3_publish_replay.py at L245/L284. They exercise privacy rejection of unsafe notes and are reviewed nonsecrets. No scanner values are printed in metadata.

Validation is static: 105 Python scenario files parse as AST, the required export exists, direct ahadiff dependency files are present in the main snapshot, and all current unit-test acceptance imports resolve to included source paths. No scenario or test was executed; no installation, build, deployment, key generation or database access occurred.

Preserve existing AGENTS.md constraints. All builds must use GitHub-hosted GitHub Actions runners. Do not run scenario scripts against existing credentials or real databases; their presence in this archive is not authorization to deploy.

ACCEPTANCE_FILE_LIST.txt contains the complete source list. ACCEPTANCE_MANIFEST.json maps every delivered path to original archived path, hash and validation. SHA256SUMS_ACCEPTANCE covers every payload file except itself.
