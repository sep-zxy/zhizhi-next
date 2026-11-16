// Accidental-use guard only; GitHub supplies the actual runner environment.
if (process.env.GITHUB_ACTIONS !== 'true' || process.env.RUNNER_ENVIRONMENT !== 'github-hosted') {
  console.error('构建仅允许在 GitHub 托管 Actions runner 执行。');
  process.exit(1);
}
