# 轻量 Python 用户管理

当前运行版本使用 FastAPI、SQLite 和原生 HTML/JavaScript，不依赖 Django、Redis、Celery 或 Docker。

## 功能

- 用户：新增、编辑、启用/禁用、删除、搜索。
- 用户组：定义用户组并维护成员。
- 角色：定义角色并绑定用户。
- 权限：定义资源权限并绑定到角色。
- 查询：用户、用户组、角色、权限均提供 REST API 和 Web 页面。

## 启动

```powershell
cd D:\codex\usermng
python -m uvicorn simple_server:app --host 127.0.0.1 --port 9529
```

打开 <http://127.0.0.1:9529/ui/>。

默认数据库文件为：

`D:\codex\sqldb\realtime\usermng\usermng`

也可以通过 `USERMNG_DB_PATH` 环境变量指定其他 SQLite 文件。首次启动会自动创建表和一个本地 `admin` 用户、Administrators 用户组及 Administrator 角色。

## API

- `GET /api/health`
- `GET/POST/PATCH/DELETE /api/v1/users/users/`
- `GET/POST/PATCH/DELETE /api/v1/users/groups/`
- `GET/POST/PATCH/DELETE /api/v1/rbac/roles/`
- `GET/POST/PATCH/DELETE /api/v1/rbac/permissions/`
- `GET/POST/DELETE /api/v1/users/users-groups-relations/`

原先下载的 JumpServer 源码仍保留在 `jumpserver/` 和 `jumpserver-lina/`，仅作为参考，不参与轻量版本运行。此前的 Go 版位于 `D:\codex\usermng-go-legacy-20261117`。

