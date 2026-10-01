"""Short-lived single-use challenges. Never expose verification answers or SMTP secrets."""
import base64
import hashlib
import hmac
import io
import ipaddress
import os
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from fastapi import HTTPException
from app.services.database import connect

CHALLENGE_SECRET = os.getenv('METHODPAL_VERIFICATION_SECRET') or secrets.token_urlsafe(48)

def challenge_hash(value):
    return hmac.new(CHALLENGE_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def client_ip(request):
    peer = request.client.host if request.client else 'unknown'
    # Nginx must OVERWRITE this header; never accept forwarded headers from arbitrary peers.
    if os.getenv('METHODPAL_ENV') == 'production' and peer in ('127.0.0.1', '::1'):
        try:
            return str(ipaddress.ip_address(request.headers.get('x-real-ip', '')))
        except ValueError:
            pass
    return peer

def limited(bucket, limit, seconds):
    now = int(time.time())
    key = digest('account:' + bucket)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO auth_attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET '
            'attempts=CASE WHEN started<=? THEN 1 ELSE attempts+1 END, '
            'started=CASE WHEN started<=? THEN excluded.started ELSE started END', (key, now, now-seconds, now-seconds))
        row = db.execute('SELECT attempts FROM auth_attempts WHERE bucket=?', (key,)).fetchone()
    if row[0] > limit:
        raise HTTPException(429, '操作过于频繁，请稍后再试')

def captcha_required():
    return os.getenv('METHODPAL_CAPTCHA_REQUIRED', '0') == '1'

def mail_enabled():
    return all(os.getenv(key, '').strip() for key in ('METHODPAL_SMTP_HOST', 'METHODPAL_SMTP_USER', 'METHODPAL_SMTP_PASSWORD', 'METHODPAL_SMTP_FROM'))

def email_required():
    return os.getenv('METHODPAL_EMAIL_REQUIRED', '1' if mail_enabled() else '0') == '1'

def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", value):
        raise HTTPException(422, '请输入有效的邮箱地址')
    local = value.split('@')[0]
    if local.startswith('.') or local.endswith('.') or '..' in local:
        raise HTTPException(422, '请输入有效的邮箱地址')
    return value

def create_captcha(request):
    from PIL import Image, ImageDraw, ImageFont
    limited('captcha:' + client_ip(request), 40, 60)
    answer = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
    cid = secrets.token_urlsafe(24)
    now = int(time.time())
    with connect() as db:
        db.execute('DELETE FROM auth_captchas WHERE expires_at<?', (now-900,))
        db.execute('INSERT INTO auth_captchas VALUES (?,?,?,?,0)', (cid, challenge_hash(cid+':'+answer), digest(client_ip(request)), now+180))
    image = Image.new('RGB', (190, 62), '#f4f3fc')
    draw = ImageDraw.Draw(image)
    for _ in range(110):
        draw.point((secrets.randbelow(190), secrets.randbelow(62)), fill='#ada7ce')
    for _ in range(6):
        draw.line((secrets.randbelow(190), secrets.randbelow(62), secrets.randbelow(190), secrets.randbelow(62)), fill='#c6c1de', width=1)
    path = next((p for p in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf') if Path(p).is_file()), None)
    font = ImageFont.truetype(path, 30) if path else ImageFont.load_default()
    for i, character in enumerate(answer):
        draw.text((12+i*33, 10+secrets.randbelow(8)), character, fill='#5142a6', font=font)
    data = io.BytesIO()
    image.save(data, format='PNG')
    return {'captcha_id':cid, 'image':'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode(), 'expires_in':180}

def check_captcha(request, cid, answer, required=True):
    if not required:
        return
    valid = False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM auth_captchas WHERE id=?', (cid or '',)).fetchone()
        if row and not row['consumed']:
            db.execute('UPDATE auth_captchas SET consumed=1 WHERE id=?', (cid,))
            valid = (row['expires_at'] > int(time.time()) and row['ip_hash'] == digest(client_ip(request))
                and hmac.compare_digest(row['answer_hash'], challenge_hash((cid or '')+':'+(answer or '').strip().upper())))
    if not valid:
        raise HTTPException(400, '图形验证码错误、过期或已使用，请刷新后重试')

def send_email(email, code, purpose):
    if not mail_enabled():
        raise HTTPException(503, '邮箱发送服务尚未配置，请联系网站管理员')
    message = EmailMessage()
    message['Subject'] = 'MethodPal 邮箱验证码'
    message['From'] = normalize_email(os.environ['METHODPAL_SMTP_FROM'])
    message['To'] = email
    label = {'register':'注册账号', 'reset':'重置密码', 'bind':'绑定邮箱'}[purpose]
    message.set_content('你正在进行 MethodPal '+label+'。\n\n验证码：'+code+'\n10 分钟内有效，请勿向任何人透露。\n如果不是你本人操作，请忽略此邮件。')
    mode = os.getenv('METHODPAL_SMTP_TLS', 'ssl')
    host = os.environ['METHODPAL_SMTP_HOST'].strip()
    port = int(os.getenv('METHODPAL_SMTP_PORT', '465' if mode == 'ssl' else '587'))
    context = ssl.create_default_context()
    try:
        if mode == 'ssl':
            smtp = smtplib.SMTP_SSL(host, port, timeout=12, context=context)
        elif mode == 'starttls':
            smtp = smtplib.SMTP(host, port, timeout=12)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        else:
            raise ValueError('TLS is mandatory')
        with smtp:
            smtp.login(os.environ['METHODPAL_SMTP_USER'], os.environ['METHODPAL_SMTP_PASSWORD'])
            smtp.send_message(message)
    except Exception:
        raise HTTPException(503, '验证码邮件发送失败，请稍后重试或联系管理员')

def issue_email_code(request, email, purpose, user_id=None):
    if not mail_enabled():
        raise HTTPException(503, '邮箱发送服务尚未配置，请联系网站管理员')
    limited('mail-ip:'+client_ip(request), 20, 3600)
    limited('mail-global', 200, 86400)
    email = normalize_email(email)
    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    cid, now = secrets.token_urlsafe(24), int(time.time())
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM email_challenges WHERE created_at<?', (now-86400,))
        recent = db.execute('SELECT count(*), max(created_at) FROM email_challenges WHERE email=? AND created_at>?', (email, now-86400)).fetchone()
        if recent[0] >= 10 or (recent[1] is not None and recent[1] > now-60):
            raise HTTPException(429, '同一邮箱请间隔 60 秒再发送，每天最多 10 次')
        db.execute('UPDATE email_challenges SET consumed=1 WHERE email=? AND purpose=? AND user_id IS ?', (email, purpose, user_id))
        db.execute('INSERT INTO email_challenges VALUES (?,?,?,?,?,?,?,0,0,?)', (cid,email,purpose,user_id,challenge_hash(cid+':'+code),now,now+600,'pending'))
    try:
        send_email(email, code, purpose)
    except Exception:
        with connect() as db:
            db.execute("UPDATE email_challenges SET status='failed', consumed=1 WHERE id=?", (cid,))
        raise
    with connect() as db:
        db.execute("UPDATE email_challenges SET status='sent' WHERE id=?", (cid,))
    # Same response whether or not an account owns this email; only the mailbox owner sees the code.
    return {'ok':True, 'verification_id':cid, 'retry_after':60, 'message':'验证码已发送，请检查收件箱和垃圾邮件。'}

def valid_code(db, cid, email, purpose, code, user_id=None, count_attempt=True):
    row = db.execute('SELECT * FROM email_challenges WHERE id=?', (cid or '',)).fetchone()
    if not row or row['email'] != email or row['purpose'] != purpose or row['user_id'] != user_id or row['status'] != 'sent' or row['consumed'] or (count_attempt and row['attempts'] >= 5) or row['expires_at'] <= int(time.time()):
        return False
    if count_attempt:
        db.execute('UPDATE email_challenges SET attempts=attempts+1 WHERE id=?', (cid,))
    return hmac.compare_digest(row['code_hash'], challenge_hash(cid+':'+code))

def verify_code(cid, email, purpose, code, user_id=None):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        valid = valid_code(db, cid, email, purpose, code, user_id)
    if not valid:
        raise HTTPException(400, '邮箱验证码错误、过期或已使用，请重新获取')
