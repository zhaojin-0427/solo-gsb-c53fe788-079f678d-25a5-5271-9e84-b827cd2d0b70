"""DMX 灯具地址编排台 —— FastAPI 后端。"""
import csv
import io
import json
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, solver

app = FastAPI(title="DMX 灯具地址编排台")


@app.on_event("startup")
def startup():
    db.init_db()


# ---------- 请求模型 ----------

class ModeIn(BaseModel):
    name: str
    channels: int = Field(ge=1, le=512)


class ModelIn(BaseModel):
    name: str
    modes: List[ModeIn]


class FixtureIn(BaseModel):
    name: str
    model_id: Optional[int] = None
    mode_id: Optional[int] = None
    channels: Optional[int] = Field(default=None, ge=1, le=512)


class FixturePatch(BaseModel):
    name: Optional[str] = None
    locked: Optional[bool] = None


class LineIn(BaseModel):
    name: str


class ReservationIn(BaseModel):
    start: int = Field(ge=1, le=512)
    end: int = Field(ge=1, le=512)


class PublishIn(BaseModel):
    preview_id: int


# ---------- 状态 ----------

@app.get("/api/state")
def get_state():
    return db.load_state(db.get_conn())


# ---------- 灯具型录 ----------

@app.post("/api/models", status_code=201)
def import_models(payload: List[ModelIn]):
    """批量导入型录：型号 + 模式 + 占用通道数。"""
    conn = db.get_conn()
    with conn:
        for m in payload:
            if not m.modes:
                raise HTTPException(400, f"型号「{m.name}」至少需要一个模式")
            cur = conn.execute("INSERT INTO models(name) VALUES (?)", (m.name,))
            for mo in m.modes:
                conn.execute(
                    "INSERT INTO modes(model_id, name, channels) VALUES (?,?,?)",
                    (cur.lastrowid, mo.name, mo.channels),
                )
        db.bump_config_version(conn)
    return db.load_state(conn)


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int):
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        db.bump_config_version(conn)
    return db.load_state(conn)


# ---------- 灯具实例 ----------

@app.post("/api/fixtures", status_code=201)
def create_fixture(payload: FixtureIn):
    conn = db.get_conn()
    channels = payload.channels
    if payload.mode_id is not None:
        row = conn.execute(
            "SELECT channels FROM modes WHERE id = ?", (payload.mode_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "模式不存在")
        channels = row["channels"]
    if channels is None:
        raise HTTPException(400, "请指定模式或通道数")
    with conn:
        conn.execute(
            "INSERT INTO fixtures(name, model_id, mode_id, channels) VALUES (?,?,?,?)",
            (payload.name, payload.model_id, payload.mode_id, channels),
        )
        db.bump_config_version(conn)
    return db.load_state(conn)


@app.patch("/api/fixtures/{fixture_id}")
def patch_fixture(fixture_id: int, payload: FixturePatch):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)).fetchone()
    if not row:
        raise HTTPException(404, "灯具不存在")
    with conn:
        if payload.name is not None:
            conn.execute(
                "UPDATE fixtures SET name = ? WHERE id = ?", (payload.name, fixture_id)
            )
        if payload.locked is not None:
            if payload.locked:
                assigned = conn.execute(
                    "SELECT 1 FROM assignments WHERE fixture_id = ?", (fixture_id,)
                ).fetchone()
                if not assigned:
                    raise HTTPException(409, "该灯具尚未发布地址，无法锁定")
            conn.execute(
                "UPDATE fixtures SET locked = ? WHERE id = ?",
                (1 if payload.locked else 0, fixture_id),
            )
        db.bump_config_version(conn)
    return db.load_state(conn)


@app.delete("/api/fixtures/{fixture_id}")
def delete_fixture(fixture_id: int):
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM fixtures WHERE id = ?", (fixture_id,))
        db.bump_config_version(conn)
    return db.load_state(conn)


# ---------- 线路与保留区 ----------

@app.post("/api/lines", status_code=201)
def create_line(payload: LineIn):
    conn = db.get_conn()
    count = conn.execute("SELECT COUNT(*) AS c FROM lines").fetchone()["c"]
    if count >= db.MAX_LINES:
        raise HTTPException(409, f"最多创建 {db.MAX_LINES} 条线路")
    with conn:
        conn.execute("INSERT INTO lines(name) VALUES (?)", (payload.name,))
        db.bump_config_version(conn)
    return db.load_state(conn)


@app.delete("/api/lines/{line_id}")
def delete_line(line_id: int):
    conn = db.get_conn()
    with conn:
        # 该线路上的既有分配一并失效（灯具回到未分配状态）
        conn.execute("DELETE FROM assignments WHERE line_id = ?", (line_id,))
        conn.execute("DELETE FROM lines WHERE id = ?", (line_id,))
        db.bump_config_version(conn)
    return db.load_state(conn)


@app.post("/api/lines/{line_id}/reservations", status_code=201)
def create_reservation(line_id: int, payload: ReservationIn):
    conn = db.get_conn()
    if not conn.execute("SELECT 1 FROM lines WHERE id = ?", (line_id,)).fetchone():
        raise HTTPException(404, "线路不存在")
    if payload.start > payload.end:
        raise HTTPException(400, "保留区起始地址不能大于结束地址")
    overlap = conn.execute(
        'SELECT 1 FROM reservations WHERE line_id = ? AND start <= ? AND "end" >= ?',
        (line_id, payload.end, payload.start),
    ).fetchone()
    if overlap:
        raise HTTPException(409, "保留区与既有保留区重叠")
    with conn:
        conn.execute(
            'INSERT INTO reservations(line_id, start, "end") VALUES (?,?,?)',
            (line_id, payload.start, payload.end),
        )
        db.bump_config_version(conn)
    return db.load_state(conn)


@app.delete("/api/reservations/{reservation_id}")
def delete_reservation(reservation_id: int):
    conn = db.get_conn()
    with conn:
        conn.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
        db.bump_config_version(conn)
    return db.load_state(conn)


# ---------- 预览 / 发布 ----------

def _load_problem(conn):
    fixtures = [dict(r) for r in conn.execute("SELECT * FROM fixtures ORDER BY id")]
    lines = [dict(r) for r in conn.execute("SELECT * FROM lines ORDER BY id")]
    reservations = [dict(r) for r in conn.execute("SELECT * FROM reservations")]
    assignments = {
        r["fixture_id"]: {"line_id": r["line_id"], "start": r["start"]}
        for r in conn.execute("SELECT * FROM assignments")
    }
    return fixtures, lines, reservations, assignments


@app.post("/api/preview")
def preview():
    conn = db.get_conn()
    fixtures, lines, reservations, assignments = _load_problem(conn)
    version = db.get_meta(conn, "config_version")
    if not fixtures:
        raise HTTPException(400, "尚未创建灯具，无法编排")
    if not lines:
        raise HTTPException(400, "尚未创建线路，无法编排")

    status, result = solver.solve(fixtures, lines, reservations, assignments)
    if status == "infeasible":
        return {
            "feasible": False,
            "config_version": version,
            "conflicts": solver.diagnose(fixtures, lines, reservations, assignments),
        }

    payload = {
        "placements": result["placements"],
        "migrations": result["migrations"],
        "moved_count": result["moved_count"],
        "used_lines": result["used_lines"],
    }
    with conn:
        cur = conn.execute(
            "INSERT INTO previews(config_version, payload) VALUES (?,?)",
            (version, json.dumps(payload)),
        )
        preview_id = cur.lastrowid
    return {
        "feasible": True,
        "exact": status == "optimal",
        "preview_id": preview_id,
        "config_version": version,
        **payload,
    }


@app.post("/api/publish")
def publish(payload: PublishIn):
    """按配置版本原子发布：版本不匹配则拒绝。"""
    conn = db.get_conn()
    # BEGIN IMMEDIATE 保证检查与写入处于同一写事务，发布是原子的
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM previews WHERE id = ?", (payload.preview_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(404, "预览不存在")
        current = db.get_meta(conn, "config_version")
        if row["config_version"] != current:
            conn.rollback()
            raise HTTPException(
                409,
                f"配置已变化（预览基于版本 {row['config_version']}，当前版本 {current}），"
                "请重新预览后再发布",
            )
        data = json.loads(row["payload"])
        conn.execute("DELETE FROM assignments")
        for fid, p in data["placements"].items():
            conn.execute(
                "INSERT INTO assignments(fixture_id, line_id, start) VALUES (?,?,?)",
                (int(fid), p["line_id"], p["start"]),
            )
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'published_version'", (current,)
        )
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    return db.load_state(conn)


# ---------- 已发布地址表 CSV ----------

@app.get("/api/download.csv")
def download_csv():
    conn = db.get_conn()
    rows = conn.execute(
        """
        SELECT f.id AS fixture_id, f.name AS fixture, f.channels,
               l.name AS line, a.start
        FROM assignments a
        JOIN fixtures f ON f.id = a.fixture_id
        JOIN lines l ON l.id = a.line_id
        ORDER BY l.id, a.start, f.id
        """
    ).fetchall()
    buf = io.StringIO()
    buf.write("﻿")  # BOM，便于 Excel 识别 UTF-8
    w = csv.writer(buf)
    w.writerow(["灯具ID", "灯具名称", "线路", "起始地址", "结束地址", "占用通道数"])
    for r in rows:
        w.writerow(
            [r["fixture_id"], r["fixture"], r["line"], r["start"],
             r["start"] + r["channels"] - 1, r["channels"]]
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=dmx_addresses.csv"},
    )


# ---------- 前端静态资源 ----------

@app.get("/")
def index():
    return FileResponse("app/static/index.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")
