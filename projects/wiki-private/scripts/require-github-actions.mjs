if (process.env.GITHUB_ACTIONS !== "true" || !process.env.GITHUB_RUN_ID) {
  console.error(
    "正式构建与打包只在 GitHub Actions 执行；本地使用 typecheck、test 和 dev 验证。",
  );
  process.exit(1);
}
