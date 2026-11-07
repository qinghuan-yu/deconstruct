# Decision Arena

个人决策对话工具。原生 HTML/CSS/JavaScript 前端，FastAPI + SQLite 后端；采用纸白、衬线正文、细线分栏的报纸版面。

支持快速问答与深度审计、文档上传、实时阶段状态、最终答案流式输出、停止生成、刷新后恢复历史。生成任务在后端运行，关闭浏览器或切换对话不会取消任务。

## 本地运行

需要 Python 3.11 或更新版本。Docker 使用 Python 3.12。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://localhost:8000>，在右侧「模型配置」保存 API Key、Base URL 和模型名。未配置 Key 时使用确定性的本地演示回复。

也可复制 `.env.example` 为 `.env`，然后显式加载：

```powershell
.venv\Scripts\python -m uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

环境变量包括 `DEEPSEEK_API_KEY`（也接受 `OPENAI_API_KEY`）、`MODEL_BASE_URL`、`MODEL_NAME`、`DATA_DIR`。界面保存的 `data/model_settings.json` 优先于环境变量。Base URL 支持带或不带 `/v1`。

当前采用**单进程部署**：任务取消、会话互斥和事件通知在本进程内管理，不要启用多个 Uvicorn worker。需要多进程时应先引入共享任务协调。

## Docker

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

默认端口 `8000`，数据写入 `decision-arena-data` volume。浏览器运行依赖已保存在 `frontend/vendor/`，生产环境不需要 Node.js 或外部 CDN。

## 深度审计流程

1. **方案分析员**：提取目标、约束、依赖和待验证前提，读取有预算限制的原文节选。
2. **红队质询员与执行评估员并行**：两者基于同一份方案拆解独立分析。
3. **证据审计员**：检查前两者的数字、推断和矛盾，保留交叉核查依赖。
4. **决策证据链**：合并争议、证据状态和决策影响。
5. **裁判台账**：生成简短内部裁决。
6. **最终编辑器**：真正流式输出决策书。

共 7 次模型调用、6 个依赖层级。每步开始与完成都会推送状态；审计正文按完成步骤提供，默认折叠。最终正文和审计记录分别保存，不再依赖字符串标记分割新消息。旧版消息仍可显示。

每步保存耗时、输入／输出字符数，以及模型服务返回的 token 用量。调用配置为连接超时 15 秒、读超时 60 秒、整轮最长 360 秒。输出被长度限制截断、连接意外结束或返回空正文时，显示错误并保留已生成的最终正文。

## 上下文与附件

- 用户正文和附件以独立字段提交，正文中的 `[文档：...]` 不再作为新消息协议。
- 支持 TXT、Markdown、DOCX、DOC；DOC 是尽力提取，界面会提示优先转换为 DOCX。
- 单文件最大 8 MB，每次最多 4 份；单文档提取最多 12 万字符，总附件文字最多 24 万字符。
- DOCX 检查解压体积、文件数量和压缩比，使用 `defusedxml` 拒绝 DTD／实体声明。
- 原文保存在 SQLite 附件字段中。长文档使用首尾和问题相关片段，单份节选最多 4,000 字符、全部节选预算 12,000 字符；这是有界检索，不保证涵盖全文的所有细节。
- 历史正文预算 16,000 字符，保留最初用户约束和最近对话；后续轮次带最终答案及紧凑的背景归纳，不重复带完整审计台账。
- Expert 的原文节选只提供给方案分析员，下游使用分析结果。Quick 将相关原文节选作为资料上下文。

## 持久化与重连

SQLite 使用 WAL 与会话索引。首段立即写入，后续每 150 ms 或累积 1,024 字符合并写入；正文增量和对应事件在同一事务提交。

会话详情返回同一数据库快照下的 `messages` 与 `cursor`。SSE 从该游标继续，支持断线重连、重复事件去重及保留期外的快照恢复。进程内条件通知唤醒 SSE，空闲时每 15 秒保活，不再每 0.4 秒轮询。

同会话已有任务时返回 HTTP 409；`POST /api/conversations/{id}/cancel` 停止生成。删除会话先取消任务。启动时将遗留 `streaming` 消息标记为 `interrupted`。完成任务的增量事件保留 7 天，在启动时及运行期间按小时检查清理；消息与审计记录不受事件清理影响。

现有数据库在启动时自动增加字段和索引。升级前可使用 SQLite backup API 备份数据库，不要只复制运行中的主文件而漏掉 WAL。

## 开发与验证

```powershell
.venv\Scripts\python -X utf8 -m unittest discover -s tests -v
npm ci
npm run vendor
```

`npm run vendor` 从锁定版本复制 Marked、DOMPurify 及许可证；修改依赖后需重新运行。HTML 经过安全清理，模型输出不能执行脚本或嵌入远程图片。

浏览器测试使用 Playwright 和本机 Edge。先另开终端启动隔离演示服务：

```powershell
$env:DATA_DIR = "$PWD/.qa/browser"
$env:DEEPSEEK_API_KEY = ""
$env:OPENAI_API_KEY = ""
.venv\Scripts\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

然后运行 `npm run test:ui`。测试包含真实 HTTP/SSE 的上传、生成、刷新、取消，以及浏览器中的 Markdown、安全清理、进度、折叠、滚动、草稿保护和移动侧栏。

显式运行 `.venv\Scripts\python -X utf8 scripts/live_smoke.py` 会使用 `data/` 中的真实模型配置，发送脚本内的合成阅读计划问题，产生 API 费用。结果单独保存到 `.qa/`，不改动日常会话。

本次验收记录见 [docs/verification.md](docs/verification.md)。
