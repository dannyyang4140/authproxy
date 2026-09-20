# RAGFlow 鉴权代理（ragflow-auth-proxy）

在企业成员 / AI 智能体与 RAGFlow 之间增加一层 **API 转发鉴权网关**：

- 成员 / Agent **不直接持有 RAGFlow API Key**，而是使用管理员分配的访问令牌（`X-Proxy-Token`）。
- 每个令牌可独立配置 **可访问的数据集（dataset）ID 白名单** 与 **权限级别**。
- 网关完成认证、数据集级授权、请求改写、限流、审计后，再注入服务端托管的 RAGFlow Key 转发请求；支持 **SSE 流式对话** 与 **文件上传** 透传。
- 提供 Web 管理台、REST 管理接口、命令行三种令牌管理方式，**Docker / docker compose 一键部署**。

## 一、能力一览

| 能力 | 说明 |
| --- | --- |
| 访问令牌 | 管理员签发，形如 `rfp_xxx`，数据库仅存 SHA-256，明文只在创建时返回一次 |
| 数据集白名单 | 每个令牌限定可访问的 dataset ID；`*` 表示全部 |
| 三级权限 | `retrieve`（检索/对话）< `write`（含上传/改文档）< `admin`（含建删知识库/助手） |
| 多位置校验 | 同时校验 **URL 路径** 中的 dataset、**请求体** `dataset_ids`、以及 `document_ids` / `chat_id` 反查归属 |
| 请求改写 | 自动把请求体 `dataset_ids` 收敛为白名单交集，纵深防御 |
| 列举过滤 | `GET /datasets` 只返回该令牌有权访问的知识库，防止枚举 |
| 流式透传 | OpenAI 兼容 `/openai/{chat_id}/chat/completions` 的 SSE 原样透传 |
| 限流 | 按令牌设置每分钟调用上限 |
| 审计 | 记录主体、IP、方法、路径、数据集、放行/拒绝、原因、状态码 |
| 管理台 | 浏览器打开 `/admin/ui` 即可签发/停用/删除令牌、查看审计 |

## 二、目录结构

```
ragflow-auth-proxy/
├── app/
│   ├── main.py          # 入口：生命周期、路由挂载、管理台
│   ├── config.py        # 环境变量配置
│   ├── db.py / models.py# SQLite 引擎与令牌、审计表
│   ├── schemas.py       # 接口模型、权限级别
│   ├── security.py      # 令牌生成/哈希、管理员校验
│   ├── policy.py        # 动作分类、资源提取、白名单决策（纯函数）
│   ├── ragflow.py       # RAGFlow 客户端与 document/chat 归属反查
│   ├── proxy.py         # 反向代理：认证→授权→改写→转发（含 SSE）
│   ├── admin.py         # 管理 API
│   ├── cli.py           # 命令行管理
│   └── static/admin.html# 管理台页面
├── tests/               # 单元 + 端到端测试（20 个用例）
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

## 三、快速部署（Docker Compose）

> 想把代码放到 **GitHub 仓库**、用 compose YAML 拉取并由 **Portainer 可视化一键部署 / 自动更新**，见 **[docs/GIT_DEPLOY.md](docs/GIT_DEPLOY.md)**（使用 `docker-compose.git.yml`，密钥通过环境变量注入、不进仓库）。

前置：目标机器已安装 Docker 与 Docker Compose，且能访问 RAGFlow 服务。

```bash
# 1. 进入目录，准备配置
cp .env.example .env
vi .env
#   必改两项：
#   RAGFLOW_BASE_URL   RAGFlow 地址
#   RAGFLOW_API_KEY    RAGFlow 管理员 API Key
#   ADMIN_TOKEN        管理台/管理接口口令（务必改成强随机串）

# 2. 构建并启动（国内构建可在 .env 中设置 PIP_INDEX_URL）
docker compose up -d --build

# 3. 查看状态与日志
docker compose ps
docker compose logs -f
```

启动后：

- 管理台：`http://<部署机>:8000/admin/ui`
- 健康检查：`http://<部署机>:8000/health`
- 接口文档：`http://<部署机>:8000/docs`

> 若 `.env` 中 `ADMIN_TOKEN` 留空，首次启动会随机生成并写入数据卷 `/data/admin_token.txt`，可通过
> `docker compose exec ragflow-auth-proxy cat /data/admin_token.txt` 查看。

### RAGFlow 连接地址怎么填

- RAGFlow 部署在**宿主机**：`RAGFLOW_BASE_URL=http://host.docker.internal:9380`（compose 已配置 host-gateway）。
- RAGFlow 是**另一个容器 / 同一 compose**：用服务名，如 `http://ragflow:9380`，并把两者放到同一 Docker 网络。
- RAGFlow 在**其它机器**：填 `http://<内网IP>:9380`。

RAGFlow API Key 在 RAGFlow 界面「右上角头像 → API」页面创建。

## 四、签发访问令牌

三种方式任选。

**方式 A：管理台** —— 打开 `/admin/ui`，输入 `ADMIN_TOKEN`，在「签发访问令牌」卡片填写名称、数据集 ID、权限级别、有效期、限流即可。

**方式 B：管理 API**

```bash
curl -X POST http://localhost:8000/admin/tokens \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{
        "name": "法务Agent",
        "datasets": ["ds_001", "ds_002"],
        "scope": "retrieve",
        "rate_limit_per_min": 60,
        "expires_at": "2026-12-31T23:59:59+08:00"
      }'
# 返回 JSON 中的 token 字段即明文访问令牌，仅显示一次
```

**方式 C：命令行（容器内）**

```bash
docker compose exec ragflow-auth-proxy python -m app.cli issue \
  --name 法务Agent --datasets ds_001,ds_002 --scope retrieve --expire-days 90 --rate-limit 60
docker compose exec ragflow-auth-proxy python -m app.cli list
docker compose exec ragflow-auth-proxy python -m app.cli disable <token_id>
```

## 五、业务侧如何调用

把原本发给 RAGFlow 的请求改为发给网关，请求头把 RAGFlow Key 换成 `X-Proxy-Token`，**路径与请求体保持不变**。

检索（retrieval）：

```bash
curl -X POST http://localhost:8000/api/v1/retrieval \
  -H "X-Proxy-Token: rfp_xxxx" \
  -H "Content-Type: application/json" \
  -d '{"question":"报销标准是多少","dataset_ids":["ds_001"]}'
```

OpenAI 兼容流式对话（SSE，网关按 chat 绑定的数据集鉴权）：

```bash
curl -N http://localhost:8000/api/v1/openai/<chat_id>/chat/completions \
  -H "X-Proxy-Token: rfp_xxxx" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}],"stream":true}'
```

上传文档到指定知识库（需 `write` 权限，multipart 原样透传）：

```bash
curl -X POST http://localhost:8000/api/v1/datasets/ds_001/documents \
  -H "X-Proxy-Token: rfp_xxxx" \
  -F "file=@./制度.pdf"
```

> 也支持 `Authorization: Bearer rfp_xxxx` 传递访问令牌；但推荐用独立头 `X-Proxy-Token`，与上游 RAGFlow Key 明确区分。

## 六、权限模型与校验逻辑

1. **动作分级（scope）**：网关依据「HTTP 方法 + 路径」把请求映射为 `retrieve / write / admin`，令牌级别不足直接拒绝（403）。
   - 检索、对话、GET 读取 → `retrieve`
   - 上传/删除/修改文档与分块 → `write`
   - 创建/删除知识库、创建/删除聊天助手 → `admin`
2. **数据集白名单**：以下三处出现的数据集 ID 都会被汇总校验，任一不在白名单即拒绝：
   - 路径：`/api/v1/datasets/{dataset_id}/...`
   - 请求体：`"dataset_ids": [...]`（retrieval、创建助手）
   - 反查归属：仅给 `document_ids` 时反查文档所属库；`chat_id` 反查助手绑定的库。
3. **默认拒绝 + 交集改写**：默认无权限；放行时还会把请求体 `dataset_ids` 重写为「请求值 ∩ 白名单」，作为纵深防御。
4. **严格模式** `STRICT_RESOURCE_BINDING=true`（默认）：当 document/chat 无法反查到归属数据集、或写操作无法定位数据集时，一律拒绝。如你的 RAGFlow 版本反查接口不同导致误伤，可设为 `false`（需自行评估风险）。
5. **列举过滤**：`GET /api/v1/datasets` 的返回会被裁剪为白名单内的知识库。

## 七、环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RAGFLOW_BASE_URL` | `http://ragflow:9380` | RAGFlow 地址 |
| `RAGFLOW_API_KEY` | 空 | RAGFlow 管理员 API Key（服务端托管） |
| `RAGFLOW_TIMEOUT_SECONDS` | `120` | 上游超时 |
| `ADMIN_TOKEN` | 空（自动生成） | 管理台/管理接口口令 |
| `ADMIN_TOKEN_FILE` | `/data/admin_token.txt` | 自动生成口令的落盘位置 |
| `DATABASE_URL` | `sqlite:////data/proxy.db` | 数据库连接 |
| `STRICT_RESOURCE_BINDING` | `true` | 无法确认资源归属时是否拒绝 |
| `BINDING_CACHE_TTL` | `300` | document/chat 归属反查缓存秒数 |
| `DEFAULT_RATE_LIMIT_PER_MIN` | `0` | 新令牌默认每分钟限流，0 不限 |
| `CORS_ORIGINS` | 空 | 浏览器跨域来源，逗号分隔；服务端调用留空 |
| `PUBLISH_PORT` | `8000` | 宿主机暴露端口 |

## 八、管理 API 速览（均需请求头 `X-Admin-Token`）

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/admin/tokens` | 列出令牌 |
| POST | `/admin/tokens` | 签发令牌（返回一次性明文） |
| PATCH | `/admin/tokens/{id}` | 更新白名单/权限/有效期/启停/限流 |
| DELETE | `/admin/tokens/{id}` | 删除令牌 |
| GET | `/admin/audit?limit=&token_id=&decision=` | 查询审计日志 |
| GET | `/admin/stats` | 令牌数与当日调用/拒绝统计 |

## 九、数据持久化与备份

- SQLite 数据库与管理员令牌文件位于数据卷 `/data`（compose 使用命名卷 `ragflow_auth_data`）。
- 备份：`docker run --rm -v <卷>:/data -v $PWD:/backup alpine tar czf /backup/ragflow-auth-backup.tgz -C /data .`
- 迁移：在新环境挂载同一数据卷或还原该目录即可。

## 十、安全加固建议（生产必看）

1. **网络层防绕过（最重要）**：用防火墙 / 安全组把 RAGFlow 的 9380 端口限制为**仅允许本网关容器 IP** 访问，使成员无法绕过网关直连 RAGFlow。
2. `ADMIN_TOKEN` 与 `RAGFLOW_API_KEY` 使用强随机值，通过 `.env`（不要提交到代码库）或密钥管理注入。
3. 网关前建议放置 HTTPS 反向代理（Nginx / 负载均衡），实现 TLS 终止。
4. 容器以非 root 用户（uid 10001）运行；数据卷定期备份。
5. 人员离职 / Agent 下线：在管理台停用或删除对应令牌，立即生效。
6. 按需最小授权：绝大多数成员/Agent 授予 `retrieve` + 必要的数据集即可，谨慎授予 `write/admin` 与 `*`。

## 十一、扩容与高可用说明

当前版本面向 200~500 人规模设计，**单实例 + SQLite 即可满足**（鉴权为 IO 密集，瓶颈在下游检索与大模型）。注意：

- 限流计数与归属反查缓存为**进程内**实现，SQLite 也不适合多实例并发写。
- 若未来多副本水平扩展，请将 `DATABASE_URL` 切换为 PostgreSQL，并把限流/缓存改为 Redis（代码中 `ratelimit.py` 与 `ragflow.py` 已隔离，替换实现即可）。

## 十二、本地开发与测试

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q          # 运行 20 个用例
uvicorn app.main:app --reload # 本地启动
```

## 十三、故障排查

| 现象 | 排查 |
| --- | --- |
| 401 缺少访问令牌 | 请求未携带 `X-Proxy-Token`，或令牌错误/被删除 |
| 403 令牌已停用/过期 | 管理台检查启用状态与有效期 |
| 403 未授权的数据集 ds_xxx | 该令牌白名单未包含该数据集；在管理台补充后重试 |
| 403 无法确认归属/无法定位数据集 | 严格模式下 document/chat 反查失败，见下方版本说明 |
| 429 超过调用限额 | 调大该令牌的每分钟限流 |
| 502 无法连接 RAGFlow | 检查 `RAGFLOW_BASE_URL` 与容器到 RAGFlow 的网络连通性 |
| 上游返回 401 | `RAGFLOW_API_KEY` 错误或已失效 |

**RAGFlow 版本适配**：document/chat 的归属反查位于 `app/ragflow.py`，使用了
`GET /api/v1/chats/{chat_id}`（回退 `/api/v1/chats` 列表匹配）与
`GET /api/v1/datasets/{dataset_id}/documents?id=...`。若你的 RAGFlow 版本路径或返回字段不同，仅需调整该文件两个方法，鉴权与转发逻辑无需改动。
