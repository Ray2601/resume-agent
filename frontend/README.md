# Vercel frontend

在 Vercel 导入同一 GitHub 仓库，将 **Root Directory** 设置为 `frontend`，并配置：

```env
API_BASE_URL=https://your-render-service.onrender.com
```

部署完成后，把 Vercel 域名写入 Render 的 `FRONTEND_ORIGIN`。
