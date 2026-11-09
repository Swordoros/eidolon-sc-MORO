---
name: repo-versioning
description: "管理这个 Eidolon-SC fork 的本地 git 仓库：提交、打标签、推送到服务器裸仓库 origin，查看历史、回滚，以及换机克隆和恢复。当用户说「提交」「推送」「保存版本」「打个标签」「回滚」「版本管理」「仓库怎么更新」时用。只做版本管理；把改动部署到运行中的服务器用 deploy-server。"
---

# 仓库版本管理

本地改动先在这里提交并推送；部署到服务器是另一件事，见 `deploy-server`。

## 环境

| 项 | 值 |
|---|---|
| 本地仓库 | `D:\Desktop\staff\eidolon-sc-Moro`，分支 `master` |
| 远程 | `ssh://root@39.105.89.152/root/eidolon.git`（服务器上的裸仓库，约 62MB） |
| SSH key | `D:\Documents\EidolonTunnel\SC.pem`（Git Bash 里写 `/d/Documents/EidolonTunnel/SC.pem`） |
| 已配置 | 仓库的 `core.sshCommand` 已指向这个 key，`git push` / `git fetch` 直接可用 |

新克隆的仓库没有 `core.sshCommand`，先补上再推：

```powershell
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe -i D:/Documents/EidolonTunnel/SC.pem -o IdentitiesOnly=yes -o BatchMode=yes'
```

## 日常：提交并推送

1. **看清改了什么**。`git status -sb` 和 `git diff`：确认改动都是自己的，没有 `.env`、数据库、密钥或大文件。完成条件：每一处改动都能说清是什么。
2. **只加要提交的文件**。`git add <文件…>`；不要用 `git add -A` 扫进无关产物。完成条件：`git status -sb` 里只剩预期文件。
3. **提交**。提交信息用中文、一句话说清做了什么，例如：

   ```powershell
   git commit -m "修复群聊话题在成员全睡时的时间计算"
   ```

   完成条件：`git log --oneline -1` 显示新提交，`git status -sb` 不再有未提交的改动。
4. **推送**。分支已跟踪 `origin/master`：

   ```powershell
   git push
   ```

   完成条件：`git status -sb` 第一行是 `## master...origin/master`，没有 `ahead`；`git log --oneline -1 origin/master` 与本地 HEAD 相同。

## 查看与比较

```powershell
git log --oneline --graph --decorate -20   # 最近的提交和标签
git show <commit>                          # 某次提交改了什么
git diff <旧>..<新> -- <路径>               # 两次提交之间的差异
git blame <文件>                            # 每一行是谁、哪次提交改的
git reflog                                  # 最近 HEAD 去过哪里，找回误删的提交
```

## 回滚与撤销

先判断改动有没有提交、有没有推送，再动手：

| 情况 | 做法 |
|---|---|
| 改了还没 add | `git restore <文件>` |
| add 了还没 commit | `git restore --staged <文件>`，再按上一条处理 |
| commit 了还没 push | 想保留改动：`git reset --soft HEAD~1`；确认不要了：`git reset --hard HEAD~1` |
| 已经 push | 不要改写远程历史，用 `git revert <commit>` 生成反向提交，再 `git push` |
| 只想把某个文件恢复到旧版本 | `git restore --source <commit> -- <文件>` |

`git reset --hard` 会丢工作区改动，执行前先 `git status` 确认。

## 打标签

里程碑、上线前、大规模改动前打一个标签，方便以后回到这个点：

```powershell
git tag -a v2026-11-10 -m "分析 bug 前的基线"
git push origin v2026-11-10
git ls-remote --tags origin v2026-11-10   # 确认远程已有
```

删标签：本地 `git tag -d <名>`，远程 `git push origin :refs/tags/<名>`。

## 远程与换机恢复

服务器裸仓库是异地备份，检查它：

```powershell
ssh -i 'D:\Documents\EidolonTunnel\SC.pem' root@39.105.89.152 'git --git-dir=/root/eidolon.git log --oneline -3'
```

换一台机器继续用：

```powershell
git clone ssh://root@39.105.89.152/root/eidolon.git eidolon-sc-Moro
cd eidolon-sc-Moro
git config core.sshCommand 'C:/Windows/System32/OpenSSH/ssh.exe -i D:/Documents/EidolonTunnel/SC.pem -o IdentitiesOnly=yes -o BatchMode=yes'
```

克隆拿到的只有代码和文档；`characters/` 是提交进仓库的，`corpus/`、`.env`、数据库和各机产物要另行准备。

## 提交边界

这些不进仓库：

- `.env`、`.env.*`（`.env.example` 除外）和任何密钥
- `*.db`、`.scratch/deploy/`（镜像 tar、数据库副本）
- `frontend/node_modules/`、`backend/.venv/`、`frontend/.next/`
- 大二进制（立绘、剧情库、音频素材）；它们放对象存储或服务器
- `.claude/settings.local.json` 等本机配置

提交进历史的大文件很难彻底清掉；不确定时先 `git status` 和 `git diff --stat` 看一眼文件大小。

## 常见故障

- **`Permission denied (publickey)`**：`core.sshCommand` 里的 key 路径不对，或 key 被移动。先手动 `ssh -i D:\Documents\EidolonTunnel\SC.pem root@39.105.89.152 'echo ok'` 确认。
- **`index.lock` 已存在**：确认没有别的 git 进程在跑，再删 `.git/index.lock`。
- **推送被拒（non-fast-forward）**：先 `git fetch`、看 `git log --oneline HEAD..origin/master`；需要合并时再 `git pull --rebase`，不要强推。
- **仓库变大**：`git count-objects -vH` 看大小，查最近提交里有没有大文件；必要时 `git gc`。

## 和部署的关系

提交、推送只更新远程仓库，**不会**改变正在运行的容器。要让服务器上的前后端用上新代码，提交并推送后再按 `deploy-server` 做 overlay 或整镜像重建。
