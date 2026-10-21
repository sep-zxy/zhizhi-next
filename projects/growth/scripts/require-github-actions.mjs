// One task-scoped, explicitly approved isolated-cloud exception. The normal
// GitHub-hosted requirement remains the default; CI identity is never spoofed.
const authorizedCloudBuild = process.env.ZHIZHI_ALLOW_ISOLATED_CLOUD_BUILD === '1'
  && process.env.ZHIZHI_BUILD_AUTHORIZATION === 'Sentinel_5be5b60cb6bc81918625cae977d6d6c7';
if (authorizedCloudBuild) {
  console.warn('本次隔离云构建使用用户明确授权的例外；不代表 GitHub Actions 或生产发布。');
} else if (process.env.GITHUB_ACTIONS !== 'true' || process.env.RUNNER_ENVIRONMENT !== 'github-hosted') {
  console.error('构建仅允许在 GitHub 托管的 Actions runner 上执行。请触发 Desktop Build 工作流并下载产物。');
  process.exit(1);
}
