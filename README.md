# MethodPal 方法搭子

面向社会科学研究者的 AI 数据分析工作区。上传 CSV/Excel 或粘贴数据，确认研究目标与变量类型后，可以手动选择方法，也可以让 Agent 追问缺失信息、规划并执行分析，再生成图表、解释和报告。

## 当前功能

- 账户注册登录、基础图形验证码、独立项目与数据存储；配置 SMTP 后可验证/绑定邮箱、找回密码。
- 数据导入、虚拟表格编辑、类型/角色设置、数据质量诊断。
- 描述统计、相关与差异检验、回归、中介/调节分析，以及 XGBoost、随机森林、CatBoost 等模型。
- 工作台接待与项目研究助手，各账号、各项目对话历史独立；LLM 结合当前项目上下文与本地产品帮助回答。
- 自动分析支持缺项追问、状态恢复、明确修改后的重跑；真实配置变化不会被旧任务结果覆盖。
- 后台任务进度、取消、重试和历史；图表与 Markdown/DOCX/PDF 报告导出。

当前定位为单机、小规模多用户研究内测。没有独立 SHAP 工具、短信验证、向量知识库或分布式任务队列；回归和特征重要性不等于因果识别。完整能力与限制见 [后端与 Agent 指南](backend/README.md)。

## 项目结构

```text
src/                       React 前端、工作区与聊天界面
backend/main.py            FastAPI 应用入口
backend/app/api/           HTTP 接口
backend/app/services/      Agent、分析工具、账户、数据和导出服务
backend/app/knowledge/     产品帮助知识库
backend/tests/             后端回归测试
tests/                     前端回归测试
deploy/                    Docker、Nginx、systemd 与维护配置
```

文档仅保留当前总说明、[后端代码指南](backend/README.md)、[部署指南](DEPLOYMENT.md)及 [第三方版权说明](ATTRIBUTIONS.md)。

## 本地启动

已有完整 Python 3.9 环境可以继续使用，不必升级系统 Python。新部署环境使用独立 Python 环境与 [完整部署依赖](backend/requirements-deploy.txt)；当前服务器已在 Python 3.12 环境验证，Docker 使用 Python 3.11。不要把部署清单直接安装到已有系统环境。

在项目根目录，用你的实际 Python 解释器启动后端：

```powershell
# 仅首次创建本地配置；不要覆盖已有密钥。
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
python -m uvicorn main:app --app-dir backend --env-file backend/.env --host 127.0.0.1 --port 8000 --workers 1
```

另开终端，在同一项目根目录启动前端：

```powershell
npm ci
npm run dev -- --host 127.0.0.1
```

浏览器打开 `http://127.0.0.1:5173/`，注册账户后创建项目。没有默认账号或密码。前端默认代理 `/api` 到后端 `127.0.0.1:8000`，可用 [.env.example](.env.example) 配置开发代理。

在私有 `backend/.env` 配置 `DEEPSEEK_API_KEY` 后启用 LLM；不配置时仍可运行已安装的分析工具，但聊天会降级为帮助检索。真实邮箱服务需另配置 SMTP，不能仅靠复制模板启用。

## 测试

```powershell
npm run test:import
npm run test:navigation
npm run test:workspace
npm run build
python backend/tests/run_all.py
```

包含真实子进程任务、二进制导出等扩展验证：

```powershell
python backend/tests/test_workflow_hardening.py
```

扩展导出需要 Matplotlib、python-docx、ReportLab 和可用中文字体；依赖齐全不代表真实 LLM 网络或 SMTP 可用。测试范围及真实服务验证见 [后端指南](backend/README.md#8-测试与排查)。

## 部署与数据安全

公网部署见 [DEPLOYMENT.md](DEPLOYMENT.md)。生产使用 HTTPS、明确的来源/主机白名单、私有后端配置和独立持久数据目录。内置调度器只能使用一个 API worker，不能直接增加 Uvicorn workers 扩容。

`.gitignore` 排除运行密钥、私钥、数据库、真实上传数据、备份、日志、虚拟环境、依赖与构建产物。每次提交前仍应检查暂存内容：忽略规则不能识别源码内的秘密，也不能清除已有 Git 历史。不要将密钥放入前端 `VITE_*` 变量。

GitHub 保存源码，不是运行数据库备份，也不会自动更新服务器。真实项目数据单独备份；LLM 上下文可能包含用户消息、字段名称和统计摘要，敏感研究需要脱敏并评估第三方处理边界。

## 设计与版权

界面源自 [MethodPal Figma 原型](https://www.figma.com/design/1zn7avgbDxPQT5rvPiOauc/MethodPal-%E6%96%B9%E6%B3%95%E6%90%AD%E5%AD%90%E5%8E%9F%E5%9E%8B%E8%AE%BE%E8%AE%A1)。第三方组件与素材许可见 [ATTRIBUTIONS.md](ATTRIBUTIONS.md)，不应将所有素材默认视为同一开源许可。
