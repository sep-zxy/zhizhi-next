/** Match backend routes only. Vite source modules such as /src/api/locale.ts are not API calls. */
export function isBackendApiRoute(url: URL): boolean {
  return url.pathname.startsWith('/api/');
}
