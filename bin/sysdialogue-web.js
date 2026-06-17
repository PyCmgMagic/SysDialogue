#!/usr/bin/env node
// bin/sysdialogue-web.js — sysdialogue Web 控制台启动器
// 启动 FastAPI 后端并同时提供 Web 前端静态文件
// 默认端口：8000，可通过 SYSDIALOGUE_WEB_PORT 环境变量修改

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const isWindows = os.platform() === "win32";
const PACKAGE_ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(PACKAGE_ROOT, ".venv");
const MARKER = path.join(VENV_DIR, ".sysdialogue-installed");

const red = (s) => `\x1b[31m${s}\x1b[0m`;
const cyan = (s) => `\x1b[36m${s}\x1b[0m`;
const green = (s) => `\x1b[32m${s}\x1b[0m`;

function log(msg) {
  console.log(`  ${cyan("[sysdialogue-web]")}`, msg);
}

function error(msg) {
  console.error(`  ${red("[sysdialogue-web]")}`, msg);
}

// 检查 venv 是否存在
if (!fs.existsSync(MARKER)) {
  error("Python 环境未安装！请重新运行：npm install -g sysdialogue");
  process.exit(1);
}

const pythonBin = isWindows
  ? path.join(VENV_DIR, "Scripts", "python.exe")
  : path.join(VENV_DIR, "bin", "python");

if (!fs.existsSync(pythonBin)) {
  error(`Python 可执行文件未找到：${pythonBin}`);
  error("请重新安装：npm install -g sysdialogue");
  process.exit(1);
}

const port = process.env.SYSDIALOGUE_WEB_PORT || "8000";
const host = process.env.SYSDIALOGUE_WEB_HOST || "127.0.0.1";

// 设置 Web 前端 dist 路径（供 FastAPI 挂载静态文件）
const webDistPath = path.join(PACKAGE_ROOT, "web", "dist");
const env = {
  ...process.env,
  SYSDIALOGUE_WEB_HOST: host,
  SYSDIALOGUE_WEB_PORT: port,
  SYSDIALOGUE_WEB_DIST: fs.existsSync(webDistPath) ? webDistPath : "",
};

const args = ["-m", "sysdialogue.app.web_api"];

log(`启动 Web 控制台...`);
log(`地址：${green(`http://${host}:${port}`)}`);
log("按 Ctrl+C 停止\n");

const child = spawn(pythonBin, args, {
  stdio: "inherit",
  cwd: process.cwd(),
  env,
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});

child.on("error", (err) => {
  error(`启动失败：${err.message}`);
  process.exit(1);
});
