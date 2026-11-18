# 轻量 Python 用户管理

当前运行版本使用 FastAPI、SQLite 和原生 HTML/JavaScript，不依赖 Django、Redis、Celery 或 Docker。

## 功能

- 用户：新增、编辑、单行或批量启用/禁用、删除、搜索、状态筛选、用户组筛选。
- 用户组：定义用户组并维护成员，支持一键加入全部用户或清空成员。
- 角色：定义角色并绑定用户。
- 权限：定义资源权限并绑定到角色。
- 查询：用户、用户组、角色、权限均提供 REST API 和独立 Web 页面，支持分页、排序、关联搜索和当前结果 CSV 导出。
- 页面：仪表盘、用户、用户组、角色、权限分别对应 `/ui/`、`/ui/users/`、`/ui/groups/`、`/ui/roles/`、`/ui/permissions/`，公共样式和脚本独立维护。
- 概览：提供数据覆盖率、最近新增用户、组/角色成员排行和快捷统计卡片。
- 性能：SQLite 启用 WAL、busy timeout 和关系索引；用户分页关联采用批量读取，减少数据库往返。

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
- `GET /api/summary`
- `GET /api/insights`
- `GET/POST/PATCH/DELETE /api/v1/users/users/`
- `PATCH /api/v1/users/users/{user_id}/status/`
- `GET/POST/PATCH/DELETE /api/v1/users/groups/`
- `POST /api/v1/users/groups/{group_id}/add-all-users/`
- `POST /api/v1/users/groups/{group_id}/remove-all-users/`
- `GET/POST/PATCH/DELETE /api/v1/rbac/roles/`
- `GET/POST/PATCH/DELETE /api/v1/rbac/permissions/`
- `GET/POST/DELETE /api/v1/users/users-groups-relations/`

原先下载的 JumpServer 源码仍保留在 `jumpserver/` 和 `jumpserver-lina/`，仅作为参考，不参与轻量版本运行。此前的 Go 版位于 `D:\codex\usermng-go-legacy-20261117`。

