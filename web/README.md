# SysDialogue Web

SysDialogue 的单页 Web 控制台。界面覆盖自然语言任务、SSH 远程目标、工具目录、内置 workflow、审批、审计导出、终端执行和运行参数设置。

## Stack

- React + Vite + TypeScript
- Tailwind CSS
- Radix/shadcn 风格组件
- lucide-react icons
- GSAP + `@gsap/react`

## Scripts

```bash
npm install
npm run dev
npm run check
npm run build
npm run preview
```

真实 SSH 需要同时启动后端桥接服务。浏览器不会直接 SSH 到服务器，真实链路是：

```text
React UI -> FastAPI Web API -> SysDialogue runtime -> Paramiko SSH / LocalExecutor
```

启动后端：

```bash
python -m sysdialogue.app.web_api
```

默认 Web API 地址是 `http://127.0.0.1:8000/api`，前端顶部提示条和设置页都可以热更新该地址。
如果 8000 端口已被占用，可以改用：

```bash
SYSDIALOGUE_WEB_PORT=8010 python -m sysdialogue.app.web_api
```

PowerShell：

```powershell
$env:SYSDIALOGUE_WEB_PORT="8010"
python -m sysdialogue.app.web_api
```

## API

可以在前端顶部提示条或设置页直接输入 SysDialogue Web API URL，点击“应用热更新”后会立即重建 API client、刷新 `/overview`，不需要改文件、重启前端或重新构建。`VITE_SYSDIALOGUE_API_URL` 只作为首次打开页面时的可选默认值。

```dotenv
VITE_SYSDIALOGUE_API_URL=http://127.0.0.1:8000/api
```

前端 API 边界集中在 `src/lib/api.ts`，UI 状态类型集中在 `src/lib/types.ts`。

运行时配置保存在浏览器 `localStorage`，包括 API URL、模型名、OpenAI-compatible Base URL、最大迭代次数、workflow 目录、安全档位和事件流开关。SSH 密码类字段只保存在当前页面状态中，连接时随请求提交给后端，不写入 `localStorage`。

SSH 连接在“服务器”页热更新：编辑 host、port、user、key/password/sudo password 后直接点击连接，前端会把当前运行时配置与 SSH 表单一起提交给真实后端。

服务器页的“控制链路”会显示当前实际通道：Web API、后端执行器和目标。SSH 模式显示 `Paramiko SSH / 远程 SSH`，Local 模式显示 `LocalExecutor / 本机`。“SSH 凭据状态”只展示是否已填写 key/password/sudo password，不展示密文；password 和 sudo password 不写入 `localStorage`，只在本次页面状态中随连接请求提交。

后端桥接服务提供以下接口：

- `GET /overview`
- `POST /connections`
- `POST /tasks`
- `POST /approvals/:id`
- `POST /terminal/exec`
- `POST /tools/run`
- `POST /workflows/run`
- `GET /audit/export?format=jsonl|replay`
- `GET /release/acceptance`
- `GET /release/acceptance-runner`
- `POST /release/readiness`
- `POST /release/acceptance-bundle`
- `POST /release/mutation-drill`

## Verification

交付前至少运行：

```bash
npm run check
```

后端桥接相关文件可用：

```bash
python -m py_compile sysdialogue/app/web_api.py sysdialogue/agent/release_readiness.py
```

浏览器烟测建议覆盖：

- 服务器页能看到“控制链路”和“SSH 凭据状态”。
- SSH 表单填写 password/sudo password 后，浏览器 `localStorage` 不包含密文。
- 命令面板能搜索到后端 `/overview` 返回的靠后工具，例如 `manage_firewall`，并显示对应风险标记。
- 390px 左右移动宽度下，服务器页、命令面板和底部导航没有横向溢出。
