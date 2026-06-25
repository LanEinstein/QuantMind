/**
 * F3 (production-hardening 2026-06-25): coerce a possibly-missing numeric
 * WS/API field to a finite number.
 *
 * Live position/order/trade rows arrive over the WebSocket / REST API; if the
 * backend ever omits a numeric field (or sends ``null`` / ``NaN``), a bare
 * ``row.x.toFixed(2)`` throws and crash-blanks the ENTIRE table for a days-open
 * dashboard. Routing every render-time numeric through ``num()`` degrades a
 * single bad field to ``0`` instead of taking down the whole view.
 */
export function num(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}
