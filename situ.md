# Resume Agent 当前故障分析

## 结论

当前不是 Neon 数据已经丢失，而是新功能代码没有进入线上部署，再叠加本地预览 API 进程配置不稳定。

线上两端目前表现为：

- Render API 健康检查正常：`https://resume-agent-2pgf.onrender.com/health` 返回 HTTP 200 和 `{"status":"ok"}`。
- Vercel 页面正常打开：`https://resume-agent-phi.vercel.app/` 返回 HTTP 200。
- Vercel 的 `/api/config` 已正确指向 Render API。
- 但线上 Vercel 页面仍是旧版精简页面，只有 JD、已有经历、岗位分类和访问口令。
- 线上 `/app.js` 返回 404，说明完整功能对应的新前端脚本尚未部署。
- 因此线上仍没有完整材料控件、session 历史入口和新增字段提交逻辑。

## “后台监控不了数据”到底是什么意思

是的，当前线上版本基本无法提供新设计的后台监控，但不是因为 Neon 本身不可用。

旧线上 API 只有：

- `POST /api/runs`
- `GET /api/runs/{job_id}`
- `GET /health`

旧版没有：

- `GET /api/sessions/{session_id}/runs`
- 完整请求参数持久化
- queued/running/failed 状态的完整 Neon 记录
- iteration history
- Badcase 独立记录
- Prompt Version 的完整历史查询

所以 Render 进程能活着，只代表 API 进程可访问，不代表新监控链路已经部署。Neon 中即使有表，线上旧代码也不会自动写入新增字段；Vercel 页面也没有历史查询入口。

本地代码已经补充：

- `deploy_runs`
- `deploy_agent_traces`
- `deploy_badcases`
- `GET /api/sessions/{session_id}/runs`
- session 隔离查询
- 请求参数、Prompt Version、Eval、迭代历史和 Badcase 写入 Neon

## “Failed to fetch” 的原因

这个错误发生在浏览器网络层，表示前端没有拿到 API 的 HTTP 响应；它不是 DeepSeek 的业务错误，也不是 Pipeline 的评分错误。

本地预览中存在几个叠加风险：

### 1. API 进程可能不是使用新环境启动的

本地曾经有旧的 Python/uvicorn 进程占用 8000 端口。后续启动新进程时，如果端口已被占用，新进程不会真正监听，浏览器仍然访问旧进程。

### 2. 本地缺少 psycopg

本机最初没有安装 Neon 驱动：

```
ModuleNotFoundError: No module named 'psycopg'
```

因此 `/health` 这种不访问数据库的接口可以返回 200，但历史查询和写入 Neon 的接口会返回 500，浏览器可能只显示笼统的 `Failed to fetch`。

### 3. localhost 与 127.0.0.1 是不同 Origin

例如页面从 `http://localhost:8089` 打开，而 API 的 CORS 只允许 `http://127.0.0.1:8089`，浏览器会阻止请求。当前代码已经支持逗号分隔的明确白名单：

```
FRONTEND_ORIGIN=http://127.0.0.1:8089,http://localhost:8089
```

### 4. 纯静态服务器没有 Vercel 的 /api/config

本地使用 `python -m http.server` 时，`/api/config` 不存在。前端已经增加 localhost 回退，找不到该接口时使用 `http://127.0.0.1:8000`；生产环境仍使用 Vercel 的 `API_BASE_URL`。

## 线上与本地问题的区别

| 问题 | 主要原因 |
|---|---|
| 线上页面没有完整控件 | 新前端尚未部署，线上仍是旧 HTML |
| 线上没有 session 历史监控 | 新 API 和历史接口尚未部署 |
| 本地点击 Failed to fetch | 本地 API 进程、CORS、psycopg 或端口配置不一致 |
| Render /health 正常 | 只能证明旧 API 进程存活，不是完整功能验证 |

## 当前代码状态

本地提交：

```
3b91240 feat: restore full resume optimization web flow
```

该提交包含完整 Vercel 输入界面、文件读取、全部优化参数、Render API 参数传递、Pipeline 调用、Neon 持久化和 session 历史查询。

测试结果：

```
34 passed
frontend file merge tests passed
/health -> 200 OK
```

## 正确处理顺序

1. 确保本地只运行一个带完整环境变量的 API 进程。
2. 确认本地 `/health` 和历史接口都能访问。
3. 从本地页面填写完整参数，确认请求包含全部字段。
4. 推送提交 `3b91240` 到 GitHub main。
5. 等待 Vercel 和 Render 自动部署。
6. 重新检查 Vercel 页面、Render API、运行查询和 session 历史查询。
7. 检查 Neon 的三张表是否有对应记录。

## 安全事项

DeepSeek Key、访问口令和 Neon 连接串已经出现在聊天记录中。验证完成后必须轮换 DeepSeek API Key、APP_ACCESS_TOKEN 和 Neon 数据库密码。它们不能写入代码、前端、Git、日志或 `NEXT_PUBLIC_*` / `VITE_*` 变量。

