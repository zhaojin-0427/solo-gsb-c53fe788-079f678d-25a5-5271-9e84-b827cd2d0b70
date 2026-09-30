"""OR-Tools CP-SAT 求解器：为灯具分配不跨线路的连续通道块。

优化目标按字典序依次：
  1. 已配灯具的迁移台数最少；
  2. 启用线路数最少；
  3. 按灯具 ID 排序后的 (线路, 起始地址) 序列字典序最小。
"""
from ortools.sat.python import cp_model

LINE_CAPACITY = 512
# 每条线路上的位置键基数：线路索引 * BASE + (起始地址 - 1)
BASE = LINE_CAPACITY + 1


def _free_segments(reservations, capacity=LINE_CAPACITY):
    """由保留区计算线路上的空闲连续段 [(start, end)]（闭区间）。"""
    segs = []
    cursor = 1
    for r in sorted(reservations, key=lambda r: r["start"]):
        if r["start"] > cursor:
            segs.append((cursor, r["start"] - 1))
        cursor = max(cursor, r["end"] + 1)
    if cursor <= capacity:
        segs.append((cursor, capacity))
    return segs


def diagnose(fixtures, lines, reservations, assignments):
    """无解时给出冲突约束说明（人话版）。"""
    conflicts = []
    if not lines:
        conflicts.append("尚未创建任何线路，无法分配地址。")
        return conflicts

    res_by_line = {l["id"]: [] for l in lines}
    for r in reservations:
        res_by_line.setdefault(r["line_id"], []).append(r)
    free_by_line = {
        l["id"]: _free_segments(res_by_line.get(l["id"], [])) for l in lines
    }
    line_name = {l["id"]: l["name"] for l in lines}
    fx_name = {f["id"]: f["name"] for f in fixtures}

    # 1) 单台灯具超过 512 通道
    for f in fixtures:
        if f["channels"] > LINE_CAPACITY:
            conflicts.append(
                f"灯具「{f['name']}」占用 {f['channels']} 通道，超过单线路容量 {LINE_CAPACITY}。"
            )

    # 2) 灯具在任何线路上都找不到足够大的连续空闲段
    for f in fixtures:
        if f["channels"] > LINE_CAPACITY:
            continue
        fits = [
            l["name"]
            for l in lines
            if any(e - s + 1 >= f["channels"] for s, e in free_by_line[l["id"]])
        ]
        if not fits:
            biggest = max(
                (e - s + 1 for segs in free_by_line.values() for s, e in segs),
                default=0,
            )
            conflicts.append(
                f"灯具「{f['name']}」需要 {f['channels']} 个连续通道，"
                f"但所有线路的最大连续空闲段仅 {biggest} 通道（受保留区限制）。"
            )

    # 3) 总需求超过总空闲容量
    total_free = sum(e - s + 1 for segs in free_by_line.values() for s, e in segs)
    total_need = sum(f["channels"] for f in fixtures)
    if total_need > total_free:
        conflicts.append(
            f"全部灯具共需 {total_need} 通道，所有线路空闲容量合计仅 {total_free} 通道。"
        )

    # 4) 锁定灯具之间的冲突 / 与保留区的冲突
    locked = [f for f in fixtures if f.get("locked") and f["id"] in assignments]
    placed = {}
    for f in locked:
        a = assignments[f["id"]]
        if a["line_id"] not in line_name:
            conflicts.append(
                f"锁定灯具「{f['name']}」所在的线路已被删除，请解除锁定或重新分配。"
            )
            continue
        s, e = a["start"], a["start"] + f["channels"] - 1
        if e > LINE_CAPACITY:
            conflicts.append(
                f"锁定灯具「{f['name']}」在「{line_name[a['line_id']]}」"
                f"占用 {s}-{e}，超出 512 通道范围。"
            )
        for r in res_by_line.get(a["line_id"], []):
            if s <= r["end"] and r["start"] <= e:
                conflicts.append(
                    f"锁定灯具「{f['name']}」（{line_name[a['line_id']]} {s}-{e}）"
                    f"与保留区 {r['start']}-{r['end']} 重叠。"
                )
        placed.setdefault(a["line_id"], []).append((s, e, f["name"]))
    for lid, items in placed.items():
        items.sort()
        for (s1, e1, n1), (s2, e2, n2) in zip(items, items[1:]):
            if s2 <= e1:
                conflicts.append(
                    f"锁定灯具「{n1}」（{s1}-{e1}）与「{n2}」（{s2}-{e2}）"
                    f"在「{line_name[lid]}」上相互重叠。"
                )

    # 5) 每条线路上被锁定灯具的占用超过该线空闲容量
    for l in lines:
        locked_need = sum(
            f["channels"]
            for f in locked
            if assignments[f["id"]]["line_id"] == l["id"]
        )
        free = sum(e - s + 1 for s, e in free_by_line[l["id"]])
        if locked_need > free:
            conflicts.append(
                f"线路「{l['name']}」上锁定灯具共需 {locked_need} 通道，"
                f"超过该线空闲容量 {free} 通道。"
            )

    if not conflicts:
        conflicts.append("约束组合过紧导致无解，请尝试减少保留区、解除部分锁定或增加线路。")
    return conflicts


def solve(fixtures, lines, reservations, assignments, time_limit=10.0):
    """求解地址编排。

    返回 (status, result)：
      status: "optimal" | "feasible" | "infeasible"
      feasible 时 result = {
          "placements": {fixture_id: {"line_id", "start"}},
          "migrations": [...], "used_lines": n, "moved_count": m,
      }
    """
    model = cp_model.CpModel()
    line_ids = [l["id"] for l in lines]
    line_idx = {lid: i for i, lid in enumerate(line_ids)}

    starts, pres, intervals = {}, {}, {lid: [] for lid in line_ids}
    for f in fixtures:
        fid, ch = f["id"], f["channels"]
        starts[fid] = model.new_int_var(1, LINE_CAPACITY - ch + 1, f"start_{fid}")
        pres_vars = []
        for lid in line_ids:
            b = model.new_bool_var(f"on_{fid}_{lid}")
            pres[(fid, lid)] = b
            pres_vars.append(b)
            iv = model.new_optional_interval_var(
                starts[fid], ch, starts[fid] + ch, b, f"iv_{fid}_{lid}"
            )
            intervals[lid].append(iv)
        model.add_exactly_one(pres_vars)

    # 保留区作为固定区间参与 NoOverlap，灯具不得与之重叠
    for r in reservations:
        lid = r["line_id"]
        if lid in intervals:
            intervals[lid].append(
                model.new_interval_var(
                    r["start"], r["end"] - r["start"] + 1, r["end"] + 1, f"res_{r['id']}"
                )
            )
    for lid in line_ids:
        model.add_no_overlap(intervals[lid])

    # 锁定：固定到既有线路与起始地址
    for f in fixtures:
        if f.get("locked") and f["id"] in assignments:
            a = assignments[f["id"]]
            if a["line_id"] in line_idx:
                model.add(starts[f["id"]] == a["start"])
                for lid in line_ids:
                    model.add(pres[(f["id"], lid)] == (1 if lid == a["line_id"] else 0))

    # 目标 1：迁移台数（已有分配且位置变化的灯具数）
    moved = []
    for f in fixtures:
        a = assignments.get(f["id"])
        if not a or a["line_id"] not in line_idx:
            continue
        fid = f["id"]
        at = model.new_bool_var(f"at_{fid}")
        model.add(starts[fid] == a["start"]).only_enforce_if(at)
        model.add(starts[fid] != a["start"]).only_enforce_if(at.negated())
        same = model.new_bool_var(f"same_{fid}")
        model.add_bool_and([pres[(fid, a["line_id"])], at]).only_enforce_if(same)
        model.add_bool_or([pres[(fid, a["line_id"])].negated(), at.negated()]).only_enforce_if(
            same.negated()
        )
        moved.append(same.negated())
    total_moved = sum(moved) if moved else 0

    # 目标 2：启用线路数
    used = []
    for lid in line_ids:
        u = model.new_bool_var(f"used_{lid}")
        members = [pres[(f["id"], lid)] for f in fixtures]
        if members:
            model.add_max_equality(u, members)
        else:
            model.add(u == 0)
        used.append(u)
    total_used = sum(used)

    # 目标 3 的位置键：线路索引 * BASE + (起始地址 - 1)
    pos = {}
    for f in fixtures:
        fid = f["id"]
        pos[fid] = sum(
            pres[(fid, lid)] * (line_idx[lid] * BASE) for lid in line_ids
        ) + (starts[fid] - 1)

    solver = cp_model.CpSolver()
    per_stage = max(1.0, time_limit / (2 + len(fixtures)))

    def run():
        solver.parameters.max_time_in_seconds = per_stage
        return solver.solve(model)

    def pin(expr, objective):
        """最小化 objective 并把结果钉住，供下一阶段使用。"""
        model.minimize(objective)
        status = run()
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return status
        model.add(objective <= int(round(solver.value(objective))))
        return status

    overall = cp_model.OPTIMAL
    if moved:
        st = pin(total_moved, total_moved)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return "infeasible", None
        overall = st
    st = pin(total_used, total_used)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "infeasible", None
    overall = min(overall, st)
    for f in sorted(fixtures, key=lambda x: x["id"]):
        st = pin(pos[f["id"]], pos[f["id"]])
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return "infeasible", None
        overall = min(overall, st)

    # 最终求解确认（约束均已钉住，应立即可行）
    model.minimize(0)
    final = run()
    if final not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "infeasible", None

    placements = {}
    for f in fixtures:
        fid = f["id"]
        lid = next(lid for lid in line_ids if solver.value(pres[(fid, lid)]))
        placements[fid] = {"line_id": lid, "start": solver.value(starts[fid])}

    migrations = []
    for f in fixtures:
        a = assignments.get(f["id"])
        p = placements[f["id"]]
        if a and (a["line_id"] != p["line_id"] or a["start"] != p["start"]):
            migrations.append(
                {
                    "fixture_id": f["id"],
                    "name": f["name"],
                    "from": a,
                    "to": p,
                }
            )
    used_lines = sum(
        1 for lid in line_ids if any(p["line_id"] == lid for p in placements.values())
    )
    return (
        "optimal" if overall == cp_model.OPTIMAL else "feasible",
        {
            "placements": placements,
            "migrations": migrations,
            "moved_count": len(migrations),
            "used_lines": used_lines,
        },
    )
