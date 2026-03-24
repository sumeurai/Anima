---
name: anima-installer
description: Install, configure, start, verify, update, and optionally expose the Anima digital-human application that integrates with OpenClaw. Use when the user wants OpenClaw to autonomously deploy Anima on the current machine, connect it to local or remote OpenClaw Gateway, write server/.env correctly, run the Flask service in the background, optionally package Electron, and return a usable access URL plus troubleshooting guidance.
---

# Anima Installer Skill

本 Skill 用来帮主人把 Anima 安装成一个可访问的数字人应用，并尽量少问问题。

默认目标：
- 先把 Web 版跑通
- 让 `http://127.0.0.1:19000` 可访问
- 如果本机就是 OpenClaw 所在机，就直接接上 OpenClaw
- Electron 只在主人明确要求桌面安装包时再处理

## 0. 一句话告诉主人这是什么

你可以先这样告诉主人：

> 这是一个给 OpenClaw Agent 加上 3D 数字人界面、语音合成和唇形同步的前端应用。装好后，你可以在浏览器里和数字人聊天，并把它绑定到 OpenClaw Agent。

## 1. 默认原则

- 默认假设宿主机是 Linux 或 macOS，且 Anima 与 OpenClaw 在同一台机器。
- 默认只安装 Web 版，不在首次安装时打包 Electron。
- 始终使用真实仓库地址：`https://github.com/sumeurai/Anima.git`
- 不要默认走 `setup.sh`，因为：
  - 它默认会继续打包 Electron
  - `server/.env` 已存在时它不会覆盖，容易留下旧配置
- 优先自动读取本机 `~/.openclaw/openclaw.json` 里的 token。
- 只有在读不到 token、端口冲突、缺系统依赖、或主人明确要求远程 OpenClaw / Electron 时再提问。
- 不要擅自杀掉不明进程，不要擅自改端口。

## 2. 安装前检查

先确认：
- `git`
- `node` >= 18
- `npm`
- Python 3.9+
- 能访问 GitHub
- 能访问 `https://sumi-test.sumeruai.com`

说明：
- Anima 的登录、头像生成、TTS、ASR、ATF 默认依赖 `https://sumi-test.sumeruai.com`
- 即使 OpenClaw 已接好，如果这条外网链路不通，数字人功能仍会不完整

如果缺依赖：
- 先明确告诉主人缺什么
- 只有在主人同意时才使用系统包管理器安装

## 3. 推荐安装路径：先跑通 Web 版

默认安装目录：

```bash
$HOME/Anima
```

默认本机 OpenClaw 配置：

```env
OPENCLAW_API_URL=http://127.0.0.1:18789/v1/chat/completions
OPENCLAW_DEFAULT_AGENT=main
```

### 3.1 推荐执行步骤

按顺序执行，不要跳步：

```bash
set -euo pipefail

PROJECT_DIR="$HOME/Anima"
REPO_URL="https://github.com/sumeurai/Anima.git"
OPENCLAW_URL="http://127.0.0.1:18789/v1/chat/completions"
OPENCLAW_AGENT="main"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Python 3 not found" >&2
  exit 1
fi

TOKEN="$($PYTHON_BIN -c 'import json, pathlib; p=pathlib.Path.home()/".openclaw"/"openclaw.json"; print(json.loads(p.read_text(encoding="utf-8")).get("token","") if p.exists() else "")')"

if [ -d "$PROJECT_DIR/.git" ]; then
  git -C "$PROJECT_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
npm install
npm run build
"$PYTHON_BIN" -m pip install -r server/requirements.txt

if [ -f server/.env ]; then
  cp server/.env "server/.env.bak.$(date +%Y%m%d_%H%M%S)"
fi

if [ -n "$TOKEN" ]; then
  cat > server/.env <<EOF
ANIMA_HOST=0.0.0.0
ANIMA_PORT=19000
ANIMA_API_BASE_URL=https://sumi-test.sumeruai.com
OPENCLAW_API_URL=$OPENCLAW_URL
OPENCLAW_TOKEN=$TOKEN
OPENCLAW_DEFAULT_AGENT=$OPENCLAW_AGENT
EOF
else
  cat > server/.env <<EOF
ANIMA_HOST=0.0.0.0
ANIMA_PORT=19000
ANIMA_API_BASE_URL=https://sumi-test.sumeruai.com
EOF
fi
```

### 3.2 启动前先检查端口

先检查 `19000` 是否已经是 Anima：

```bash
curl -fsS http://127.0.0.1:19000/health || true
```

如果返回类似：

```json
{"status":"ok","version":"..."}
```

说明已经跑着了。此时：
- 不要重复启动
- 直接告诉主人访问地址

如果 `19000` 被占用，但 `/health` 不是 Anima：
- 不要直接杀进程
- 问主人是换端口还是停止现有服务

### 3.3 后台启动 Flask 服务

```bash
set -euo pipefail

PROJECT_DIR="$HOME/Anima"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

if [ -f "$PROJECT_DIR/anima-server.pid" ] && kill -0 "$(cat "$PROJECT_DIR/anima-server.pid")" 2>/dev/null; then
  kill "$(cat "$PROJECT_DIR/anima-server.pid")" || true
  sleep 1
fi

cd "$PROJECT_DIR/server"
nohup "$PYTHON_BIN" app.py > "$PROJECT_DIR/anima-server.log" 2>&1 &
echo $! > "$PROJECT_DIR/anima-server.pid"
sleep 3

curl -fsS http://127.0.0.1:19000/health
```

成功后告诉主人：

```text
Anima 已安装完成
访问地址：http://127.0.0.1:19000
日志文件：~/Anima/anima-server.log
PID 文件：~/Anima/anima-server.pid
```

## 4. 如果本机没读到 OpenClaw token

只问主人一次，推荐这样问：

> 我已经把 Anima 的 Web 安装准备好了，但当前没有读到 OpenClaw token。你希望我先只把界面跑起来，还是现在把 token 接上，直接启用 Agent 对话？

说明给主人：
- 没 token 也能打开页面
- 但绑定了 `agentId` 的数字人聊天不会完整工作

## 5. 安装成功后，必须提醒主人的内容

至少提醒这几件事：

1. 入口地址是 `http://127.0.0.1:19000`
2. 首次使用需要登录；登录与注册默认走 Anima 的远程服务
3. 创建数字人时，在 **Link Agent** 步骤里可以：
   - 新建 OpenClaw Agent
   - 绑定已有 Agent
4. 如果没有配置 `OPENCLAW_TOKEN`，页面能打开，但 OpenClaw Agent 对话能力不会完整

建议口径：

> 我已经把 Anima 跑起来了。你现在可以先打开页面体验数字人界面；如果你希望数字人直接对接 OpenClaw Agent，我也可以继续帮你检查 token 和 Agent 绑定。

## 6. 远程 OpenClaw 场景

只有在 Anima 与 OpenClaw 不在同一台机器时，才进入这个分支。

### 6.1 Gateway 可直接被远程访问

在 `server/.env` 里写：

```env
OPENCLAW_API_URL=http://<remote-ip>:18789/v1/chat/completions
OPENCLAW_TOKEN=<token>
OPENCLAW_DEFAULT_AGENT=main
OPENCLAW_SSH_HOST=agent@<remote-ip>
OPENCLAW_WORKSPACE_BASE=/home/agent/.openclaw
```

前提：
- 远程机器开放了 `18789`
- Flask 所在机器可 SSH 到 OpenClaw 所在机器
- 已配置 SSH 免密登录

### 6.2 Gateway 只监听 `127.0.0.1`

先建立 SSH 隧道：

```bash
ssh -L 18789:127.0.0.1:18789 user@<remote-ip>
```

保持这个终端不断开，然后在 `server/.env` 里写：

```env
OPENCLAW_API_URL=http://127.0.0.1:18789/v1/chat/completions
OPENCLAW_TOKEN=<token>
OPENCLAW_DEFAULT_AGENT=main
OPENCLAW_SSH_HOST=user@<remote-ip>
OPENCLAW_WORKSPACE_BASE=/home/agent/.openclaw
```

说明：
- `OPENCLAW_API_URL` 负责聊天转发
- `OPENCLAW_SSH_HOST` 负责前端创建、同步、删除 Agent 时执行 CLI

## 7. Electron 只在明确要求时处理

只有在主人明确要求桌面应用或安装包时，再执行这一段。

先说明：

> Electron 打包比 Web 版更重，首次安装时不建议默认执行；如果你需要桌面应用，我再继续打包。

执行：

```bash
set -euo pipefail
cd "$HOME/Anima/electron-shell"
npm install
npm run make
```

完成后告诉主人输出目录：

```text
~/Anima/electron-shell/out
```

说明：
- `electron-shell/src/main.ts` 会在桌面应用启动时自动检测 Flask 是否已运行
- 如果没运行，它会尝试自动拉起 `server/app.py`

## 8. 可选：给主人一个公网地址

如果机器上有 `cloudflared`，可以给主人一个临时公网链接：

```bash
cloudflared tunnel --url http://127.0.0.1:19000
```

拿到 `https://xxx.trycloudflare.com` 后发给主人，并说明：

> 这是临时公网地址，关闭隧道后会失效。

不要主动暴露内网 IP。

## 9. 更新已有安装

如果主人说“之前装过，现在帮我更新”，按这条链路做：

```bash
set -euo pipefail

PROJECT_DIR="$HOME/Anima"

if [ ! -d "$PROJECT_DIR/.git" ]; then
  echo "Anima repo not found at $PROJECT_DIR" >&2
  exit 1
fi

git -C "$PROJECT_DIR" pull --ff-only

cd "$PROJECT_DIR"
npm install
npm run build

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

"$PYTHON_BIN" -m pip install -r server/requirements.txt

if [ -f "$PROJECT_DIR/anima-server.pid" ] && kill -0 "$(cat "$PROJECT_DIR/anima-server.pid")" 2>/dev/null; then
  kill "$(cat "$PROJECT_DIR/anima-server.pid")" || true
  sleep 1
fi

cd "$PROJECT_DIR/server"
nohup "$PYTHON_BIN" app.py > "$PROJECT_DIR/anima-server.log" 2>&1 &
echo $! > "$PROJECT_DIR/anima-server.pid"
sleep 3

curl -fsS http://127.0.0.1:19000/health
```

更新时不要随便覆盖主人手改过的 `server/.env`，除非当前配置明显错误，或主人明确要求重置。

## 10. 常见问题与处理

### Q1. 页面能打开，但 Agent 列表为空

优先检查：
- `OPENCLAW_TOKEN` 是否为空
- `OPENCLAW_API_URL` 是否可达
- 本机执行 `openclaw agents list --json` 是否正常

告诉主人：

> 这通常不是前端没装好，而是 OpenClaw 配置没连通。

### Q2. 页面能打开，但创建数字人或语音功能失败

优先检查：
- 是否能访问 `https://sumi-test.sumeruai.com`
- 浏览器登录是否成功
- `anima-server.log` 是否有请求转发报错

说明：
- Anima 的头像生成、TTS、ASR、ATF 默认依赖远程 API

### Q3. `python app.py` 启动失败，提示找不到 `dist/`

说明前端还没 build。重新执行：

```bash
cd "$HOME/Anima"
npm install
npm run build
```

### Q4. 首次安装时 Electron 打包失败

不要把它当成主链路失败。先告诉主人：

> Web 版已经可以先正常使用；Electron 打包是额外步骤，可以稍后单独处理。

### Q5. `server/.env` 改了却没生效

重启 Flask 服务：

```bash
kill "$(cat "$HOME/Anima/anima-server.pid")" || true
cd "$HOME/Anima/server"
nohup python3 app.py > "$HOME/Anima/anima-server.log" 2>&1 &
echo $! > "$HOME/Anima/anima-server.pid"
```

如果当前机器没有 `python3`，把命令里的 `python3` 改成 `python`。

## 11. 只有在必要时才问主人的问题

只在下面这些情况提问：
1. 本机读不到 OpenClaw token
2. 端口 `19000` 被别的服务占用，且不是 Anima
3. 主人明确要桌面安装包，才继续 Electron
4. 缺系统依赖，而你需要用系统包管理器安装
5. 主人要求连接远程 OpenClaw，而不是本机 OpenClaw

## 12. 你自己的执行提醒

- 先跑通 Web 版，再谈 Electron
- 优先使用真实仓库地址：`https://github.com/sumeurai/Anima.git`
- 优先写对 `server/.env`
- 优先验证 `/health`
- 优先把日志与 PID 路径告诉主人
- 不要因为 Electron 或公网访问失败就误判整个 Anima 安装失败
