"""Small, dependency-light user and RBAC management service.

This replaces the heavyweight JumpServer runtime for the local user-management
use case.  It intentionally keeps the API and the UI in one process:
FastAPI serves the REST API and the static HTML page, while sqlite3 stores all
data in the user-selected database file.
"""

from __future__ import annotations

import os
import sqlite3
import csv
import io
from threading import Lock
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware


ROOT = Path(__file__).resolve().parent
WEB_FILE = ROOT / "web" / "index.html"
WEB_DIR = ROOT / "web"
PAGE_FILES = {
    "users": WEB_DIR / "users.html",
    "groups": WEB_DIR / "groups.html",
    "roles": WEB_DIR / "roles.html",
    "permissions": WEB_DIR / "permissions.html",
}
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
CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);
CREATE INDEX IF NOT EXISTS idx_groups_name ON groups(name);
CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name);
CREATE INDEX IF NOT EXISTS idx_permissions_resource_action ON permissions(resource, action);
CREATE INDEX IF NOT EXISTS idx_user_groups_group_user ON user_groups(group_id, user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_role_user ON user_roles(role_id, user_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_permission_role ON role_permissions(permission_id, role_id);
"""

_DATABASE_READY = False
_DATABASE_INIT_LOCK = Lock()


def ensure_database() -> None:
    global _DATABASE_READY
    if _DATABASE_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DATABASE_INIT_LOCK:
        if _DATABASE_READY:
            return
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
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
        _DATABASE_READY = True


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    ensure_database()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
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


def user_records(connection: sqlite3.Connection, user_ids: list[int]) -> list[dict[str, Any]]:
    """Load a page of users and both relationship sets with three queries."""
    if not user_ids:
        return []
    placeholders = ",".join("?" for _ in user_ids)
    users = {
        item["id"]: item
        for item in rows(connection, f"SELECT * FROM users WHERE id IN ({placeholders})", tuple(user_ids))
    }
    for item in users.values():
        item["is_active"] = bool(item["is_active"])
        item["groups"] = []
        item["roles"] = []
    for row in rows(
        connection,
        f"SELECT ug.user_id, g.id, g.name FROM user_groups ug JOIN groups g ON g.id = ug.group_id WHERE ug.user_id IN ({placeholders}) ORDER BY g.name",
        tuple(user_ids),
    ):
        users[row["user_id"]]["groups"].append({"id": row["id"], "name": row["name"]})
    for row in rows(
        connection,
        f"SELECT ur.user_id, r.id, r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id IN ({placeholders}) ORDER BY r.name",
        tuple(user_ids),
    ):
        users[row["user_id"]]["roles"].append({"id": row["id"], "name": row["name"]})
    return [users[user_id] for user_id in user_ids if user_id in users]


def delete_many(connection: sqlite3.Connection, table: str, ids: list[int]) -> int:
    if table not in {"users", "groups", "roles", "permissions"}:
        raise ValueError(table)
    placeholders = ",".join("?" for _ in ids)
    return connection.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", tuple(ids)).rowcount


def page(items: list[dict[str, Any]], count: int, page_number: int, page_size: int) -> dict[str, Any]:
    return {
        "count": count,
        "page": page_number,
        "page_size": page_size,
        "results": items,
        "next": page_number * page_size < count,
        "previous": page_number > 1,
    }


def paging(page_number: int, page_size: int) -> tuple[int, int]:
    """Return a safe offset/limit pair for all list endpoints."""
    return (page_number - 1) * page_size, page_size


def sort_clause(value: str, allowed: set[str], default: str = "name") -> str:
    """Build a SQL sort expression from a small allow-list."""
    field = value.lstrip("-")
    if field not in allowed:
        field = default
    direction = "DESC" if value.startswith("-") else "ASC"
    return f"{field} {direction}"


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


@app.get("/ui/users/", include_in_schema=False)
def users_page() -> FileResponse:
    return FileResponse(PAGE_FILES["users"])


@app.get("/ui/groups/", include_in_schema=False)
def groups_page() -> FileResponse:
    return FileResponse(PAGE_FILES["groups"])


@app.get("/ui/roles/", include_in_schema=False)
def roles_page() -> FileResponse:
    return FileResponse(PAGE_FILES["roles"])


@app.get("/ui/permissions/", include_in_schema=False)
def permissions_page() -> FileResponse:
    return FileResponse(PAGE_FILES["permissions"])


@app.get("/ui/styles.css", include_in_schema=False)
def styles() -> Response:
    return FileResponse(WEB_DIR / "styles.css", media_type="text/css")


@app.get("/ui/app.js", include_in_schema=False)
def app_script() -> Response:
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")


@app.get("/api/health")
def health() -> dict[str, Any]:
    ensure_database()
    return {"status": "ok", "database": str(DB_PATH), "engine": "sqlite3"}


@app.get("/api/summary")
def summary() -> dict[str, int]:
    """Compact counters used by the dashboard cards."""
    with db() as connection:
        values = {
            "users": connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "active_users": connection.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0],
            "groups": connection.execute("SELECT COUNT(*) FROM groups").fetchone()[0],
            "roles": connection.execute("SELECT COUNT(*) FROM roles").fetchone()[0],
            "permissions": connection.execute("SELECT COUNT(*) FROM permissions").fetchone()[0],
            "memberships": connection.execute("SELECT COUNT(*) FROM user_groups").fetchone()[0],
        }
        return values


@app.get("/api/insights")
def insights() -> dict[str, Any]:
    """Return compact dashboard metrics without loading full resource pages."""
    with db() as connection:
        total_users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assigned_groups = connection.execute("SELECT COUNT(DISTINCT user_id) FROM user_groups").fetchone()[0]
        assigned_roles = connection.execute("SELECT COUNT(DISTINCT user_id) FROM user_roles").fetchone()[0]
        groups = rows(
            connection,
            "SELECT g.name, COUNT(ug.user_id) AS users_count FROM groups g "
            "LEFT JOIN user_groups ug ON ug.group_id = g.id GROUP BY g.id ORDER BY users_count DESC, g.name LIMIT 5",
        )
        roles = rows(
            connection,
            "SELECT r.name, COUNT(DISTINCT ur.user_id) AS users_count, "
            "COUNT(DISTINCT rp.permission_id) AS permissions_count FROM roles r "
            "LEFT JOIN user_roles ur ON ur.role_id = r.id "
            "LEFT JOIN role_permissions rp ON rp.role_id = r.id "
            "GROUP BY r.id ORDER BY users_count DESC, r.name LIMIT 5",
        )
        recent_users = rows(
            connection,
            "SELECT id, username, name, is_active, created_at FROM users ORDER BY created_at DESC, id DESC LIMIT 5",
        )
        return {
            "coverage": {
                "users_with_group": assigned_groups,
                "users_without_group": max(total_users - assigned_groups, 0),
                "users_with_role": assigned_roles,
                "users_without_role": max(total_users - assigned_roles, 0),
            },
            "top_groups": groups,
            "top_roles": roles,
            "recent_users": recent_users,
        }


@app.get("/api/v1/users/profile/")
def profile() -> dict[str, Any]:
    return {"id": 1, "username": "admin", "name": "Administrator", "is_superuser": True}


@app.get("/api/v1/users/users/")
def list_users(
    search: str = "",
    group_id: int | None = None,
    is_active: bool | None = None,
    sort: str = "name",
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, alias="page_size", ge=1, le=500),
) -> dict[str, Any]:
    with db() as connection:
        where = ""
        params: list[Any] = []
        if search:
            where = "WHERE (u.username LIKE ? OR u.name LIKE ? OR u.email LIKE ? OR u.comment LIKE ?)"
            token = f"%{search}%"
            params.extend([token, token, token, token])
        if group_id is not None:
            where += " AND " if where else "WHERE "
            where += "EXISTS (SELECT 1 FROM user_groups ug WHERE ug.user_id = u.id AND ug.group_id = ?)"
            params.append(group_id)
        if is_active is not None:
            where += " AND " if where else "WHERE "
            where += "u.is_active = ?"
            params.append(int(is_active))
        count = connection.execute(f"SELECT COUNT(*) FROM users u {where}", tuple(params)).fetchone()[0]
        offset, limit = paging(page_number, page_size)
        order = sort_clause(sort, {"name", "username", "email", "created_at", "is_active"})
        ids = rows(
            connection,
            f"SELECT u.id FROM users u {where} ORDER BY u.{order}, u.id LIMIT ? OFFSET ?"
            if order.split()[0] in {"name", "username", "email", "created_at", "is_active"}
            else f"SELECT u.id FROM users u {where} ORDER BY u.name, u.id LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        )
        return page(
            user_records(connection, [item["id"] for item in ids]),
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


@app.patch("/api/v1/users/users/")
def bulk_update_users(payload: Any = Body(...)) -> dict[str, Any]:
    """Update active state for several users in one request."""
    items = payload if isinstance(payload, list) else [payload]
    changed = 0
    with db() as connection:
        for item in items:
            if not isinstance(item, dict) or "id" not in item:
                raise HTTPException(400, "批量更新需要 id")
            fields = {}
            if "is_active" in item:
                fields["is_active"] = int(bool(item["is_active"]))
            if "comment" in item:
                fields["comment"] = str(item["comment"])
            if not fields:
                continue
            assignments = ", ".join(f"{key} = ?" for key in fields)
            cursor = connection.execute(
                f"UPDATE users SET {assignments} WHERE id = ?",
                (*fields.values(), int(item["id"])),
            )
            changed += cursor.rowcount
    return {"count": changed}


@app.delete("/api/v1/users/users/", status_code=204)
def bulk_delete_users(ids: str = Query(..., description="逗号分隔的用户 ID")) -> None:
    user_ids = id_list(ids)
    if not user_ids:
        raise HTTPException(400, "至少选择一个用户")
    with db() as connection:
        delete_many(connection, "users", user_ids)


@app.patch("/api/v1/users/users/{user_id}/status/")
def update_user_status(user_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with db() as connection:
        if not connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise HTTPException(404, "用户不存在")
        if "is_active" not in payload:
            raise HTTPException(400, "is_active 不能为空")
        connection.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(bool(payload["is_active"])), user_id))
        return user_record(connection, user_id) or {}


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
def list_groups(
    search: str = "",
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, alias="page_size", ge=1, le=500),
    sort: str = "name",
) -> dict[str, Any]:
    with db() as connection:
        token = f"%{search}%"
        where = "WHERE g.name LIKE ? OR g.comment LIKE ?"
        params = (token, token)
        count = connection.execute("SELECT COUNT(*) FROM groups g " + where, params).fetchone()[0]
        offset, limit = paging(page_number, page_size)
        order = sort_clause(sort, {"name", "created_at"})
        items = rows(
            connection,
            "SELECT g.*, COUNT(ug.user_id) AS users_count FROM groups g "
            "LEFT JOIN user_groups ug ON ug.group_id = g.id "
            f"{where} GROUP BY g.id ORDER BY g.{order}, g.id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        for item in items:
            item["users"] = relation_ids(connection, "user_groups", "group_id", "user_id", item["id"])
        return page(items, count, page_number, page_size)


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


@app.delete("/api/v1/users/groups/", status_code=204)
def bulk_delete_groups(ids: str = Query(..., description="逗号分隔的用户组 ID")) -> None:
    group_ids = id_list(ids)
    if not group_ids:
        raise HTTPException(400, "至少选择一个用户组")
    with db() as connection:
        delete_many(connection, "groups", group_ids)


@app.post("/api/v1/users/groups/{group_id}/add-all-users/")
def add_all_users_to_group(group_id: int) -> dict[str, int]:
    with db() as connection:
        if not connection.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "用户组不存在")
        connection.execute(
            "INSERT OR IGNORE INTO user_groups (user_id, group_id) SELECT id, ? FROM users",
            (group_id,),
        )
        count = connection.execute("SELECT COUNT(*) FROM user_groups WHERE group_id = ?", (group_id,)).fetchone()[0]
        return {"count": count}


@app.post("/api/v1/users/groups/{group_id}/remove-all-users/")
def remove_all_users_from_group(group_id: int) -> dict[str, int]:
    with db() as connection:
        if not connection.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "用户组不存在")
        removed = connection.execute("DELETE FROM user_groups WHERE group_id = ?", (group_id,)).rowcount
        return {"count": removed}


@app.get("/api/v1/rbac/roles/")
def list_roles(
    search: str = "",
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, alias="page_size", ge=1, le=500),
    sort: str = "name",
) -> dict[str, Any]:
    with db() as connection:
        token = f"%{search}%"
        where = "WHERE r.name LIKE ? OR r.comment LIKE ?"
        params = (token, token)
        count = connection.execute("SELECT COUNT(*) FROM roles r " + where, params).fetchone()[0]
        offset, limit = paging(page_number, page_size)
        order = sort_clause(sort, {"name", "created_at"})
        items = rows(
            connection,
            "SELECT r.*, COUNT(DISTINCT rp.permission_id) AS permissions_count, "
            "COUNT(DISTINCT ur.user_id) AS users_count FROM roles r "
            "LEFT JOIN role_permissions rp ON rp.role_id = r.id "
            f"LEFT JOIN user_roles ur ON ur.role_id = r.id {where} GROUP BY r.id ORDER BY r.{order}, r.id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        for item in items:
            item["permissions"] = relation_ids(connection, "role_permissions", "role_id", "permission_id", item["id"])
            item["users"] = relation_ids(connection, "user_roles", "role_id", "user_id", item["id"])
        return page(items, count, page_number, page_size)


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


@app.delete("/api/v1/rbac/roles/", status_code=204)
def bulk_delete_roles(ids: str = Query(..., description="逗号分隔的角色 ID")) -> None:
    role_ids = id_list(ids)
    if not role_ids:
        raise HTTPException(400, "至少选择一个角色")
    with db() as connection:
        delete_many(connection, "roles", role_ids)


@app.get("/api/v1/rbac/permissions/")
def list_permissions(
    search: str = "",
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, alias="page_size", ge=1, le=500),
    sort: str = "code",
) -> dict[str, Any]:
    with db() as connection:
        token = f"%{search}%"
        where = "WHERE code LIKE ? OR name LIKE ? OR resource LIKE ? OR action LIKE ?"
        params = (token, token, token, token)
        count = connection.execute("SELECT COUNT(*) FROM permissions " + where, params).fetchone()[0]
        offset, limit = paging(page_number, page_size)
        order = sort_clause(sort, {"code", "name", "resource", "action", "created_at"}, "code")
        items = rows(
            connection,
            f"SELECT * FROM permissions {where} ORDER BY {order}, id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return page(items, count, page_number, page_size)


@app.get("/api/export")
def export_csv(
    kind: str = Query("users"),
    search: str = "",
    is_active: bool | None = None,
    group_id: int | None = None,
) -> Response:
    """Export the current resource as UTF-8 CSV for handoff and backup."""
    if kind not in {"users", "groups", "roles", "permissions"}:
        raise HTTPException(400, "不支持的导出类型")
    with db() as connection:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        if kind == "users":
            writer.writerow(["ID", "用户名", "姓名", "邮箱", "状态", "用户组", "角色", "备注"])
            clauses: list[str] = []
            params: list[Any] = []
            if search:
                token = f"%{search}%"
                clauses.append("(username LIKE ? OR name LIKE ? OR email LIKE ? OR comment LIKE ?)")
                params.extend([token, token, token, token])
            if is_active is not None:
                clauses.append("is_active = ?")
                params.append(int(is_active))
            if group_id is not None:
                clauses.append("EXISTS (SELECT 1 FROM user_groups ug WHERE ug.user_id = users.id AND ug.group_id = ?)")
                params.append(group_id)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            for item in rows(connection, f"SELECT * FROM users{where} ORDER BY name, id", tuple(params)):
                full = user_record(connection, item["id"]) or item
                writer.writerow([
                    full["id"], full["username"], full["name"], full["email"],
                    "启用" if full["is_active"] else "禁用",
                    "; ".join(group["name"] for group in full["groups"]),
                    "; ".join(role["name"] for role in full["roles"]), full["comment"],
                ])
        elif kind == "groups":
            writer.writerow(["ID", "名称", "成员数", "说明"])
            token = f"%{search}%"
            for item in rows(connection, "SELECT g.*, COUNT(ug.user_id) AS users_count FROM groups g LEFT JOIN user_groups ug ON ug.group_id = g.id WHERE g.name LIKE ? OR g.comment LIKE ? GROUP BY g.id ORDER BY g.name", (token, token)):
                writer.writerow([item["id"], item["name"], item["users_count"], item["comment"]])
        elif kind == "roles":
            writer.writerow(["ID", "名称", "权限数", "成员数", "说明"])
            token = f"%{search}%"
            for item in rows(connection, "SELECT r.*, COUNT(DISTINCT rp.permission_id) AS permissions_count, COUNT(DISTINCT ur.user_id) AS users_count FROM roles r LEFT JOIN role_permissions rp ON rp.role_id = r.id LEFT JOIN user_roles ur ON ur.role_id = r.id WHERE r.name LIKE ? OR r.comment LIKE ? GROUP BY r.id ORDER BY r.name", (token, token)):
                writer.writerow([item["id"], item["name"], item["permissions_count"], item["users_count"], item["comment"]])
        else:
            writer.writerow(["ID", "编码", "名称", "资源", "动作"])
            token = f"%{search}%"
            for item in rows(connection, "SELECT * FROM permissions WHERE code LIKE ? OR name LIKE ? OR resource LIKE ? OR action LIKE ? ORDER BY resource, action, code", (token, token, token, token)):
                writer.writerow([item["id"], item["code"], item["name"], item["resource"], item["action"]])
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )


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


@app.delete("/api/v1/rbac/permissions/", status_code=204)
def bulk_delete_permissions(ids: str = Query(..., description="逗号分隔的权限 ID")) -> None:
    permission_ids = id_list(ids)
    if not permission_ids:
        raise HTTPException(400, "至少选择一个权限")
    with db() as connection:
        delete_many(connection, "permissions", permission_ids)


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
