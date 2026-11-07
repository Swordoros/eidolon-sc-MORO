---
name: deploy-server
description: "把代码改动部署到服务器 39.105.89.152 的 Docker 环境（Eidolon fork，compose 项目 eidolon-sc-041）。后端 Python 源文件改动用 overlay 镜像秒上线，改依赖或改前端才需要整镜像重建。当用户说「部署」「上线」「更新服务器上的后端/前端」「把改动推到服务器」时用。"
---

# 部署到服务器（39.105.89.152）

把本地仓库（`D:\WindowsDocument\Desktop\staff\eidolon-sc-Moro`）里的代码改动推到阿里云服务器上的 Docker 环境。

## 环境速查

| 项 | 值 |
|---|---|
| 服务器 | `39.105.89.152`，root，SSH key `D:\WindowsDocument\Desktop\SC.pem`（Git Bash 里写 `/d/WindowsDocument/Desktop/SC.pem`） |
| compose 目录 | `/root/eidolon/`（`docker-compose.yml`，项目名 `eidolon-sc-041`） |
| 镜像 | `eidolon-sc-041-backend:latest`(1.81GB)、`eidolon-sc-041-frontend:latest`(262MB) |
| 容器 | `eidolon-sc-041-backend-1`、`eidolon-sc-041-frontend-1` |
| 数据 | docker 卷 `eidolon-data`，容器内 `/data`（含 `/data/eidolon.db`），重建容器不丢 |

**关键机制**：后端 `eidolon` 是 **editable 安装**——site-packages 里的 `eidolon.pth` 指向 `/app/src`，所以 `import eidolon.<mod>` 走的是 `/app/src/eidolon/<mod>.py`。改单个 Python 源文件，只要覆盖镜像里的 `/app/src/eidolon/<file>` 就生效，**不用重装依赖、不用重建 1.81GB 整镜像**。前端是编译产物（Next.js），没有这个机制。

## 后端单文件改动（overlay，秒上线）

改后端一个 Python 文件（最常见）时：

1. **本地改代码 + 跑测试**。`eidolon` 是 src 布局、没装进 venv，得靠 `PYTHONPATH`：

   ```bash
   cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/<file>.py
   ```

2. **scp 到服务器**：

   ```bash
   scp -i /d/WindowsDocument/Desktop/SC.pem backend/src/eidolon/<file>.py \
     root@39.105.89.152:/root/eidolon/overlay/<file>.py
   ```

3. **服务器上打 overlay 镜像 + 重建**（一次 SSH 跑完）：

   ```bash
   ssh -i /d/WindowsDocument/Desktop/SC.pem root@39.105.89.152 '
     set -e
     docker tag eidolon-sc-041-backend:latest eidolon-sc-041-backend:pre-fix   # 首次备份，之后跳过
     mkdir -p /root/eidolon/overlay
     printf "FROM eidolon-sc-041-backend:latest\nCOPY <file>.py /app/src/eidolon/<file>.py\n" \
       > /root/eidolon/overlay/Dockerfile
     docker build -t eidolon-sc-041-backend:latest /root/eidolon/overlay/
     cd /root/eidolon && docker compose up -d
   '
   ```

   `up -d` 会重建 backend 和 frontend 两个容器（服务会断几秒）。

4. **验证**（见下面「验证」）。

## 后端多文件改动

改多个文件时，Dockerfile 写多条 `COPY`，或把改动按目录结构放好、整目录覆盖：

```dockerfile
FROM eidolon-sc-041-backend:latest
COPY eidolon/ /app/src/eidolon/
```

scp 时把改动文件按 `overlay/eidolon/<子路径>/<file>.py` 的目录结构放好即可。

## 改依赖或改前端（整镜像重建）

overlay 只覆盖 Python 源文件。下面这些情况要**整镜像重建**：

- 改了 `backend/pyproject.toml` 或 `uv.lock`（依赖变化）。
- 改了 `frontend/`（编译产物，没有 editable 机制）。

整镜像重建两种方式：

- **本地构建**：Docker Desktop 要开着。`docker compose build backend`（或 `frontend`）→ `docker save <镜像> | gzip > xxx.tar` → scp → 服务器 `docker load < xxx.tar` → `docker compose up -d`。服务器上的 `/root/eidolon/backend.tar`、`frontend.tar` 就是这个 `docker save` 产物。
- **服务器构建**：`docker compose up -d --build`。大陆服务器下依赖要配代理，慢。

## 验证

```bash
ssh -i /d/WindowsDocument/Desktop/SC.pem root@39.105.89.152 '
  docker logs --since 2m eidolon-sc-041-backend-1 | grep -iE "story vectors|uvicorn|error|traceback" | tail
  docker exec eidolon-sc-041-backend-1 python -c "import eidolon.<mod> as m; print(m.__file__)"
'
```

- 看到 `story vectors loaded: ...` 和 `Uvicorn running on 0.0.0.0:8000`、没有 error/traceback，即启动正常。
- `m.__file__` 指向 `/app/src/eidolon/<mod>.py`，且改动特征（新常量/函数）能 import 到，即新代码生效。
- **注意**：后端 8000 端口没映射到宿主机，`curl 127.0.0.1:8000` 会 000；用户访问的是前端 3000（前端内网走 `backend:8000`）。

## 回滚

```bash
docker tag eidolon-sc-041-backend:pre-fix eidolon-sc-041-backend:latest
cd /root/eidolon && docker compose up -d
```

## 查数据库（排障常用）

宿主机 `sqlite3` CLI 太老读不了 schema（报 `malformed database schema`），要用容器里的 Python（3.11 + sqlite 3.46）：

```bash
ssh -i /d/WindowsDocument/Desktop/SC.pem root@39.105.89.152 "docker exec -i eidolon-sc-041-backend-1 python -" <<'PY'
import sqlite3, json
db = sqlite3.connect('file:/data/eidolon.db?mode=ro', uri=True)
db.row_factory = sqlite3.Row
for r in db.execute("SELECT id, role, text, ja, zh, segments, created_at FROM messages ORDER BY id DESC LIMIT 20"):
    print(dict(r))
PY
```

关键表 `messages(id, conversation_id, role, text, ja, zh, segments, created_at, affinity)`：`role='user'` 是用户的话（`text`），`role='character'` 是回复（`segments` 是 JSON `[{"ja":…,"zh":…}]`，`ja`/`zh` 是拼接后的整段）。查通话/回复问题先看这张表。
