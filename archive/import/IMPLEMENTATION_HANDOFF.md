# Cloud source snapshot handoff

Current filesystem source, including eligible uncommitted and untracked code, is included. SOURCE_MANIFEST.json records Git HEADs, safe origins, source changes, omissions and hashes. No Git objects are copied. Import into one NEW cloud repository; create four worktrees there after the design stage.

- User requested design diagrams before implementation and hourly reporting via the main conversation.
- Delivery requires end-to-end validation. No test or build was performed for this snapshot.
- Preserve projects/growth/AGENTS.md: ALL builds must use GitHub-hosted GitHub Actions runners. Local builds and self-hosted runners are prohibited, including local builds in the cloud workspace.
- Preserve nested AGENTS.md, licenses, notices, lockfiles, package manifests and current code.
- No secrets, runtime databases, real vault notes/Wiki/daily entries or published content are supplied. Create synthetic fixtures in the cloud.
- Dependencies can be restored from official registries using included manifests/lockfiles when installation is appropriate. Registry credentials are intentionally omitted.
- Suspected-credential files are omitted wholesale. Inspect EXCLUSIONS.json before implementation; omitted required modules are explicit handoff gaps.
- vault-manager has no Git repository at its local root. selfweb outer folder has no Git repository; its Git summary applies to selfweb/site.
- Dot-prefixed .quartz plugin SOURCE is retained because current Quartz routing and layout require it.
- No deployment credentials or new deployment authority are included.

SHA256SUMS covers every payload file except itself. Outer ZIP SHA256 is in the separate DELIVERY.json. Exclusion directory roots are recorded without enumerating contents. Heuristic credential scanning cannot certify absolute absence of all secrets.
