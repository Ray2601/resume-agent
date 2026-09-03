# 免费部署：Vercel + Render + Neon + DeepSeek

## 0. 前提

- GitHub 仓库：`https://github.com/Ray2601/resume-agent`
- Vercel Hobby：部署 `frontend/`
- Render Free：部署根目录 Python API
- Neon Free：持久保存 Web Run 与 Agent Trace
- DeepSeek：API 调用按 Token 收费

不要把 `.env`、数据库、真实简历或 API Key 提交到 Git。

## 1. 创建 Neon 数据库

1. 在 Neon 创建 Free Project。
2. 复制带 `sslmode=require` 的 PostgreSQL connection string。
3. 不需要手动建表；Render 第一次保存结果时自动创建。

## 2. 部署 Render 后端

1. Render → **New → Blueprint**，连接 GitHub 仓库。
2. Render 会读取根目录 `render.yaml`。
3. 配置 Secret：

| 变量 | 值 |
|---|---|
| `API_KEY` | DeepSeek API Key |
| `DATABASE_URL` | Neon connection string |
| `APP_ACCESS_TOKEN` | 自己生成的长随机口令 |
| `FRONTEND_ORIGIN` | 首次可填 `*`，Vercel 部署后换成完整域名 |

4. 等待部署，通过 `https://<service>.onrender.com/health` 验证。

Free 实例空闲后会休眠，首次请求可能需要约一分钟唤醒。任务在单进程后台线程执行，适合作品集演示；实例重启会丢失尚未完成的 queued/running 任务，但完成结果已保存到 Neon。

## 3. 部署 Vercel 前端

1. Vercel → **Add New Project**，导入同一仓库。
2. Root Directory 设置为 `frontend`。
3. Framework Preset 选 `Other`，不需要 Build Command。
4. 添加环境变量：

```env
API_BASE_URL=https://<service>.onrender.com
```

5. 部署，得到 `https://<project>.vercel.app`。
6. 回到 Render，把 `FRONTEND_ORIGIN` 改成上述完整地址并重新部署。
7. 打开页面，输入 Render 中设置的 `APP_ACCESS_TOKEN` 后运行。

## 4. 本地验证云 API

```powershell
pip install -r requirements.txt
$env:DATABASE_URL="你的 Neon URL"
$env:API_KEY="你的 DeepSeek Key"
$env:APP_ACCESS_TOKEN="你的访问口令"
uvicorn api.main:app --reload
```

健康检查：`http://127.0.0.1:8000/health`。

## 免费额度注意事项

- Vercel Hobby 只用于个人、非商业项目。
- Render Free 会休眠且没有持久磁盘。
- Neon 使用免费额度，数据库连接应保留 SSL 参数。
- 不要把 Batch Evaluation 暴露成公共 API；20 Case 实验会产生大量 DeepSeek Token 成本。
