import { useEffect, useState, type ReactNode, type FormEvent } from "react";
import { ApiError, clearAuthSession, authenticate, restoreSession, getAuthConfig, sendEmailCode, resetPassword, type AuthSession, type AuthConfig } from "../lib/api";
import {CaptchaField, type CaptchaProof} from './CaptchaField';

export function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<"login" | "register" | "reset">("login");
  const [config, setConfig] = useState<AuthConfig|null>(null);
  const [email, setEmail] = useState('');
  const [emailCode, setEmailCode] = useState('');
  const [verificationId, setVerificationId] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [captcha, setCaptcha] = useState<CaptchaProof>({captcha_id:'',captcha_answer:''});
  const [captchaKey, setCaptchaKey] = useState(0);
  const [sending, setSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [notice, setNotice] = useState('');
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {getAuthConfig().then(setConfig).catch(e => setError(e.message));}, []);
  useEffect(() => {if (!cooldown) return; const timer=setTimeout(()=>setCooldown(v=>Math.max(0,v-1)),1000);return ()=>clearTimeout(timer);}, [cooldown]);
  useEffect(() => {
    let alive = true;
    const restore = () => restoreSession().then((value) => { if (alive) { setSession(value); setError(""); } }).catch((error) => { if (alive) { setSession(null); setError(error instanceof ApiError && error.status === 401 ? "" : error.message); } }).finally(() => { if (alive) setLoading(false); });
    const clear = () => { clearAuthSession(); if (alive) { setSession(null); setPassword(""); } };
    const visible = () => { if (document.visibilityState === "visible") void restore(); };
    const channel = new BroadcastChannel("methodpal-auth");
    channel.onmessage = clear;
    window.addEventListener("methodpal:unauthorized", clear);
    document.addEventListener("visibilitychange", visible);
    void restore();
    return () => { alive = false; channel.close(); window.removeEventListener("methodpal:unauthorized", clear); document.removeEventListener("visibilitychange", visible); };
  }, []);
  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => { clearAuthSession(); setSession(null); setError("登录已过期，请重新登录"); }, Math.max(0, session.expires_at * 1000 - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      if (mode !== 'login' && password !== confirmation) throw new Error('两次输入的密码不一致');
      if (mode === 'reset') {
        const result = await resetPassword({email, verification_id:verificationId,email_code:emailCode,new_password:password,...captcha});
        setMode('login');setNotice(result.message);setConfirmation('');
      } else {
        setSession(await authenticate(mode,username,password,{...captcha,...(mode === 'register' && email ? {email,verification_id:verificationId,email_code:emailCode}: {})}));
      }
      setPassword('');
    }
    catch (error) { setError(error instanceof Error ? error.message : "连接失败，请重试"); }
    finally { setBusy(false);setCaptchaKey(v=>v+1); }
  }
  async function sendCode() {
    setError('');setNotice('');setSending(true);
    try {
      const result = await sendEmailCode({email,purpose:mode === 'reset'?'reset':'register',...captcha});
      setVerificationId(result.verification_id);setEmailCode('');setCooldown(result.retry_after);setNotice(result.message);
    } catch(e: any) {setError(e.message);}
    finally {setSending(false);setCaptchaKey(v=>v+1);}
  }
  function switchMode(next: 'login'|'register'|'reset') {
    setMode(next);setError('');setNotice('');setPassword('');setConfirmation('');setEmail('');setEmailCode('');setVerificationId('');setCaptchaKey(v=>v+1);
  }
  if (loading) return <div className="h-screen grid place-items-center bg-background" role="status">正在恢复登录状态…</div>;
  if (session) return <div key={session.user.id} className="contents">{children}</div>;
  return (
    <main className="min-h-screen bg-background flex items-center justify-center p-6">
      <section className="w-full max-w-md rounded-2xl border border-border bg-card p-8 shadow-sm">
        <div className="text-primary font-bold text-xl mb-8">MethodPal</div>
        <h1 className="text-2xl font-semibold mb-2">{mode === "login" ? "登录您的工作区" : mode === 'reset' ? '找回密码' : "创建个人账户"}</h1>
        <p className="text-sm text-muted-foreground mb-6">保存研究项目，继续您的数据分析。</p>
        <form onSubmit={submit} className="space-y-4">
          {mode !== 'reset' && <label className="block text-sm">{mode === 'login' ? '用户名或已验证邮箱' : '用户名'}
            <input aria-label="用户名或邮箱" autoComplete="username" required minLength={3} maxLength={mode === 'register'?40:254} pattern={mode === 'register'?'[A-Za-z0-9_\\-]+':undefined} value={username} onChange={(e) => setUsername(e.target.value)} className="mt-2 w-full rounded-lg border border-border bg-background p-3" />
          </label>}
          {(mode === 'reset' || (mode === 'register' && config?.email_enabled)) && <>
            <label className="block text-sm">邮箱
              <input type="email" autoComplete="email" required={mode === 'reset' || config?.email_required} maxLength={254} value={email} onChange={e=>{setEmail(e.target.value);setVerificationId('');setEmailCode('');}} className="mt-2 w-full rounded-lg border border-border bg-background p-3"/>
            </label>
            <div className="flex items-center gap-2"><input aria-label="邮箱验证码" placeholder="6 位邮箱验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required={Boolean(email)} value={emailCode} onChange={e=>setEmailCode(e.target.value.replace(/\D/g,''))} className="min-w-0 flex-1 rounded-lg border border-border bg-background p-3"/>
              <button type="button" disabled={sending || busy || cooldown>0 || !config?.email_enabled || !email || captcha.captcha_answer.length !== 5} onClick={sendCode} className="text-xs text-primary disabled:opacity-50">{cooldown?`${cooldown} 秒后重发`:sending?'发送中…':'发送验证码'}</button>
            </div>
          </>}
          <label className="block text-sm">密码
            <input aria-label="密码" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={mode !== "login" ? 12 : 1} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} className="mt-2 w-full rounded-lg border border-border bg-background p-3" />
          </label>
          {mode !== 'login' && <label className="block text-sm">确认密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={confirmation} onChange={e=>setConfirmation(e.target.value)} className="mt-2 w-full rounded-lg border border-border bg-background p-3"/></label>}
          {(config?.captcha_required || mode === 'reset' || (mode === 'register' && config?.email_enabled)) && <CaptchaField key={captchaKey} required={config?.captcha_required || mode === 'reset'} onChange={setCaptcha}/>}
          {mode === "register" && <p className="text-xs text-muted-foreground">用户名使用字母、数字、下划线或短横线；密码至少 12 个字符。</p>}
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
          {notice && <p role="status" className="text-sm text-primary">{notice}</p>}
          {!config?.email_enabled && mode !== 'login' && <p className="text-xs text-amber-700">管理员尚未启用邮件服务。当前不能通过邮箱找回密码；旧账号需在服务启用后先绑定邮箱。</p>}
          <button disabled={busy || sending || !config || (mode === 'reset' && !config.email_enabled) || (mode === 'register' && (!config.registration_open || (config.email_required && !config.email_enabled)))} className="w-full rounded-lg bg-primary text-primary-foreground p-3 disabled:opacity-50">{busy ? "正在处理…" : mode === "login" ? "登录" : mode === 'reset' ? '验证并重置密码' : "注册并进入工作区"}</button>
        </form>
        <div className="mt-5 flex gap-5 text-sm text-primary"><button disabled={busy || sending} onClick={()=>switchMode(mode === 'login'?'register':'login')}>{mode === 'login'?'还没有账户？注册':'返回登录'}</button>{mode === 'login' && <button disabled={busy} onClick={()=>switchMode('reset')}>忘记密码？</button>}</div>
      </section>
    </main>
  );
}
