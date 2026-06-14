# AB-003 修订 — 2026-06-14 param 运行时落地:lockfile schema v2 + RuntimeParamStore + 解除两处拒(P1d)

> **修订基准**: [P2-2-amendment-2026-06-12 sim 客观晋升](./P2-2-amendment-2026-06-12-sim-objective-promotion.md) §1.2(AB-003 activation:`ActivationManifest`/`next_boot.lock`/boot consume-once)+ `backend/strategy_evolution/activation.py`(param 两处拒:`write_next_boot_lock:168` / `apply_pending_activation:288`)
> **关联**: 自进化 dossier §3.6 + §8.4(codex)+ `backend/strategy_evolution/evolvable_params.py`(AB-005 白名单 + clamp + 单调)
> **修订日期**: 2026-06-14
> **触发**: AB-003 已预留 param 路径但留空 —— `activation.py` 两处硬拒 param-bearing manifest(注释明写「lands with the AB experiment harness」),lockfile `new_lock`(:314)只写 `approved` 丢 `params`,无 runtime 消费端。量化参数进化环闭合需补此最后一段(发现→预筛→冻结 shadow→人工 pin→**重启生效**)。

## 1. 修订前

- `ActivationManifest.params` 已建模,但:
  - `write_next_boot_lock`(:168):`if manifest.params: raise ValueError`(staging 拒)。
  - `apply_pending_activation`(:288):`if manifest.params: ... CORRUPT_STAGED_MANIFEST`(apply 防御纵深拒)。
  - `new_lock`(:314)只写 `version/updated_at/approved`,**不写 params**。
- 15 个白名单参数散落各模块 dataclass 默认值 / YAML(`IntradayTriggerConfig`/`add_position`/`intraday_calibration`/`config/candidate_weights`/`config/allocation_policy.yaml`/`config/slot_rotation_policy.yaml`);**无中央 runtime 注入点**读 `manifest.params`。

## 2. 修订后(lockfile v2 + RuntimeParamStore + 解除拒)

### 2.1 lockfile schema v1→v2

- `config/live_artifacts.lock.json` 增可选 `params: {name: value}` 块(已在 `ActivationManifest.params` 建模)。**v2 读兼容 v1**(无 params = 空 = **与今天 byte-identical**)。
- `apply_pending_activation` 的 `new_lock` 字典补写 `manifest.params`(不再丢弃)。

### 2.2 新建 boot 期 `RuntimeParamStore`(不可变)

- boot 一次性从 live lockfile `params` 载入 → **再过一遍 `validate_param_set`(fail-closed:clamp 违反/冻结集命中/组约束破 → 拒 boot 或回落默认 + 大声 audit)** → 不可变快照(MappingProxyType,无 runtime mutation)。
- **缺失/空 = 全取 code 默认 = 现状 byte-identical**(N-001 式安全:未激活任何 evolved param 时系统行为不变)。

### 2.3 注入消费端(既有注入模式)

- `RuntimeParamStore` 注入消费模块构造点:`IntradayTriggerConfig`(注:Line-2 盘中参数**不进环**,但若 selector/allocation 之外将来纳入须走此通路)/ `candidate_selector` 权重 / `allocation` 配额 / `theme` tier 权重 —— 构造时 `store.get(name, code_default)`,叠在现有默认之上。store 空时取 code_default。
- **P1 首批生效目标 = selector/allocation 权重**(`P2-2-amendment-2026-06-14-quant-param-evolution-loop` §2.5);其余消费端按需接。

### 2.4 解除两处拒(防御纵深保留)

- `write_next_boot_lock` + `apply_pending_activation` 的 param ValueError 替换为:写 `manifest.params` 进 lockfile v2 + 路径上**两道 `validate_param_set` 复验**(staging 前 + boot 载入)+ boot health assert(`LiveArtifactRegistry.from_lockfile` + RuntimeParamStore 载入成功);任一失败 → 自动回退 prev 字节(沿用 AB-003 rollback)。

### 2.5 单调-vs-冻结默认(codex,防多步放松)

- safety-adjacent 参数(能推迟 SELL 的)「只紧不松」单调约束的基线 = **冻结代码默认值**(不可变 baseline),**非上次进化值** —— 防多次晋升一步步放松到接近 clamp_max。clamp_max 本身仍是被审定硬上界 → 双重兜底。`evolvable_params.validate_param_change` 的 `current` 语义据此收紧(对抗测试先写)。

## 3. 实施与门禁

- 本 amendment = 边界文档 → docs 例外。**实施(P1d)** commit 前 codex-review + 全量 pytest + ruff + redline。TDD 对抗先写:lockfile 空 → RuntimeParamStore 空 → 系统 byte-identical(回归);lockfile params clamp 违反 → boot fail-closed;多步放松 safety-adjacent → 被冻结默认基线拒;param manifest staging+apply 全程双复验。
- **依赖 P1c**(进化环产出 param manifest)。activation 受控重启沿用 `deploy/promote_restart.sh`;env/重启 owner 亲为。

## 4. 红线清单

1. lockfile v2 `params` 块;**空 params = 现状 byte-identical**(未激活不改行为)。
2. `RuntimeParamStore` 不可变 + boot 一次性载入 + 双 `validate_param_set` 复验(fail-closed)。
3. 解除 staging/apply 两处拒,但保留防御纵深(双复验 + health assert + 自动回退)。
4. safety-adjacent 单调基线 = 冻结代码默认(防多步放松);clamp 硬上界不变。
5. 进化应用仍**人工 pin + 重启**(sim 亦然);config runtime 不可改 + hot-reload 禁(改 = git diff + amendment + 重启)不变。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG;plan.html P1d 任务。
</content>
