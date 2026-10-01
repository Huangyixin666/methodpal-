#!/usr/bin/env bash
# Run as root after uploading a release containing backend/, dist/, deploy/.
set -euo pipefail
release=$(realpath "${1:?Provide the uploaded release directory}")
case "$release" in /opt/methodpal/releases/*) ;; *) echo 'Invalid release path'; exit 1;; esac
test -f "$release/backend/main.py"
test -f "$release/dist/index.html"
id methodpal >/dev/null 2>&1 || useradd --system --home-dir /var/lib/methodpal --shell /usr/sbin/nologin methodpal
install -d -o methodpal -g methodpal -m 700 /var/lib/methodpal
install -d -o methodpal -g methodpal -m 700 /var/lib/methodpal/.cache /var/lib/methodpal/.cache/matplotlib
install -d -o root -g methodpal -m 750 /etc/methodpal
install -d -o root -g root -m 700 /etc/methodpal/tls
if [ ! -f /etc/methodpal/server.env ]; then
    install -o root -g methodpal -m 640 "$release/deploy/native/server.env.example" /etc/methodpal/server.env
fi
if [ ! -f /etc/methodpal/tls/server.key ]; then
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 90 \
        -keyout /etc/methodpal/tls/server.key -out /etc/methodpal/tls/server.crt \
        -subj '/CN=139.199.4.56' \
        -addext 'subjectAltName=IP:139.199.4.56,IP:127.0.0.1' >/dev/null 2>&1
    chmod 600 /etc/methodpal/tls/server.key
    chmod 644 /etc/methodpal/tls/server.crt
fi
python3 -m venv /opt/methodpal/venv
/opt/methodpal/venv/bin/pip install --disable-pip-version-check \
    --index-url https://mirrors.cloud.tencent.com/pypi/simple \
    -r "$release/backend/requirements-deploy.txt"
ln -sfn "$release" /opt/methodpal/current
chmod -R a+rX "$release"
install -m 644 "$release/deploy/native/methodpal.service" /etc/systemd/system/methodpal.service
install -m 644 "$release/deploy/native/nginx-ip-test.conf" /etc/nginx/sites-available/methodpal
ln -sfn /etc/nginx/sites-available/methodpal /etc/nginx/sites-enabled/methodpal
if [ -L /etc/nginx/sites-enabled/default ]; then unlink /etc/nginx/sites-enabled/default; fi
nginx -t
systemctl daemon-reload
systemctl enable methodpal nginx
systemctl restart methodpal
systemctl restart nginx
echo 'Started. Check: systemctl status methodpal nginx'
