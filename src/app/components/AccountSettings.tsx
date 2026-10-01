import {useEffect, useState} from 'react';
import {getAuthConfig, restoreSession, listSessions, revokeOtherSessions, sendEmailCode, bindEmail, changePassword, getUserStorage, type AuthConfig} from '../lib/api';
import {CaptchaField, type CaptchaProof} from './CaptchaField';

export function AccountSettings({onClose}: {onClose:()=>void}) {
  const [config,setConfig] = useState<AuthConfig|null>(null);
  const [user,setUser] = useState<any>(null);
  const [sessions,setSessions] = useState<any[]>([]);
  const [storage,setStorage] = useState<any>(null);
  const [email,setEmail] = useState('');
  const [code,setCode] = useState('');
  const [verificationId,setVerificationId] = useState('');
  const [currentPassword,setCurrentPassword] = useState('');
  const [newPassword,setNewPassword] = useState('');
  const [confirmation,setConfirmation] = useState('');
  const [captcha,setCaptcha] = useState<CaptchaProof>({captcha_id:'',captcha_answer:''});
  const [captchaKey,setCaptchaKey] = useState(0);
  const [cooldown,setCooldown] = useState(0);
  const [busy,setBusy] = useState(false);
  const [message,setMessage] = useState('');
  const [error,setError] = useState('');
  useEffect(()=>{
    let alive = true;
    Promise.all([getAuthConfig(),restoreSession(),listSessions(),getUserStorage()]).then(([c,a,s,u])=>{
      if (!alive) return;setConfig(c);setUser(a.user);setSessions(s.items);setStorage(u);
    }).catch(e=>{if (alive) setError(e.message);});
    return ()=>{alive=false;};
  },[]);
  useEffect(()=>{if(!cooldown)return;const timer=setTimeout(()=>setCooldown(v=>Math.max(0,v-1)),1000);return()=>clearTimeout(timer);},[cooldown]);
  async function perform(action:()=>Promise<void>) {
    setBusy(true);setError('');setMessage('');
    try {await action();} catch(e:any){setError(e.message);} finally{setBusy(false);}
  }
  const input = 'mt-2 w-full rounded-lg border border-border bg-background p-3';
  return <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4">
    <section role="dialog" aria-modal="true" aria-labelledby="account-title" className="bg-card rounded-2xl border border-border shadow-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6 space-y-6">
      <div className="flex justify-between items-center"><h2 id="account-title" className="text-xl font-semibold">账户与安全</h2><button onClick={onClose} disabled={busy} aria-label="关闭账户设置" className="text-sm text-primary">关闭</button></div>
      <div className="text-sm">{user?.username || '加载中…'} · {user?.email_verified ? `已验证邮箱：${user.email}` : '尚未绑定邮箱，忘记密码时无法自助找回'}</div>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}{message && <p role="status" className="text-sm text-primary">{message}</p>}
      {!user?.email_verified && <form className="space-y-3 border-t border-border pt-4" onSubmit={e=>{e.preventDefault();void perform(async()=>{
        const result=await bindEmail({email,verification_id:verificationId,email_code:code,current_password:currentPassword});
        setUser((await restoreSession()).user);setCurrentPassword('');setCode('');setMessage(result.message);
      });}}>
        <h3 className="font-semibold">绑定找回邮箱</h3>
        {!config?.email_enabled && <p className="text-sm text-amber-700">邮件服务尚未启用。管理员完成配置后，可在此绑定邮箱。</p>}
        <label className="block text-sm">邮箱<input type="email" required maxLength={254} value={email} onChange={e=>{setEmail(e.target.value);setVerificationId('');setCode('');}} className={input}/></label>
        <label className="block text-sm">当前密码<input type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={e=>setCurrentPassword(e.target.value)} className={input}/></label>
        {config?.email_enabled && <CaptchaField key={captchaKey} required={false} onChange={setCaptcha}/>}
        <div className="flex items-center gap-3"><input aria-label="邮箱验证码" required placeholder="6 位邮箱验证码" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))} className={input}/>
          <button type="button" disabled={busy || cooldown>0 || !email || !config?.email_enabled || captcha.captcha_answer.length!==5} className="text-xs text-primary shrink-0 disabled:opacity-50" onClick={()=>void perform(async()=>{
            try{const result=await sendEmailCode({email,purpose:'bind',...captcha});setVerificationId(result.verification_id);setCode('');setCooldown(result.retry_after);setMessage(result.message);}
            finally{setCaptchaKey(v=>v+1);}
          })}>{cooldown?`${cooldown} 秒后重发`:'发送验证码'}</button>
        </div>
        <button disabled={busy || !config?.email_enabled || !verificationId} className="bg-primary text-primary-foreground rounded-lg px-4 py-2 text-sm disabled:opacity-50">验证并绑定</button>
      </form>}
      <form className="space-y-3 border-t border-border pt-4" onSubmit={e=>{e.preventDefault();void perform(async()=>{
        if(newPassword!==confirmation)throw new Error('两次输入的新密码不一致');
        if(!window.confirm('修改密码后，所有设备（包括当前设备）将退出登录。继续吗？'))return;
        await changePassword({current_password:currentPassword,new_password:newPassword});
      });}}>
        <h3 className="font-semibold">修改密码</h3>
        <label className="block text-sm">当前密码<input type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={e=>setCurrentPassword(e.target.value)} className={input}/></label>
        <label className="block text-sm">新密码（至少 12 个字符）<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={newPassword} onChange={e=>setNewPassword(e.target.value)} className={input}/></label>
        <label className="block text-sm">确认新密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={confirmation} onChange={e=>setConfirmation(e.target.value)} className={input}/></label>
        <button disabled={busy || !user} className="bg-primary text-primary-foreground rounded-lg px-4 py-2 text-sm disabled:opacity-50">修改密码并重新登录</button>
      </form>
      <div className="border-t border-border pt-4 space-y-2"><h3 className="font-semibold">登录会话</h3>
        {sessions.map(s=><p key={s.session_id} className="text-xs text-muted-foreground">{s.current?'当前会话':'其他会话'} · {s.created_at?new Date(s.created_at*1000).toLocaleString():'旧版会话'} · {new Date(s.expires_at*1000).toLocaleString()} 到期</p>)}
        <button disabled={busy || sessions.every(s=>s.current)} className="text-sm text-primary disabled:opacity-50" onClick={()=>void perform(async()=>{
          await revokeOtherSessions();setSessions((await listSessions()).items);setMessage('其他登录会话已退出，当前设备保持登录。');
        })}>退出其他所有设备</button>
      </div>
      {storage && <div className="border-t border-border pt-4 text-sm space-y-1"><h3 className="font-semibold">个人存储空间</h3>
        <p>{(storage.used_bytes/1024/1024).toFixed(1)} / {(storage.quota_bytes/1024/1024).toFixed(0)} MiB · 包含原始文件、项目和分析历史</p>
        <p className="text-xs text-muted-foreground">项目 {storage.project_count}/{storage.project_limit} · 数据集 {storage.dataset_count}/{storage.dataset_limit}。不同账号之间不能访问彼此的数据。</p>
      </div>}
    </section>
  </div>;
}
