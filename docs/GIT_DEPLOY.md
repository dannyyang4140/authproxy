# 用 GitHub + Compose YAML 部署（GitOps）

把代码放到你自己的 GitHub 仓库，部署时让面板或服务器**拉取仓库中的 `docker-compose.git.yml` 自动构建运行**。好处：配置即代码、更新只需 `git push`、可多环境复用；密钥通过部署环境变量注入，**不进 Git 仓库**。

- 想全程鼠标操作：用 **Portainer 从 Git 仓库部署（方式 A，推荐）**。
- 不装面板：在服务器 `git clone` 后用 compose 部署（方式 B）。

---

## 一、把代码推到 GitHub

> 安全前提：仓库里**只放 `.env.example`，绝不放真实 `.env`**。工程的 `.gitignore` 已忽略 `.env`、`*.db`、`data/`，但仍请人工确认一次。建议仓库设为 **Private**。

### 方法 1：网页上传（最简单，无需安装 Git）

1. 在 GitHub 右上角 `+ → New repository`，建一个私有仓库，例如 `ragflow-auth-proxy`，**不要**勾选自动生成 README。
2. 进入空仓库页面，点击 **“uploading an existing file”**。
3. 把本地解压后的 `ragflow-auth-proxy/` 目录里的**全部内容**拖进去（包含 `app/`、`Dockerfile`、`docker-compose.git.yml`、`.env.example`、`.gitignore` 等；**不要拖入 `.env`、`data/`**）。
4. 点 **Commit changes**。

### 方法 2：本地 Git 命令

```bash
cd ragflow-auth-proxy
# 确认没有真实密钥会被提交
ls -a | grep -E '^\.env$' && echo "警告：存在 .env，请勿提交" || echo "OK：无 .env"

git init
git add .
git commit -m "init ragflow-auth-proxy"
git branch -M main
git remote add origin https://github.com/<你的用户名>/ragflow-auth-proxy.git
git push -u origin main
```

私有仓库供面板拉取时，需要一个 **GitHub Personal Access Token（PAT）**：
GitHub 头像 → Settings → Developer settings → Personal access tokens → Fine-grained token（或 Tokens classic，勾选 `repo` 读取权限）→ 生成并复制保存。

---

## 二、方式 A：Portainer 从 Git 仓库部署（可视化）

前置：已在服务器安装 Portainer CE（安装命令见文末）。

1. 左侧选择你的环境（**local / Docker Standalone**）→ **Stacks → Add stack**。
2. Build method 选择 **Git repository**。
3. 填写：
   - **Name**：`ragflow-auth-proxy`
   - **Repository URL**：`https://github.com/<你的用户名>/ragflow-auth-proxy.git`
   - 私有仓库：在 **Repository credentials** 填 GitHub 用户名和上面的 PAT。
   - **Repository reference**：`refs/heads/main`
   - **Compose path**：`./docker-compose.git.yml`
4. 展开 **Environment variables**，按下表逐条添加（这些就是密钥与配置，不会进入仓库）：

| 变量名 | 是否必填 | 示例值 |
| --- | --- | --- |
| `RAGFLOW_API_KEY` | 必填 | RAGFlow 的 API Key |
| `ADMIN_TOKEN` | 必填 | 你设定的管理员强口令 |
| `RAGFLOW_BASE_URL` | 可选 | RAGFlow 在宿主机用默认 `http://host.docker.internal:9380`；在其它机器填 `http://<IP>:9380` |
| `PUBLISH_PORT` | 可选 | 默认 `8000` |
| `PIP_INDEX_URL` | 可选 | 国内构建加速 `https://mirrors.aliyun.com/pypi/simple/` |
| `DEFAULT_RATE_LIMIT_PER_MIN` | 可选 | 默认 `0`（不限） |
| `STRICT_RESOURCE_BINDING` | 可选 | 默认 `true` |

5. 展开 **Automatic updates（可选但推荐）**：开启后选择间隔（如 5 分钟），勾选 **Re-pull and redeploy**；以后你 `git push`，Portainer 会自动拉取并重新构建部署。也可关闭，改为每次手动在 Stack 页点 **Pull and redeploy**。
6. 点 **Deploy the stack**。Portainer 会自动 clone → build → 起容器。
7. 在 **Containers** 查看 `ragflow-auth-proxy` 的日志与状态；浏览器访问 `http://<服务器IP>:8000/admin/ui`，用 `ADMIN_TOKEN` 登录签发访问令牌。

> Portainer 安装（仅首次，一条命令）：
> ```bash
> docker volume create portainer_data
> docker run -d -p 9443:9443 --name portainer --restart=always \
>   -v /var/run/docker.sock:/var/run/docker.sock \
>   -v portainer_data:/data portainer/portainer-ce:latest
> ```
> 访问 `https://<服务器IP>:9443` 初始化管理员账号。

---

## 三、方式 B：服务器命令行 `git clone` + Compose

```bash
# 1. 克隆（私有仓库按提示输入用户名 + PAT 作为密码）
git clone https://github.com/<你的用户名>/ragflow-auth-proxy.git
cd ragflow-auth-proxy

# 2. 在服务器创建不进仓库的 .env（compose 会自动用它做变量插值）
cat > .env <<'EOF'
RAGFLOW_API_KEY=ragflow-你的真实Key
ADMIN_TOKEN=你的管理员强口令
RAGFLOW_BASE_URL=http://host.docker.internal:9380
PUBLISH_PORT=8000
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
EOF
chmod 600 .env

# 3. 用 Git 专用 compose 构建并启动
docker compose -f docker-compose.git.yml up -d --build
docker compose -f docker-compose.git.yml logs -f
```

更新到最新代码：

```bash
cd ragflow-auth-proxy
git pull
docker compose -f docker-compose.git.yml up -d --build
```

---

## 四、验证

```bash
curl http://localhost:8000/health     # 返回 {"status":"ok",...}
```

浏览器打开 `http://<服务器IP>:8000/admin/ui`，用 `ADMIN_TOKEN` 登录 → 签发令牌；业务侧用 `X-Proxy-Token: rfp_xxx` 调用 `/api/v1/...`。

## 五、数据与密钥说明

- 令牌、审计保存在 Docker 命名卷 `ragflow_auth_data`（容器内 `/data`），**更新/重建容器不会丢**；删除 Stack/容器时不要勾选删除卷。
- 密钥只存在于：Portainer 环境变量 / 服务器 `.env`，Git 仓库中无密钥。
- 轮换密钥：在 Portainer 修改环境变量或改服务器 `.env` 后重新部署即可。

## 六、常见问题

| 现象 | 处理 |
| --- | --- |
| 部署报 `RAGFLOW_API_KEY` / `ADMIN_TOKEN` 必填 | compose 的 `${VAR:?}` 校验生效，在 Portainer 环境变量表单或服务器 `.env` 补齐 |
| Portainer 拉取私有仓库失败鉴权 | 检查 Repository credentials 的用户名与 PAT，PAT 需具备该仓库读取权限 |
| Build 很慢/超时 | 设置 `PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/` |
| 502 无法连接 RAGFlow | 核对 `RAGFLOW_BASE_URL`；RAGFlow 在其它容器/主机时改用对应地址，宿主机方案已内置 host-gateway |
| 自动更新未生效 | 确认 Automatic updates 已开启且仓库分支为 `main`；或手动 Pull and redeploy |
| Compose path 找不到 | 仓库根目录下应能看到 `docker-compose.git.yml`，Compose path 填 `./docker-compose.git.yml` |
