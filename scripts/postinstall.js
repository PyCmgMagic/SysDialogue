#!/usr/bin/env node
// postinstall.js — 自动创建 Python venv 并安装 sysdialogue
// 在 npm install 时自动运行

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const isWindows = os.platform() === "win32";
const PACKAGE_ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(PACKAGE_ROOT, ".venv");
const MARKER = path.join(VENV_DIR, ".sysdialogue-installed");

// 颜色输出
const green = (s) => `\x1b[32m${s}\x1b[0m`;
const red = (s) => `\x1b[31m${s}\x1b[0m`;
const yellow = (s) => `\x1b[33m${s}\x1b[0m`;
const cyan = (s) => `\x1b[36m${s}\x1b[0m`;

function log(msg) {
  console.log(`  ${cyan("[sysdialogue]")}`, msg);
}

function warn(msg) {
  console.warn(`  ${yellow("[sysdialogue]")}`, msg);
}

function error(msg) {
  console.error(`  ${red("[sysdialogue]")}`, msg);
}

function findPython() {
  const candidates = isWindows
    ? ["python3", "python", "py -3.11", "py -3.12", "py -3.13", "py"]
    : ["python3.13", "python3.12", "python3.11", "python3"];

  for (const cmd of candidates) {
    try {
      const version = execSync(`${cmd} --version 2>&1`, { encoding: "utf-8" }).trim();
      const match = version.match(/Python (\d+)\.(\d+)/);
      if (match) {
        const major = parseInt(match[1], 10);
        const minor = parseInt(match[2], 10);
        if (major >= 3 && minor >= 11) {
          return cmd;
        }
      }
    } catch {
      // 继续尝试下一个
    }
  }
  return null;
}

function run(cmd, opts = {}) {
  log(`> ${cmd}`);
  execSync(cmd, {
    stdio: "inherit",
    cwd: PACKAGE_ROOT,
    ...opts,
  });
}

function main() {
  console.log();
  log("正在安装 SysDialogue Python 环境...\n");

  // 1. 查找 Python
  const python = findPython();
  if (!python) {
    error("未找到 Python 3.11+！请先安装 Python 3.11 或更高版本。");
    error("下载地址：https://www.python.org/downloads/");
    process.exit(1);
  }

  const version = execSync(`${python} --version 2>&1`, { encoding: "utf-8" }).trim();
  log(`找到 ${version} (${python})`);

  // 2. 创建 venv（如果不存在）
  if (!fs.existsSync(VENV_DIR)) {
    log("创建 Python 虚拟环境...");
    run(`${python} -m venv "${VENV_DIR}"`);
  } else {
    log("虚拟环境已存在，跳过创建。");
  }

  // 3. 确定 pip 路径
  const pip = isWindows
    ? `"${path.join(VENV_DIR, "Scripts", "pip")}"`
    : `"${path.join(VENV_DIR, "bin", "pip")}"`;
  const pythonVenv = isWindows
    ? `"${path.join(VENV_DIR, "Scripts", "python")}"`
    : `"${path.join(VENV_DIR, "bin", "python")}"`;

  // 4. 升级 pip
  log("升级 pip...");
  run(`${pythonVenv} -m pip install --upgrade pip setuptools wheel`);

  // 5. 安装项目
  log("安装 sysdialogue 及其依赖...");
  run(`${pip} install "${PACKAGE_ROOT}"`);

  // 6. 标记安装完成
  fs.writeFileSync(MARKER, new Date().toISOString());

  console.log();
  console.log(green("  ╔══════════════════════════════════════════════════════════╗"));
  console.log(green("  ║                                                          ║"));
  console.log(green("  ║") + "     ✓ SysDialogue 安装成功！" + "                           " + green("║"));
  console.log(green("  ║                                                          ║"));
  console.log(green("  ╚══════════════════════════════════════════════════════════╝"));
  console.log();
  log(cyan("下一步：配置你的 AI 连接"));
  log("");
  log("  sysdialogue --setup     交互式配置向导（推荐）");
  log("");
  log(cyan("或者手动配置环境变量："));
  log("  export OPENAI_API_KEY=your-key");
  log("  export OPENAI_MODEL=your-model");
  log("");
  log(cyan("更多命令："));
  log("  sysdialogue --help      查看所有选项");
  log("  sysdialogue-web         启动 Web 控制台");
  console.log();
}

main();
