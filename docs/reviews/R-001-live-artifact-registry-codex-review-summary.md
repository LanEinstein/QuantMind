# R-001 LiveArtifactRegistry — codex review summary

> 任务:Phase R / R-001(LiveArtifactRegistry 批准哈希集 + 对抗测试先写)。
> 日期:2026-06-11(session #73)。门禁:本地 pytest + ruff + mypy(strict)+ redline 全绿 + 跨模型审查。
> 审查:codex `review --uncommitted` 1 轮(2 findings 全修)→ codex 撞 usage limit(重置 18:27)→ 回退 `/code-review`(对抗 verify agent)1 轮确认 clean。

## 设计要点

R-001 = 自进化的**唯一批准闸**(R0 §8 七泄漏路径 / P2-2 §2.3):boot 从不可变 `config/live_artifacts.lock.json` 载入 5 类(`ArtifactKind`:strategy_code/feature_def/prompt_version/anomaly_model/rag_index)批准 SHA256 集;`is_approved(kind, identifier)` **kind-typed** 仅认 pin 哈希(草案 `is_approved(hash)` 精化为分型,防策略哈希批准 prompt;在已决 5 类边界内,**非新 amendment**);**无 runtime 加哈希路径**;空 bootstrap = deny-all(fail-closed)。镜像 `backend/services/prompt_registry.py` 的 frozen+strict+fail-closed+不可变姿态。对抗测试先写(RED→GREEN)。

## codex cycle 1(2 findings 全修)

- **P1 冻结批准映射本身** —— `__setattr__` 只防重绑 `_approved`,但它是普通 dict → `registry._approved[kind] = frozenset({new_hash})` 即 runtime 加哈希路径,击穿红线。→ 改 `MappingProxyType`(map 只读,item-assign 抛 TypeError)+ 值仍 frozenset;新增测试锁 `reg._approved[kind] = ...` 抛 TypeError。
- **P2 mypy strict 失败** —— `_approved` 仅经 `object.__setattr__` 设、无类级注解 → strict 报 `attr-defined`/`no-any-return`。→ 加类级 `_approved: Mapping[ArtifactKind, frozenset[str]]` 注解(与 `__slots__` 共存,bare 注解无冲突);`mypy --strict` 转 clean。

## /code-review 回退 verify(codex 撞额度)

对抗 verify agent 全文复核 6 维,**[] 无新/残留缺陷**,逐项 SOUND:
1. 不可变:`__init__` comprehension 重建 + `frozenset()` 拷贝(caller-dict/set 别名不泄漏)+ MappingProxyType + frozenset 值 + `__setattr__` raise + `__slots__` 无 `__dict__`;`approved()` 返回 frozenset 本身不可变。唯一绕过 = `object.__setattr__`(需任意进程内代码执行,超威胁模型,Pydantic frozen 同样无法防)。
2. fail-closed:缺文件/坏 JSON/坏 schema/非 sha256/未知 kind(extra=forbid)/错 version literal 全 raise typed exception。
3. is_approved:纯集成员,空串/有效未 pin/跨 kind 全 deny;空 bootstrap deny-all。
4. schema:`^[a-f0-9]{64}$` fullmatch(小写 content-addressed),validator 迭代 ArtifactKind,frozen+strict+forbid。
5. 测试非 tautological:对抗测试真断不变量(返回集改动不影响 registry / item-assign TypeError / 真写坏文件断 typed 异常 / 加载真实 in-repo lockfile)。
6. mypy strict + ruff + import 隔离(7 禁子包 AST 扫描)全绿。
+ 顺手清理 1 cosmetic(未用 `tmp_path` fixture)。

## 终态门禁

- `tests/test_live_artifact_registry.py` 16 passed;`live_artifact_registry.py` 覆盖率 **100%**;`mypy --strict` clean。
- 全量 `pytest -q --cov=backend --cov-fail-under=70`:**4880 passed / 13 skipped / 90.86%**(R-001 前)→ 含 R-001。
- ruff 全绿;redline 全绿;`backend/strategy_evolution/` import 隔离(无 `backend.{api,broker,risk,llm,agents,mirofish,data}`)。
- 红线一条未破:无 runtime 加哈希、fail-closed、人工 gate(晋升经 amendment+pin+git+restart)、模块隔离。
