# MethodPal 部署指南

本文只说明当前版本的部署和维护方式。功能与代码职责见 [项目说明](README.md) 和 [后端 / Agent 指南](backend/README.md)，不包含逐次补丁记录。

## 运行架构与适用范围

前端生产构建由 Nginx 提供，`/api` 反向代理到 FastAPI；数据库为 SQLite WAL，上传文件按服务端用户 ID 隔离。账户、项目、分析、报告、聊天与任务记录使用服务端持久存储。

当前适合单机、小规模研究工作区。API 必须使用 **一个 Uvicorn worker**，后台任务调度使用单子进程；最多 32 个排队 / 运行任务、每账号最多 3 个。不要直接增加 API workers 或启动多个共享同一存储的实例。PostgreSQL、对象存储、分布式队列尚未实现，也不能通过配置数据库 URL 自动启用。

当前 IP 内测实例采用 Ubuntu 24.04、独立 Python 3.12 venv、systemd 和 Nginx。本机既有 Python 3.9 环境不需要随服务器升级。仓库同时提供 Python 3.11 Docker 配置，但容器方案仍需在实际目标服务器构建并完成业务验证。

## 配置与数据

配置模板：

- 本地：[backend/.env.example](backend/.env.example)
- 原生部署：[server.env.example](deploy/native/server.env.example)、[email.env.example](deploy/native/email.env.example)
- Docker：[production.env.example](deploy/production.env.example)

真实配置必须放在私有环境文件或服务器环境中，不提交 Git。模型密钥只用于后端，不能写进 `VITE_*` 变量或前端构建产物。

| 配置 | 用途 |
| --- | --- |
| `METHODPAL_ENV` | 公网部署使用 `production` |
| `METHODPAL_ORIGINS` | 精确的 HTTPS 网站 Origin，不使用通配符 |
| `METHODPAL_ALLOWED_HOSTS` | 允许的域名 / IP |
| `METHODPAL_DATA_ROOT` | 数据和上传文件的持久目录 |
| `METHODPAL_DATABASE_PATH` | SQLite 路径，默认位于数据目录 |
| `METHODPAL_REGISTRATION` | 是否允许新用户注册 |
| `METHODPAL_CAPTCHA_REQUIRED` | 是否要求图形验证码 |
| `METHODPAL_EMAIL_REQUIRED` | 是否强制邮箱验证注册；无 SMTP 时强制启用会阻止注册 |
| `METHODPAL_VERIFICATION_SECRET` | 服务端验证码摘要密钥，使用私有随机值 |
| `DEEPSEEK_API_KEY` | LLM 对话与解释；缺失 / 故障时明确降级 |
| SMTP 与账户配额 | 完整字段见上述模板和后端配置实现 |

生产会话 Cookie 使用 Secure，因此需要 HTTPS。不要为了登录方便关闭生产安全策略。使用 IP 自签名证书只能用于确认身份后的受控测试，不等于正式受信任 HTTPS。

原生部署中，API 仅监听 loopback；Nginx 覆盖 `X-Real-IP`，应用仅信任生产模式下来自 loopback 的该头。不要把 8000 端口开放到公网。其他代理拓扑需要单独审核可信来源。

## 原生部署

建议目录：

```text
/opt/methodpal/
  releases/<release>/     # 独立版本的 backend、dist、部署文件
  current -> releases/... # 当前代码
  venv/                   # 独立 Python 环境
/var/lib/methodpal/        # 数据库、上传文件、运行缓存
/etc/methodpal/
  server.env              # 私有服务配置
  llm.env                 # 可选私有模型配置
  email.env               # 可选私有邮件配置
```

1. 在源码根目录执行 `npm ci` 和 `npm run build`，准备包含 `backend/`、`dist/` 和必要部署文件的版本目录，不打包密钥、数据库、用户文件或备份。
2. 在服务器创建独立 Python 环境，安装 [requirements-deploy.txt](backend/requirements-deploy.txt)；准备 Nginx、系统运行依赖和可读的中文字体。不要把部署依赖装进本机已有的系统 Python。
3. 使用专用服务用户 `methodpal`，设置代码读取及数据目录写入权限。参考 [methodpal.service](deploy/native/methodpal.service) 和 [Nginx 配置](deploy/native/nginx-ip-test.conf)，调整实际路径、地址及证书。
4. 配置 HTTPS、Origin / Host、数据目录和账户安全选项，按需加入 LLM / SMTP。启动后检查健康接口，再用独立测试账户验证完整业务。

[install-ip-test.sh](deploy/native/install-ip-test.sh) 是首次安装参考脚本，包含内测实例的固定地址和目录，必须先检查并替换为目标环境。不要把它当作已有服务器的无条件更新脚本。

只读运行检查：

```sh
sudo systemctl is-active methodpal nginx
curl -fsS http://127.0.0.1:8000/api/health
```

排查工具：

- [verify-runtime.py](deploy/native/verify-runtime.py)：只读数据库摘要与完整性检查；使用 `--llm` 验证真实 LLM 会产生 API 调用费用，脚本输出不能代替 TLS 和监听地址检查。
- [diagnose-jobs.py](deploy/native/diagnose-jobs.py)：查看任务状态摘要。
- [verify-production-idle.py](deploy/native/verify-production-idle.py)：发布前检查是否有活动任务，避免打断分析。

## Docker 部署

将配置模板复制为私有的 `.env.production` 并填写实际环境，在具备 Docker / Compose 的服务器运行：

```sh
docker compose --env-file .env.production up --build -d
```

前端绑定宿主机 `127.0.0.1:8080`，API 不直接发布端口；需要宿主机 HTTPS 反向代理转发到该地址并保留外部 Host。数据通过命名卷持久化。停止服务时不要使用 `docker compose down -v`，该选项会移除数据卷。

构建成功不代表部署完成：还需验证 Cookie、反向代理、中文图表、二进制报告导出、持久化和重启后的恢复。

## 启用邮箱验证与找回密码

1. 准备支持认证的 SMTP 发信服务，获取应用专用密码 / 授权码，不使用日常登录密码，不在聊天中发送。域名发信按服务商要求配置 SPF / DKIM / DMARC。
2. 参照 [email.env.example](deploy/native/email.env.example) 填写服务器 `/etc/methodpal/email.env`。建议所有者为 `root:methodpal`、权限 640，父目录权限 750。模板占位值不能视为已启用。
3. 支持 SMTP-over-TLS 465 或 STARTTLS 587，始终校验证书，不支持明文 SMTP。配置后重启后端。
4. 用自己的测试收件箱验证注册、旧账户绑定和找回密码的真实收信；检查垃圾邮件和服务商限制。

SMTP 未配置时，界面会提示邮件功能未启用，绑定 / 找回发送接口返回错误，不伪造发送成功。旧账户保留原 ID 和数据，需要登录后在“账户与安全”验证绑定邮箱。目前不支持短信验证或更换已绑定邮箱。

固定的私有 `METHODPAL_VERIFICATION_SECRET` 可让未过期验证码跨服务重启验证；不配置时重启会使未完成的验证码失效，不影响已有登录会话。

## 更新、备份与回滚

更新 GitHub 源码不会自动部署服务器。发布前先确认任务空闲，保留旧代码版本，并对数据库与上传文件做一致性备份。

- SQLite 在线备份应使用 backup API，并配合一致的文件备份；或停止写入后完整备份数据目录。只复制在线主 `.sqlite3` 文件可能遗漏 WAL 中的已提交数据。
- 新版本在独立目录安装 / 构建 / 测试，通过后切换 `current` 并重启。不要覆盖持久数据目录。
- 回滚代码前核对数据库 schema 兼容性；旧程序不一定能读取升级后的数据库。
- 不要为回滚代码直接用旧数据库覆盖当前数据库，那会丢失升级后的新用户和业务数据。
- 备份包含用户数据，应限制访问，并定期验证恢复。Git 不能代替数据库与文件备份。

旧演示数据需要管理员离线指定归属，可参考 [import_legacy.py](backend/scripts/import_legacy.py)。默认只预览，明确 `--apply` 才导入；先停止写入和备份，不通过网页认领旧数据。

## 验证与正式上线前检查

回归命令见 [项目测试说明](README.md#测试) 和 [后端测试指南](backend/README.md#8-测试与排查)。测试使用隔离的临时存储，不能连接真实用户项目。

[smoke-ip-test.py](deploy/native/smoke-ip-test.py) 是基础 HTTP 冒烟工具，旧注册流程未自动处理当前的图形验证码；不要直接用于启用验证码的正式实例，也不要为运行测试关闭生产验证码。使用隔离测试实例或浏览器完成注册安全流程。真实 LLM 测试和邮件收信测试需单独验证，不可用模拟测试代替。

正式开放前至少确认：受信任 HTTPS、邮件真实送达、账户 / 文件隔离、分析与报告导出、磁盘配额、备份恢复、依赖安全与目标负载下的压力测试。大数据 / 多用户并发能力需实际测量，当前队列与配额不代表已支持大规模服务。

LLM 会接收聊天文本和有限项目摘要，不自动发送数据行；这些文本仍可能包含敏感信息。网站需要明确告知外部模型使用范围，并提示用户脱敏。共享产品帮助库不索引用户上传数据。
