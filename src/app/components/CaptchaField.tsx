import {useEffect, useState} from 'react';
import {getCaptcha} from '../lib/api';

export type CaptchaProof = {captcha_id: string; captcha_answer: string};
export function CaptchaField({onChange, required = true}: {onChange: (proof: CaptchaProof) => void; required?: boolean}) {
  const [challenge, setChallenge] = useState<{captcha_id:string;image:string}|null>(null);
  const [answer, setAnswer] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true; setChallenge(null); setAnswer(''); setError('');
    onChange({captcha_id:'',captcha_answer:''});
    getCaptcha().then(value => {if (alive) {setChallenge(value); onChange({captcha_id:value.captcha_id,captcha_answer:''});}})
      .catch(e => {if (alive) setError(e.message);});
    return () => {alive = false;};
  }, [refresh]);
  return <div className="space-y-2">
    <label className="block text-sm" htmlFor="captcha-answer">图形验证码（不区分大小写）</label>
    <div className="flex items-center gap-2 flex-wrap">
      {challenge ? <img src={challenge.image} width={190} height={62} alt="请输入图中显示的五个字符" className="rounded-lg border border-border"/> : <span className="text-xs">{error || '正在加载验证码…'}</span>}
      <button type="button" onClick={() => setRefresh(v=>v+1)} className="text-xs text-primary">换一张</button>
    </div>
    <input id="captcha-answer" aria-label="图形验证码" autoComplete="off" required={required} maxLength={5} value={answer} onChange={e => {const next=e.target.value.toUpperCase();setAnswer(next);onChange({captcha_id:challenge?.captcha_id || '',captcha_answer:next});}} className="w-full rounded-lg border border-border bg-background p-3"/>
  </div>;
}
