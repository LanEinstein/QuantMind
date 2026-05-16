"""Feishu / Lark integration (P0-2 / P0-4 / F-001..F-006).

The integration surface is intentionally narrow:
* :class:`FeishuClient` — thin async wrapper over ``lark-oapi`` for
  ``POST /open-apis/im/v1/messages`` (F-001).
* :class:`MessageRenderer` — pure-Python template renderer used by every
  outbound Feishu message (F-002 / placeholder).
* WebSocket long-connection receiver (F-003 / placeholder).
* Execution-report parser (F-004 / placeholder).

LLM red line: no module in this package imports
``backend.llm`` / ``backend.agents`` / ``backend.mirofish``
(P0-2 §1.2 — LLM never composes Feishu wire text).
"""
