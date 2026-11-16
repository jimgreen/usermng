# Python 用户、用户组与权限管理

当前工作区使用 [JumpServer](https://github.com/jumpserver/jumpserver)，已从 Go 版切换为 Python 版。JumpServer 是 Django/Python 开源 Web 平台，包含后端 API、Web 管理前端、RBAC 权限和审计功能。

- 后端：`D:\codex\usermng\jumpserver`
- Web 管理前端：`D:\codex\usermng\jumpserver-lina`

上游仓库：

- 后端：<https://github.com/jumpserver/jumpserver.git>
- 前端：<https://github.com/jumpserver/lina.git>

两个仓库当前均检出 `dev` 分支，许可证均为 GPLv3。

## 功能范围

登录管理端后，可以定义和查询：

| 功能 | 支持内容 |
| --- | --- |
| 用户 | 创建、编辑、启用/禁用、密码与 MFA、用户详情和登录审计 |
| 用户组/组织 | 创建组织和用户组、维护成员关系、按组织查询资源 |
| 角色 | 系统角色和组织角色、角色绑定、角色成员查询 |
| 权限 | 按资源定义查看/新增/修改/删除等权限，按角色授权，RBAC 校验 |
| 访问策略 | 资产、账号、命令、剪贴板等访问控制策略，以及授权关系查询 |
| 审计 | 用户登录、操作、会话和权限相关记录查询 |

后端 API 位于 `jumpserver/apps/*/api/`，用户、用户组和角色相关代码分别位于 `jumpserver/apps/users/`、`jumpserver/apps/orgs/` 和 `jumpserver/apps/rbac/`；Web 前端页面位于 `jumpserver-lina/src/`。

## 启动方式

JumpServer 依赖 PostgreSQL、Redis、Celery 等服务，当前源码要求 Python 3.14+；前端 `lina` 要求 Node.js 24 和 Yarn 4。官方推荐使用 Docker Compose 或 Linux 安装脚本。Windows 上建议安装 Docker Desktop，并在 WSL2/Linux 环境执行官方安装方式：

```bash
curl -sSL https://github.com/jumpserver/jumpserver/releases/latest/download/quick_start.sh | bash
```

安装完成后访问 `https://<服务器地址>/`，默认账号是 `admin`，默认密码是 `ChangeMe`；首次登录后必须立即修改密码。详细部署说明见 [官方中文 README](jumpserver/readmes/README.zh-hans.md) 和 [官方文档](https://jumpserver.com/docs)。

若需要从源码开发，依赖和 Python 配置见 [jumpserver/pyproject.toml](jumpserver/pyproject.toml)、[jumpserver/config_example.yml](jumpserver/config_example.yml) 以及 [官方开发文档](https://docs.jumpserver.org/)。

## 目录说明

- `jumpserver/`：Python 后端、API、模型和迁移。
- `jumpserver-lina/`：JumpServer Web 管理前端。
- `jumpserver/apps/users/`：用户和用户组模型、接口、序列化和查询过滤器。
- `jumpserver/apps/rbac/`：角色、权限、角色绑定和 RBAC 权限校验。
- `jumpserver/apps/orgs/`：组织隔离和组织成员关系。
- `jumpserver/apps/acls/`、`jumpserver/apps/perms/`：访问控制策略和授权关系。

此前下载的 Go 版已完整移到 `D:\codex\usermng-go-legacy-20261117`，需要回看时可从该目录恢复。

