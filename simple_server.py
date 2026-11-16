"""Small, dependency-light user and RBAC management service.

This replaces the heavyweight JumpServer runtime for the local user-management
use case.  It intentionally keeps the API and the UI in one process:
FastAPI serves the REST API and the static HTML page, while sqlite3 stores all
data in the user-selected database file.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware


ROOT = Path(__file__).resolve().parent
WEB_FILE = ROOT / "web" / "index.html"
DB_PATH = Path(
    os.getenv(
        "USERMNG_DB_PATH",
        r"D:\codex\sqldb\realtime\usermng\usermng",
    )
)

app = FastAPI(title="User Management", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_groups (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);
"""


def ensure_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO users (id, username, name, comment) "
            "VALUES (1, 'admin', 'Administrator', 'Local administrator')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO groups (id, name, comment) "
            "VALUES (1, 'Administrators', 'Built-in management group')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO roles (id, name, comment) "
            "VALUES (1, 'Administrator', 'Full user-management access')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (1, 1)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (1, 1)"
        )


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    ensure_database()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def id_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        raise HTTPException(400, "关联 ID 必须是数组或逗号分隔字符串")
    try:
        return sorted({int(item) for item in value})
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "关联 ID 必须是整数") from exc


def replace_links(
    connection: sqlite3.Connection,
    table: str,
    left_column: str,
    left_id: int,
    right_column: str,
    right_ids: list[int],
) -> None:
    allowed = {"user_groups", "user_roles", "role_permissions"}
    if table not in allowed:
        raise ValueError(table)
    connection.execute(f"DELETE FROM {table} WHERE {left_column} = ?", (left_id,))
    if right_ids:
        connection.executemany(
            f"INSERT OR IGNORE INTO {table} ({left_column}, {right_column}) VALUES (?, ?)",
            [(left_id, right_id) for right_id in right_ids],
        )


def relation_ids(connection: sqlite3.Connection, table: str, left_column: str, right_column: str, left_id: int) -> list[int]:
    return [
        row[0]
        for row in connection.execute(
            f"SELECT {right_column} FROM {table} WHERE {left_column} = ? ORDER BY {right_column}",
            (left_id,),
        ).fetchall()
    ]


def user_record(connection: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    result = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if result is None:
        return None
    item = dict(result)
    item["is_active"] = bool(item["is_active"])
    item["groups"] = rows(
        connection,
        "SELECT g.id, g.name FROM groups g JOIN user_groups ug ON ug.group_id = g.id "
        "WHERE ug.user_id = ? ORDER BY g.name",
        (user_id,),
    )
    item["roles"] = rows(
        connection,
        "SELECT r.id, r.name FROM roles r JOIN user_roles ur ON ur.role_id = r.id "
        "WHERE ur.user_id = ? ORDER BY r.name",
        (user_id,),
    )
    return item


def page(items: list[dict[str, Any]], count: int, page_number: int, page_size: int) -> dict[str, Any]:
    return {
        "count": count,
        "page": page_number,
        "page_size": page_size,
        "results": items,
        "next": page_number * page_size < count,
        "previous": page_number > 1,
    }


def save_error(exc: sqlite3.IntegrityError) -> HTTPException:
    message = "名称或用户名已存在" if "UNIQUE" in str(exc).upper() else "数据保存失败"
    return HTTPException(409, message)


@app.on_event("startup")
def startup() -> None:
    ensure_database()


@app.get("/", include_in_schema=False)
@app.get("/ui/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_FILE)


@app.get("/api/health")
def health() -> dict[str, Any]:
    ensure_database()
    return {"status": "ok", "database": str(DB_PATH), "engine": "sqlite3"}


@app.get("/api/v1/users/profile/")
def profile() -> dict[str, Any]:
    return {"id": 1, "username": "admin", "name": "Administrator", "is_superuser": True}


@app.get("/api/v1/users/users/")
def list_users(
    search: str = "",
    group_id: int | None = None,
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, alias="page_size", ge=1, le=500),
) -> dict[str, Any]:
    with db() as connection:
        where = ""
        params: list[Any] = []
        if search:
            where = "WHERE u.username LIKE ? OR u.name LIKE ? OR u.email LIKE ?"
            token = f"%{search}%"
            params.extend([token, token, token])
        if group_id is not None:
            where += " AND " if where else "WHERE "
            where += "EXISTS (SELECT 1 FROM user_groups ug WHERE ug.user_id = u.id AND ug.group_id = ?)"
            params.append(group_id)
        count = connection.execute(f"SELECT COUNT(*) FROM users u {where}", tuple(params)).fetchone()[0]
        offset = (page_number - 1) * page_size
        ids = rows(
            connection,
            f"SELECT u.id FROM users u {where} ORDER BY u.name, u.id LIMIT ? OFFSET ?",
            tuple(params + [page_size, offset]),
        )
        return page(
            [user_record(connection, item["id"]) for item in ids],
            count,
            page_number,
            page_size,
        )


@app.post("/api/v1/users/users/", status_code=201)
def create_user(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    username = str(payload.get("username", "")).strip()
    name = str(payload.get("name", username)).strip()
    if not username or not name:
        raise HTTPException(400, "username 和 name 不能为空")
    try:
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, name, email, is_active, comment) VALUES (?, ?, ?, ?, ?)",
                (username, name, str(payload.get("email", "")), int(bool(payload.get("is_active", True))), str(payload.get("comment", ""))),
            )
            user_id = cursor.lastrowid
            replace_links(connection, "user_groups", "user_id", user_id, "group_id", id_list(payload.get("groups")))
            replace_links(connection, "user_roles", "user_id", user_id, "role_id", id_list(payload.get("roles")))
            return user_record(connection, user_id) or {}
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.get("/api/v1/users/users/{user_id}/")
def get_user(user_id: int) -> dict[str, Any]:
    with db() as connection:
        item = user_record(connection, user_id)
        if item is None:
            raise HTTPException(404, "用户不存在")
        return item


@app.patch("/api/v1/users/users/{user_id}/")
@app.put("/api/v1/users/users/{user_id}/")
def update_user(user_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    fields = {key: payload[key] for key in ("username", "name", "email", "comment") if key in payload}
    if "is_active" in payload:
        fields["is_active"] = int(bool(payload["is_active"]))
    try:
        with db() as connection:
            if not connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                raise HTTPException(404, "用户不存在")
            if fields:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                connection.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id))
            if "groups" in payload:
                replace_links(connection, "user_groups", "user_id", user_id, "group_id", id_list(payload["groups"]))
            if "roles" in payload:
                replace_links(connection, "user_roles", "user_id", user_id, "role_id", id_list(payload["roles"]))
            return user_record(connection, user_id) or {}
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.delete("/api/v1/users/users/{user_id}/", status_code=204)
def delete_user(user_id: int) -> None:
    with db() as connection:
        if connection.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount == 0:
            raise HTTPException(404, "用户不存在")


@app.get("/api/v1/users/groups/")
def list_groups(search: str = "") -> dict[str, Any]:
    with db() as connection:
        token = f"%{search}%"
        items = rows(
            connection,
            "SELECT g.*, COUNT(ug.user_id) AS users_count FROM groups g "
            "LEFT JOIN user_groups ug ON ug.group_id = g.id "
            "WHERE g.name LIKE ? GROUP BY g.id ORDER BY g.name",
            (token,),
        )
        for item in items:
            item["users"] = relation_ids(connection, "user_groups", "group_id", "user_id", item["id"])
        return {"count": len(items), "results": items}


@app.get("/api/v1/users/groups/{group_id}/")
def get_group(group_id: int) -> dict[str, Any]:
    with db() as connection:
        result = connection.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if result is None:
            raise HTTPException(404, "用户组不存在")
        item = dict(result)
        item["users"] = relation_ids(connection, "user_groups", "group_id", "user_id", group_id)
        return item


@app.post("/api/v1/users/groups/", status_code=201)
def create_group(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "用户组名称不能为空")
    try:
        with db() as connection:
            group_id = connection.execute(
                "INSERT INTO groups (name, comment) VALUES (?, ?)",
                (name, str(payload.get("comment", ""))),
            ).lastrowid
            replace_links(connection, "user_groups", "group_id", group_id, "user_id", id_list(payload.get("users")))
            result = connection.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
            item = dict(result)
            item["users"] = id_list(payload.get("users"))
            return item
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.patch("/api/v1/users/groups/{group_id}/")
@app.put("/api/v1/users/groups/{group_id}/")
def update_group(group_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        with db() as connection:
            if not connection.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone():
                raise HTTPException(404, "用户组不存在")
            fields = {key: payload[key] for key in ("name", "comment") if key in payload}
            if fields:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                connection.execute(f"UPDATE groups SET {assignments} WHERE id = ?", (*fields.values(), group_id))
            if "users" in payload:
                replace_links(connection, "user_groups", "group_id", group_id, "user_id", id_list(payload["users"]))
            result = connection.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
            item = dict(result)
            item["users"] = relation_ids(connection, "user_groups", "group_id", "user_id", group_id)
            return item
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.delete("/api/v1/users/groups/{group_id}/", status_code=204)
def delete_group(group_id: int) -> None:
    with db() as connection:
        if connection.execute("DELETE FROM groups WHERE id = ?", (group_id,)).rowcount == 0:
            raise HTTPException(404, "用户组不存在")


@app.get("/api/v1/rbac/roles/")
def list_roles(search: str = "") -> dict[str, Any]:
    with db() as connection:
        token = f"%{search}%"
        items = rows(
            connection,
            "SELECT r.*, COUNT(DISTINCT rp.permission_id) AS permissions_count, "
            "COUNT(DISTINCT ur.user_id) AS users_count FROM roles r "
            "LEFT JOIN role_permissions rp ON rp.role_id = r.id "
            "LEFT JOIN user_roles ur ON ur.role_id = r.id WHERE r.name LIKE ? GROUP BY r.id ORDER BY r.name",
            (token,),
        )
        for item in items:
            item["permissions"] = relation_ids(connection, "role_permissions", "role_id", "permission_id", item["id"])
            item["users"] = relation_ids(connection, "user_roles", "role_id", "user_id", item["id"])
        return {"count": len(items), "results": items}


@app.post("/api/v1/rbac/roles/", status_code=201)
def create_role(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "角色名称不能为空")
    try:
        with db() as connection:
            role_id = connection.execute(
                "INSERT INTO roles (name, comment) VALUES (?, ?)",
                (name, str(payload.get("comment", ""))),
            ).lastrowid
            replace_links(connection, "role_permissions", "role_id", role_id, "permission_id", id_list(payload.get("permissions")))
            replace_links(connection, "user_roles", "role_id", role_id, "user_id", id_list(payload.get("users")))
            item = dict(connection.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone())
            item["permissions"] = id_list(payload.get("permissions"))
            item["users"] = id_list(payload.get("users"))
            return item
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.patch("/api/v1/rbac/roles/{role_id}/")
@app.put("/api/v1/rbac/roles/{role_id}/")
def update_role(role_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        with db() as connection:
            if not connection.execute("SELECT 1 FROM roles WHERE id = ?", (role_id,)).fetchone():
                raise HTTPException(404, "角色不存在")
            fields = {key: payload[key] for key in ("name", "comment") if key in payload}
            if fields:
                assignments = ", ".join(f"{key} = ?" for key in fields)
                connection.execute(f"UPDATE roles SET {assignments} WHERE id = ?", (*fields.values(), role_id))
            if "permissions" in payload:
                replace_links(connection, "role_permissions", "role_id", role_id, "permission_id", id_list(payload["permissions"]))
            if "users" in payload:
                replace_links(connection, "user_roles", "role_id", role_id, "user_id", id_list(payload["users"]))
            item = dict(connection.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone())
            item["permissions"] = relation_ids(connection, "role_permissions", "role_id", "permission_id", role_id)
            item["users"] = relation_ids(connection, "user_roles", "role_id", "user_id", role_id)
            return item
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.delete("/api/v1/rbac/roles/{role_id}/", status_code=204)
def delete_role(role_id: int) -> None:
    with db() as connection:
        if connection.execute("DELETE FROM roles WHERE id = ?", (role_id,)).rowcount == 0:
            raise HTTPException(404, "角色不存在")


@app.get("/api/v1/rbac/permissions/")
def list_permissions(search: str = "") -> dict[str, Any]:
    with db() as connection:
        token = f"%{search}%"
        items = rows(
            connection,
            "SELECT * FROM permissions WHERE code LIKE ? OR name LIKE ? OR resource LIKE ? "
            "ORDER BY resource, action, code",
            (token, token, token),
        )
        return {"count": len(items), "results": items}


@app.post("/api/v1/rbac/permissions/", status_code=201)
def create_permission(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    code = str(payload.get("code", "")).strip()
    name = str(payload.get("name", code)).strip()
    if not code or not name:
        raise HTTPException(400, "权限编码和名称不能为空")
    try:
        with db() as connection:
            permission_id = connection.execute(
                "INSERT INTO permissions (code, name, resource, action) VALUES (?, ?, ?, ?)",
                (code, name, str(payload.get("resource", "")), str(payload.get("action", ""))),
            ).lastrowid
            return dict(connection.execute("SELECT * FROM permissions WHERE id = ?", (permission_id,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise save_error(exc) from exc


@app.patch("/api/v1/rbac/permissions/{permission_id}/")
def update_permission(permission_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with db() as connection:
        if not connection.execute("SELECT 1 FROM permissions WHERE id = ?", (permission_id,)).fetchone():
            raise HTTPException(404, "权限不存在")
        fields = {key: payload[key] for key in ("code", "name", "resource", "action") if key in payload}
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            connection.execute(f"UPDATE permissions SET {assignments} WHERE id = ?", (*fields.values(), permission_id))
        return dict(connection.execute("SELECT * FROM permissions WHERE id = ?", (permission_id,)).fetchone())


@app.delete("/api/v1/rbac/permissions/{permission_id}/", status_code=204)
def delete_permission(permission_id: int) -> None:
    with db() as connection:
        if connection.execute("DELETE FROM permissions WHERE id = ?", (permission_id,)).rowcount == 0:
            raise HTTPException(404, "权限不存在")


@app.get("/api/v1/users/users-groups-relations/")
def list_user_group_relations(usergroup: int | None = None, user: int | None = None) -> dict[str, Any]:
    with db() as connection:
        where: list[str] = []
        params: list[Any] = []
        if usergroup is not None:
            where.append("ug.group_id = ?")
            params.append(usergroup)
        if user is not None:
            where.append("ug.user_id = ?")
            params.append(user)
        condition = f"WHERE {' AND '.join(where)}" if where else ""
        items = rows(
            connection,
            "SELECT ug.user_id AS user, u.name AS user_display, ug.group_id AS usergroup, g.name AS usergroup_display "
            "FROM user_groups ug JOIN users u ON u.id = ug.user_id JOIN groups g ON g.id = ug.group_id " + condition,
            tuple(params),
        )
        return {"count": len(items), "results": items}


@app.post("/api/v1/users/users-groups-relations/", status_code=201)
def add_user_group_relations(payload: Any = Body(...)) -> dict[str, Any]:
    items = payload if isinstance(payload, list) else [payload]
    with db() as connection:
        for item in items:
            connection.execute(
                "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)",
                (int(item["user"]), int(item["usergroup"])),
            )
        return {"count": len(items)}


@app.delete("/api/v1/users/users-groups-relations/", status_code=204)
def remove_user_group_relation(usergroup: int, user: int) -> None:
    with db() as connection:
        connection.execute("DELETE FROM user_groups WHERE user_id = ? AND group_id = ?", (user, usergroup))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("simple_server:app", host="127.0.0.1", port=int(os.getenv("PORT", "9529")), reload=False)
