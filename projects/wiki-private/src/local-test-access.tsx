import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { type WikiApi, WikiApiError } from "./api";
import { Badge, Icon, Notice } from "./components";

function authMessage(error: unknown, logout: boolean): string {
  if (logout)
    return "已清除本页会话资料，但服务端注销未得到确认。不要把这次结果当作已成功撤销；服务端测试会话最多 15 分钟到期。";
  if (!(error instanceof WikiApiError))
    return "无法验证配对结果，没有建立已认证状态。";
  if (error.status === 401 || error.status === 404)
    return "当前服务未提供可用的本机测试配对。请使用已获准、已初始化的本机隔离测试启动入口；不要在网页填写令牌。";
  if (error.status === 403)
    return "本次请求未通过本机同源与会话保护检查。请使用测试包提供的 127.0.0.1 入口，不要跨端口代理或放宽权限。";
  if (error.status === 429)
    return "配对请求已达到服务限制。请稍后显式重试，页面不会自动重复申请。";
  if (error.code === "NETWORK_ERROR")
    return "连接结果未得到确认。页面不会自动重试或把断连当作配对成功。";
  return "服务返回的配对结果无法验证，没有按成功处理。请重新申请，不要粘贴任何令牌或密钥。";
}

/** Only the public, non-secret snapshot is visible to React. */
export function LocalTestAccess({
  api,
  blocked,
}: {
  api: WikiApi;
  blocked: boolean;
}) {
  const session = useSyncExternalStore(
    api.subscribeLocalTestSession,
    api.getLocalTestSession,
    api.getLocalTestSession,
  );
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!session.expiresAt) return;
    const expire = () => {
      setNow(Date.now());
      api.expireLocalTestSession();
    };
    expire();
    const timer = window.setInterval(expire, 1000);
    document.addEventListener("visibilitychange", expire);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", expire);
    };
  }, [api, session.expiresAt]);

  async function action(kind: "request" | "check" | "logout") {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (kind === "request") await api.requestLocalTestPairing();
      else if (kind === "check") {
        const result = await api.checkLocalTestSession();
        if (result.status === "waiting_approval")
          setMessage("尚未收到包内配对工具的批准。配对码本身不是已认证会话。");
        if (result.status === "waiting_auth")
          setMessage("申请已失效或未取得有效会话，请重新配对。");
      } else {
        await api.logoutLocalTestSession();
        setMessage("服务已确认注销本次测试会话，本页会话资料已清除。");
      }
    } catch (value) {
      setError(authMessage(value, kind === "logout"));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  if (!blocked && session.status !== "authenticated" && !message && !error)
    return null;
  const remaining = Math.max(0, Math.ceil((session.expiresAt - now) / 1000));
  return (
    <section className="panel local-test-access" aria-label="本机隔离测试会话">
      <div className="panel-heading">
        <h2>
          <Icon name="lock" />
          本机隔离测试配对
        </h2>
        <Badge tone="warning">仅合成测试数据 · 非生产身份</Badge>
      </div>
      <p className="muted">
        只用于已获准的 Windows 隔离成品包。不是个人资料登录，不启用
        worker、模型或渠道，也不代表完整端到端验收通过。
      </p>
      {session.status === "authenticated" ? (
        <>
          <Notice title="已连接本机测试身份">
            服务已确认本次测试会话，剩余约 {remaining}{" "}
            秒。账本为空时会真实显示空目录，不导入个人资料。
          </Notice>
          <button
            className="button"
            disabled={busy}
            onClick={() => void action("logout")}
          >
            {busy ? "正在注销…" : "注销本次测试会话"}
          </button>
        </>
      ) : session.status === "waiting_approval" ? (
        <>
          <p>
            在已初始化的测试包中运行包内配对工具，并输入下方一次性配对码。完成后点击“检查本机批准”。
          </p>
          <output className="pairing-code" aria-label="一次性配对码">
            {session.pairingCode}
          </output>
          <p className="muted small">
            申请剩余约 {remaining} 秒。配对码不写入
            URL、存储或日志，请勿上传或提交到 Git。
          </p>
          <div className="actions">
            <button
              className="button primary"
              disabled={busy || !remaining}
              onClick={() => void action("check")}
            >
              {busy ? "正在检查…" : "检查本机批准"}
            </button>
            <button
              className="button"
              disabled={busy}
              onClick={() => {
                api.forgetLocalTestSession();
                setMessage(
                  "已清除此页配对信息；服务端未完成的申请会在短期有效期结束后失效。",
                );
                setError("");
              }}
            >
              取消本页配对
            </button>
          </div>
        </>
      ) : (
        <>
          <Notice title="默认关闭，须显式申请">
            仅在已配置本机测试身份的隔离服务上可用。浏览器不接收服务
            bearer；会话证明只留在内存，刷新后必须重新配对。
          </Notice>
          <button
            className="button primary"
            disabled={busy}
            onClick={() => void action("request")}
          >
            {busy ? "正在申请…" : "申请本机测试配对码"}
          </button>
        </>
      )}
      {message && (
        <p role="status" className="muted">
          {message}
        </p>
      )}
      {error && (
        <div role="alert">
          <Notice tone="warning" title="操作未确认成功">
            {error}
          </Notice>
        </div>
      )}
    </section>
  );
}
