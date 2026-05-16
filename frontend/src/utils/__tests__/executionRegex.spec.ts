/**
 * G-005 — Frontend half of the shared regex-mirror fixture.
 *
 * Pair file: tests/test_execution_regex_mirror_backend.py.
 *
 * Both run against tests/fixtures/execution_reports_mirror_samples.json
 * so a single-side edit causes one side or the other to fail.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import {
  normalizeForPreview,
  PATTERN_IDS,
  PATTERN_STRINGS,
  previewExecutionReport,
  TEMPLATES,
  type PatternId,
} from '../executionRegex'

interface ValidSample {
  name: string
  pattern_id: PatternId
  raw_text: string
  groups: Record<string, string>
}

interface InvalidSample {
  name: string
  raw_text: string
}

interface Fixture {
  valid: ValidSample[]
  invalid: InvalidSample[]
}

const fixturePath = resolve(
  __dirname,
  '../../../../tests/fixtures/execution_reports_mirror_samples.json',
)
const fixture: Fixture = JSON.parse(readFileSync(fixturePath, 'utf-8'))

describe('executionRegex (G-005 SSoT mirror)', () => {
  it('exports the locked 9 pattern ids in canonical order', () => {
    expect([...PATTERN_IDS]).toEqual([
      'FILLED',
      'PARTIAL',
      'UNFILLED',
      'AMEND_FILLED',
      'AMEND_PARTIAL',
      'AMEND_UNFILLED',
      'POST_CLOSE_FILLED',
      'POST_CLOSE_PARTIAL',
      'POST_CLOSE_UNFILLED',
    ])
  })

  it('PATTERN_STRINGS has all 9 entries', () => {
    for (const id of PATTERN_IDS) {
      expect(typeof PATTERN_STRINGS[id]).toBe('string')
      expect(PATTERN_STRINGS[id].startsWith('^')).toBe(true)
      expect(PATTERN_STRINGS[id].endsWith('$')).toBe(true)
    }
  })

  it('every fixture valid sample matches the expected pattern and groups', () => {
    for (const sample of fixture.valid) {
      const preview = previewExecutionReport(sample.raw_text)
      expect(preview.patternId, `sample ${sample.name}`).toBe(sample.pattern_id)
      for (const [key, value] of Object.entries(sample.groups)) {
        expect(preview.groups[key], `${sample.name}.${key}`).toBe(value)
      }
    }
  })

  it('valid samples match exactly ONE pattern (no ambiguity)', () => {
    for (const sample of fixture.valid) {
      let matches = 0
      for (const id of PATTERN_IDS) {
        const re = new RegExp(PATTERN_STRINGS[id], 's')
        const m = re.exec(sample.raw_text)
        if (m && m[0] === sample.raw_text) matches += 1
      }
      expect(matches, `sample ${sample.name}`).toBe(1)
    }
  })

  it('every fixture invalid sample is rejected by all 9 patterns', () => {
    for (const sample of fixture.invalid) {
      const preview = previewExecutionReport(sample.raw_text)
      expect(
        preview.patternId,
        `invalid sample ${sample.name} should match no pattern`,
      ).toBeNull()
    }
  })

  it('TEMPLATES expose 5 entries with non-empty placeholders', () => {
    expect(TEMPLATES.length).toBe(5)
    for (const tpl of TEMPLATES) {
      expect(tpl.id.length).toBeGreaterThan(0)
      expect(tpl.label.length).toBeGreaterThan(0)
      expect(tpl.placeholder.length).toBeGreaterThan(0)
      // Each placeholder must itself be a valid report (sanity).
      const preview = previewExecutionReport(tpl.placeholder)
      expect(preview.patternId, `template ${tpl.id} placeholder must parse`).not.toBeNull()
    }
  })

  it('empty input never matches any pattern', () => {
    expect(previewExecutionReport('').patternId).toBeNull()
  })

  it('codex cycle 1 P2: leading + trailing whitespace is tolerated', () => {
    const sample = fixture.valid[0]
    // Paste-style mistake — operator copies the report with trailing
    // newline / extra spaces. Backend ``_normalise`` trims; the
    // preview must mirror that so the submit button does not stall
    // on a report the backend would happily accept.
    const padded = `   ${sample.raw_text}\n  `
    const preview = previewExecutionReport(padded)
    expect(preview.patternId).toBe(sample.pattern_id)
  })

  it('codex cycle 1 P2: multiple inner spaces collapse to one', () => {
    const sample = fixture.valid[0]
    const collapsed = sample.raw_text.replace(/ /g, '   ')
    const preview = previewExecutionReport(collapsed)
    expect(preview.patternId).toBe(sample.pattern_id)
  })

  it('normalizeForPreview trims and collapses runs of ASCII spaces', () => {
    expect(normalizeForPreview('  hello   world\t\t')).toBe('hello world')
    // newlines internal to the body are preserved for UNFILLED reason.
    expect(normalizeForPreview('foo\nbar')).toBe('foo\nbar')
    // surrounding newlines are trimmed.
    expect(normalizeForPreview('\nfoo bar\n')).toBe('foo bar')
  })

  it('codex cycle 2 P2: mixed newline+indent boundary noise is fully trimmed', () => {
    // Paste-style mistake where the operator copies a report wrapped in
    // a blockquote — leading newline followed by indent spaces and a
    // matching trailing pair. Earlier 3-stage shape left this partially
    // un-trimmed, blocking valid input.
    const sample = fixture.valid[0]
    const wrapped = ` \n  ${sample.raw_text}  \n `
    const preview = previewExecutionReport(wrapped)
    expect(preview.patternId, 'wrapped sample should still parse').toBe(
      sample.pattern_id,
    )
    // normalizeForPreview itself should drop the surrounding noise.
    expect(normalizeForPreview(wrapped)).toBe(sample.raw_text)
  })

  it('groups object is frozen on match', () => {
    const sample = fixture.valid[0]
    const preview = previewExecutionReport(sample.raw_text)
    expect(Object.isFrozen(preview.groups)).toBe(true)
  })
})
