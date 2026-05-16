/**
 * G-005 — JS mirror of backend/execution/regex_patterns.py (B-003 SSoT).
 *
 * Every entry must keep byte-equal pattern strings with the backend
 * ``PATTERNS_AS_DICT`` mapping; vitest assertion at unit-test time
 * guarantees the mirror stays in sync (P0-4 §1.1 frontend channel must
 * use the same parser shape as the Feishu main path — drift means a
 * report that previews "OK" on the frontend would later get rejected
 * by the backend, defeating the purpose of the preview).
 *
 * If you need to add a new pattern, change BOTH files in the same
 * commit. The redline / vitest test catches single-side edits.
 *
 * P0-4 §1.1.2 — LLM-assisted parsing is forbidden, so the regex tier
 * is the only allowed parser. This file MUST NOT call any AI helper.
 */

/** Locked pattern-id vocabulary. Order matches backend `PATTERNS_AS_DICT`. */
export const PATTERN_IDS = [
  'FILLED',
  'PARTIAL',
  'UNFILLED',
  'AMEND_FILLED',
  'AMEND_PARTIAL',
  'AMEND_UNFILLED',
  'POST_CLOSE_FILLED',
  'POST_CLOSE_PARTIAL',
  'POST_CLOSE_UNFILLED',
] as const

export type PatternId = (typeof PATTERN_IDS)[number]

const IID = '(?<instruction_id>QM-\\d{8}-\\d{6}-\\d{6}-(?:BUY|SELL)-\\d{3})'
const SIDE = '(?<side_zh>买入|卖出)'
const CODE = '(?<stock_code>\\d{6})'
const NONNEG = '\\d+(?:\\.\\d+)?'

const FILLED_BASE =
  `已执行 ${IID} ${SIDE} ${CODE} (?<volume>\\d+)股 ` +
  `成交价 (?<fill_price>${NONNEG}) ` +
  `手续费 (?<fee>${NONNEG})`

const PARTIAL_BASE =
  `部分执行 ${IID} ${SIDE} ${CODE} ` +
  `(?<filled_volume>\\d+)股 ` +
  `成交价 (?<fill_price>${NONNEG}) ` +
  `剩余未成交 (?<remain_volume>\\d+)股`

// re.DOTALL on the Python side; JS RegExp 's' flag mirrors the same
// behaviour (any-char including newline). The `{1,200}` repetition
// caps reason length the same way the Python parser does.
const UNFILLED_BASE = `未执行 ${IID} 原因[::]\\s?(?<reason>.{1,200})`

const ANCHOR = (body: string) => `^${body}$`

/**
 * Pattern strings keyed by the canonical id; structurally identical to
 * `backend.execution.regex_patterns.PATTERNS_AS_DICT` AFTER both sides
 * are anchored with ``^...$`` (Python compiles the bare body and the
 * mirror exposes the anchored form — vitest mirror test compares the
 * anchored shape, so the API surface stays comparable).
 */
export const PATTERN_STRINGS: Readonly<Record<PatternId, string>> = Object.freeze({
  FILLED: ANCHOR(FILLED_BASE),
  PARTIAL: ANCHOR(PARTIAL_BASE),
  UNFILLED: ANCHOR(UNFILLED_BASE),
  AMEND_FILLED: ANCHOR(`更正 ${FILLED_BASE}`),
  AMEND_PARTIAL: ANCHOR(`更正 ${PARTIAL_BASE}`),
  AMEND_UNFILLED: ANCHOR(`更正 ${UNFILLED_BASE}`),
  POST_CLOSE_FILLED: ANCHOR(`盘后补录 ${FILLED_BASE}`),
  POST_CLOSE_PARTIAL: ANCHOR(`盘后补录 ${PARTIAL_BASE}`),
  POST_CLOSE_UNFILLED: ANCHOR(`盘后补录 ${UNFILLED_BASE}`),
})

/** Compiled regex map. Lazy — built on first use; cached afterwards. */
let _COMPILED: Readonly<Record<PatternId, RegExp>> | null = null

function compileAll(): Readonly<Record<PatternId, RegExp>> {
  if (_COMPILED) return _COMPILED
  const out: Partial<Record<PatternId, RegExp>> = {}
  for (const id of PATTERN_IDS) {
    // 's' enables dotall so the reason capture in UNFILLED can include
    // newlines (matches Python re.DOTALL).
    out[id] = new RegExp(PATTERN_STRINGS[id], 's')
  }
  _COMPILED = Object.freeze(out as Record<PatternId, RegExp>)
  return _COMPILED
}

export interface PreviewMatch {
  /** ID of the pattern that matched, or null when no pattern fits. */
  patternId: PatternId | null
  /** Named-capture groups extracted by the matching pattern. */
  groups: Readonly<Record<string, string>>
}

/**
 * Backend ``parse_execution_report`` trims leading/trailing whitespace
 * and collapses repeated horizontal whitespace inside the body before
 * matching. We mirror that here so the preview banner does not gatekeep
 * input the backend would accept (codex cycle 1 P2 RESOLVED). The
 * normalization is deliberately narrow — newlines stay intact for the
 * UNFILLED reason capture, and full-width whitespace is preserved so
 * any Chinese punctuation variants round-trip cleanly.
 */
export function normalizeForPreview(raw: string): string {
  // Mirror backend ``backend.services.execution_report_parser._normalise``
  // exactly:
  //   1. ``text.strip()`` — Python's ``str.strip`` drops every
  //      whitespace codepoint (space, tab, newline, plus the wider
  //      Unicode whitespace categories). JS ``String.trim()`` is the
  //      closest equivalent (trims WhiteSpace + LineTerminator).
  //   2. Collapse runs of ``[ \t]+`` (ASCII space / tab) inside the
  //      body. Newlines stay so the UNFILLED reason capture (re.DOTALL
  //      on Python, /s/ flag on JS) can still cover multi-line reasons.
  //
  // The earlier 3-stage shape left mixed newline+space boundary noise
  // (e.g. ``" \n  已执行 ...  \n "``) partially un-trimmed, blocking
  // valid pasted reports the backend would have accepted (codex cycle
  // 2 P2 RESOLVED).
  return raw.trim().replace(/[ \t]+/g, ' ')
}

/**
 * Run the 9 locked patterns against the normalized input and return
 * the first full match. Used by the ExecutionReportEntry preview
 * banner to confirm a report would be accepted by the backend parser
 * before submit.
 */
export function previewExecutionReport(raw: string): PreviewMatch {
  const patterns = compileAll()
  const normalized = normalizeForPreview(raw)
  for (const id of PATTERN_IDS) {
    const re = patterns[id]
    const m = re.exec(normalized)
    if (m && m[0] === normalized) {
      const groups = Object.freeze({ ...(m.groups || {}) }) as Readonly<
        Record<string, string>
      >
      return { patternId: id, groups }
    }
  }
  return { patternId: null, groups: Object.freeze({}) }
}

/**
 * Top-level 5 templates surfaced to operators in the UI. Each entry
 * carries a copy-paste-friendly placeholder so a first-time user does
 * not have to type the prefix from scratch.
 */
export const TEMPLATES = [
  {
    id: 'FILLED',
    label: '已执行',
    placeholder:
      '已执行 QM-20260516-093001-600519-BUY-001 买入 600519 100股 成交价 1800.5 手续费 5.4',
    description: '订单完全成交时使用。',
  },
  {
    id: 'PARTIAL',
    label: '部分执行',
    placeholder:
      '部分执行 QM-20260516-093001-600519-BUY-001 买入 600519 60股 成交价 1800.5 剩余未成交 40股',
    description: '订单部分成交时使用,剩余股数自动 EXPIRED。',
  },
  {
    id: 'UNFILLED',
    label: '未执行',
    placeholder:
      '未执行 QM-20260516-093001-600519-BUY-001 原因:盘中没机会建仓',
    description: '订单完全未成交,需说明原因(≤200 字)。',
  },
  {
    id: 'AMEND_FILLED',
    label: '更正',
    placeholder:
      '更正 已执行 QM-20260516-093001-600519-BUY-001 买入 600519 100股 成交价 1800.5 手续费 5.4',
    description: '已提交回报需要修正时,使用对应 "更正 ..." 前缀。',
  },
  {
    id: 'POST_CLOSE_FILLED',
    label: '盘后补录',
    placeholder:
      '盘后补录 已执行 QM-20260516-093001-600519-BUY-001 买入 600519 100股 成交价 1800.5 手续费 5.4',
    description:
      '盘后(16:00 后)补录,bypass valid_until,但仅限当日 16:00 前完成。',
  },
] as const

export type TemplateId = (typeof TEMPLATES)[number]['id']
