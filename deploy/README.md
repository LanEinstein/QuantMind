# QuantMind 部署模板

本目录存放**生产评估期**部署模板：`docker compose`（仅基础设施）+ systemd
（后端）+ nginx（前端 & HTTPS）。执行路径需要 sudo 权限，由管理员手动完成。
所有模板都不会自动复制到 `/etc/`。

> 前提：Phase 5 代码已完成（A1+A2 代码已合入 main，验收 pytest/前端/Playwright
> 通过）。

## 拓扑总览

| 组件 | 运行方式 | 说明 |
|---|---|---|
| MongoDB | `docker compose` | `restart: unless-stopped` |
| Redis | `docker compose` | `restart: unless-stopped` |
| Backend | systemd + conda | 绑定 `127.0.0.1:8000`，单 worker 保证 SSE hub 一致 |
| Frontend | `npm run build` + nginx 静态 | nginx 反代 `/api` 和 `/ws` |
| HTTPS | nginx + mkcert | 本地可信证书 `quantmind.local` |

## 一次性安装步骤

### 1. 基础设施（无需 sudo）

```bash
cd /home/ps/papers/QuantMind

# 仅启动 MongoDB + Redis，后端不走容器
docker compose up -d
docker compose ps
```

### 2. 私有 LLM env 文件（chmod 600）

```bash
mkdir -p /home/ps/.config/quantmind
cp deploy/llm.env.example /home/ps/.config/quantmind/llm.env
chmod 600 /home/ps/.config/quantmind/llm.env
# 然后编辑替换 replace-me 为真实 key
```

### 3. systemd 后端服务（需 sudo）

```bash
sudo cp deploy/quantmind-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quantmind-backend
sudo systemctl status quantmind-backend
```

卸载：
```bash
sudo systemctl disable --now quantmind-backend
sudo rm /etc/systemd/system/quantmind-backend.service
sudo systemctl daemon-reload
```

### 4. 前端构建

```bash
cd frontend
npm run build
# dist/ 将由 nginx 托管
```

### 5. nginx + mkcert（需 sudo）

```bash
# 安装 mkcert（一次性）
sudo apt install mkcert libnss3-tools     # Debian/Ubuntu
mkcert -install                            # 注入本地根证书

# 生成本地证书
sudo mkdir -p /etc/ssl/quantmind
cd /etc/ssl/quantmind
sudo mkcert -cert-file quantmind.local.crt -key-file quantmind.local.key \
    quantmind.local localhost 127.0.0.1

# 写 hosts
grep -q 'quantmind.local' /etc/hosts || \
  echo '127.0.0.1 quantmind.local' | sudo tee -a /etc/hosts

# 安装 nginx + 配置
sudo apt install nginx
sudo cp /home/ps/papers/QuantMind/deploy/nginx-quantmind.conf \
    /etc/nginx/sites-available/quantmind
sudo ln -sf /etc/nginx/sites-available/quantmind /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 6. 备份目录与文件权限

```bash
# 默认备份路径（已从仓库内 backups/ 改到家目录 state 目录）
mkdir -p ~/.local/state/quantmind/backups
chmod 700 ~/.local/state/quantmind/backups

# 仓库根的 .env 必须 0600，避免 systemd 服务读到 group/world-readable
# 的非占位配置。
chmod 600 /home/ps/papers/QuantMind/.env
chmod 600 /home/ps/.config/quantmind/llm.env
```

### 7. 冒烟验证

```bash
curl -k https://quantmind.local/api/health
curl -k https://quantmind.local/api/analysis/history?limit=1
# 浏览器打开 https://quantmind.local/agent-debate
```

### 8. 重启自愈测试

```bash
sudo reboot
# 15 分钟内检查：
docker compose ps                          # mongodb + redis 恢复
sudo systemctl status quantmind-backend    # active (running)
curl -k https://quantmind.local/api/health # 200 ok
```

## 文件清单

| 文件 | 目标位置 | 用途 |
|---|---|---|
| `deploy/quantmind-backend.service` | `/etc/systemd/system/` | systemd unit |
| `deploy/llm.env.example` | `/home/ps/.config/quantmind/llm.env` | 私有 LLM key |
| `deploy/nginx-quantmind.conf` | `/etc/nginx/sites-available/quantmind` | nginx 反代 |

## 安全红线

- `deploy/llm.env.example` 仅占位，**禁止**填入真实 key 后再提交 git。
- `/home/ps/.config/quantmind/llm.env` **必须** chmod 600。
- `/home/ps/papers/QuantMind/.env` 也 **必须** chmod 600（systemd 通过
  EnvironmentFile 加载它）。
- 运行模式由 `FEISHU_INTERACTIVE_ENABLED`(P0-1)单一开关决定：默认 false 启动
  `simulation_auto` 底座；仅 P0-6 45 交易日验收通过后才允许置 true 叠加 Feishu
  人工执行通道。`AUTHORIZATION_MODE` / `QUANTMIND_PHASE` 已删除，禁止重新引入。
- 全层入站监听必须 127.0.0.1 only(P1-6 §1.5)：backend uvicorn `--host 127.0.0.1`、
  Vite `host: '127.0.0.1'`、nginx `listen 127.0.0.1:80/443`、docker-compose
  `127.0.0.1:8000:8000`、Mongo/Redis `127.0.0.1`。远程访问只走 SSH tunnel。
- `scripts/backup.sh` 默认写 `~/.local/state/quantmind/backups`（仓库外），
  加 `umask 077` + `chmod 600`。如改写到仓库内目录，必须确认
  `.gitignore` 已包含该路径。
