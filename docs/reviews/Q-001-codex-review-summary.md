# Q-001 codex review summary(2026-06-04)

任务:KG store(SQLite+NetworkX,9 节点/12 边,双时态 + SUPERSEDES)。
新文件:`backend/knowledge_graph/{__init__,schema,store}.py` + `tests/knowledge_graph/test_store.py`。

## Cycle 1 — `codex review --uncommitted`

2 findings(均 P2,均确认修复):

| # | 级别 | 位置 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | P2 | `store.py:28` / `pyproject.toml` | `networkx` 被无条件 import 但未在 `pyproject.toml` 声明 → 干净环境 `import backend.knowledge_graph` 即 `ModuleNotFoundError`(本机恰好装了所以测试全绿 —— 绿测试≠clean-install 安全) | `pyproject.toml` dependencies 增 `networkx>=3.4,<4` |
| 2 | P2 | `store.py:273` | `to_networkx` 把 `attrs` splat 进 `add_node/add_edge`,合法的域属性名 `name`/`status`/`node_type`/`edge_type` 会与保留关键字冲突 → `TypeError`,图视图在合法数据上崩 | 域属性改嵌套 `attrs=dict(...)` 单键,保留键永不冲突;新增回归测试 `test_networkx_view_survives_reserved_attr_names` |

## Cycle 2 — `codex exec --sandbox read-only` verify

**COMMIT-SAFE**(两项修复均确认)。

## 门禁

- pytest `tests/knowledge_graph/` 12 passed;全量 4746→4747 passed(见 commit 信息)
- 模块覆盖率 99%(schema 99% / store 99%)
- ruff + redline-check ALL PASS
- 红线:append-only 由 SQLite authorizer 物理拒 UPDATE/DELETE;零 LLM;
  import 闭包 = {sqlite3, json, networkx, pydantic, structlog} + 本包,无 backend.{api,broker,risk,llm,agents,mirofish,data}
