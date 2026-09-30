# DMX 灯具地址编排台

本地 Web 应用：为 DMX 灯具自动规划 512 通道线路上的连续地址块。

- 后端：Python + FastAPI + SQLite + OR-Tools（CP-SAT）
- 前端：浏览器原生 JavaScript（无构建步骤）
- 部署：Docker Compose 一键启动

## 功能

- **灯具型录导入**：批量导入型号、模式及占用通道数（JSON）。
- **灯具实例**：从型录创建灯具实例；已发布地址的灯具可**锁定**当前线路与起始地址。
- **线路与保留区**：最多 4 条线路，每条 512 通道；每条线路可设多个保留区。
- **自动编排**：每台灯具分配**不跨线路的连续通道块**，互不重叠、避开保留区；优化目标按字典序：
  1. 已配灯具的**迁移台数最少**；
  2. **启用线路数最少**；
  3. 按灯具 ID 排序后的 (线路, 起始地址) 序列**字典序最小**。
- **预览与诊断**：界面展示各线路占用图、迁移清单；无解时列出冲突约束（容量不足、保留区挤压、锁定冲突等）。
- **原子发布**：预览绑定配置版本；配置一旦变化，旧预览发布即被拒绝（HTTP 409），需重新预览。发布在单个写事务内完成。
- **CSV 下载**：已发布地址表可下载为 CSV（UTF-8 带 BOM，Excel 可直接打开）。

## 启动（Docker Compose）

```bash
docker compose up --build
```

访问 <http://localhost:8000>。数据持久化在名为 `dmx-data` 的卷中（SQLite，容器内路径 `/data/dmx.db`）。

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
DMX_DB=./data/dmx.db uvicorn app.main:app --reload
```

访问 <http://localhost:8000>。

## 使用流程

1. **灯具型录**：粘贴或上传 JSON 导入型号，例如：
   ```json
   [{"name": "LED Par 64", "modes": [{"name": "3CH", "channels": 3}, {"name": "8CH", "channels": 8}]}]
   ```
2. **灯具实例**：选择型号与模式，逐台添加灯具。
3. **线路与保留区**：创建 1–4 条线路，按需添加保留区（如 1–10 保留给调光柜）。
4. **地址编排**：点击「求解预览」查看线路占用图与迁移清单；无解时显示冲突约束。确认后点击「发布此预览」。
5. **已发布**：查看当前生效的地址表并下载 CSV。对需要保持不动的灯具勾选「锁定」，下次求解将固定其线路与起始地址。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 全量状态（型录/灯具/线路/保留区/已发布分配/版本号） |
| POST | `/api/models` | 批量导入型录 |
| DELETE | `/api/models/{id}` | 删除型号 |
| POST | `/api/fixtures` | 新增灯具（按模式确定通道数） |
| PATCH | `/api/fixtures/{id}` | 改名 / 锁定 / 解锁 |
| DELETE | `/api/fixtures/{id}` | 删除灯具 |
| POST | `/api/lines` | 新增线路（最多 4 条） |
| DELETE | `/api/lines/{id}` | 删除线路（其分配一并失效） |
| POST | `/api/lines/{id}/reservations` | 新增保留区 |
| DELETE | `/api/reservations/{id}` | 删除保留区 |
| POST | `/api/preview` | 求解并生成预览（绑定当前配置版本） |
| POST | `/api/publish` | 按 `preview_id` 原子发布；版本不符返回 409 |
| GET | `/api/download.csv` | 下载已发布地址表 CSV |

任何配置变更（型录/灯具/线路/保留区/锁定）都会使配置版本号 +1，从而让旧预览失效。
