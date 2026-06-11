"""Bounded theme-research investigation job (Y-002).

The job body of the timed (盘前/周频), non-realtime peer-sourcing layer
(P0-8-amendment-2026-06-01 §2.1/§2.2/§2.9). It is NEVER on the Line-1 signal /
runtime / replay path — it runs as a scheduled job, captures every byte, and
hands a strict-validated structured output to the human-pin stage (Y-004).

Red-line-safe by construction:

* **Injected clients.** Web + LLM access arrive as Protocols, so the deterministic
  logic (allowlist enforcement, byte capture, budget bounds, strict-schema parse,
  promotability) is fully testable offline and this module never reaches into the
  trading stack. The investigator holds NO secrets / trading state / RiskConfig
  (§2.8 containment) — it only knows its injected clients + the source allowlist.
* **Source allowlist.** Any fetch to a non-allowlisted domain is refused BEFORE
  the fetcher is called (fail-closed) — web text is adversarial data.
* **Full provenance.** Every SERP/page/prompt/response byte is captured into the
  content-addressed store; a run that could not capture is non-promotable.
* **Cost-bounded.** max web fetches / LLM calls / tokens / timeout per run, and
  every LLM reservation goes through the shared ``llm:usage:{utc_date}`` counter
  (injected reserver); a refused reservation aborts the run (¥100/日 hard).
* **Evidence-only output.** The LLM produces JSON validated against
  :class:`ThemeResearchOutput`; malformed output yields NO candidates (fail-closed).
  Nothing here writes a decision field.

Allowed imports: knowledge_graph (read pinned KG), the local schema/provenance/
loader. NOT ``backend.{risk,broker,api,data}`` — keep the trading stack pure.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple, Protocol
from urllib.parse import urlparse

import structlog
from pydantic import ValidationError

from backend.theme_research.provenance import (
    ThemeArtifactType,
    ThemeResearchRun,
    ThemeResearchSnapshot,
    ThemeResearchStore,
    theme_sha256,
)
from backend.theme_research.sop_schema import SourceCitation, ThemeResearchOutput

log = structlog.get_logger(component="theme_research.investigator")

# Conservative chars→tokens ratio for budget reservation. Chinese text is ~1.5-2
# chars/token; using 2 over-reserves slightly (fail-closed) so the ¥100/日 cap is
# never crossed by an undercounted prompt.
_CHARS_PER_TOKEN = 2


class InvestigatorError(RuntimeError):
    """Raised when a run hits a fail-closed boundary (budget / allowlist)."""


@dataclass(frozen=True)
class InvestigatorBudget:
    """Per-run + per-day cost bounds (P1-7 sustained; §2.9).

    ``cadence`` documents the non-realtime scheduling intent ("preopen" /
    "weekly"); the scheduler owns enforcement of ``max_runs_per_day`` across the
    day, the investigator enforces every per-run bound here.
    """

    cadence: str = "preopen"
    max_runs_per_day: int = 1
    max_web_fetches_per_run: int = 12
    max_llm_calls_per_run: int = 3
    max_tokens_per_run: int = 40_000
    timeout_seconds: float = 120.0
    max_candidates_per_artifact: int = 8

    def __post_init__(self) -> None:
        for name in (
            "max_runs_per_day",
            "max_web_fetches_per_run",
            "max_llm_calls_per_run",
            "max_tokens_per_run",
            "max_candidates_per_artifact",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive int, got {value!r}")
        if not (self.timeout_seconds > 0):
            raise ValueError(
                f"timeout_seconds must be > 0, got {self.timeout_seconds!r}"
            )


class WebFetchResult(NamedTuple):
    """One web fetch's bytes + provenance, as returned by an injected fetcher."""

    url: str
    domain: str
    http_status: int
    raw_bytes: bytes
    encoding: str


class WebFetcher(Protocol):
    """Injected web client. The investigator gates the URL by allowlist first.

    Contract (codex Y P1 — prevents off-allowlist I/O, not just off-allowlist
    *promotion*): a conforming fetcher MUST NOT transparently follow redirects to
    another host. It either (a) does not follow redirects at all and returns the
    3xx response as-is (the investigator then refuses to chase it), or (b)
    re-checks the source allowlist on every ``Location`` hop before fetching it.
    The investigator additionally re-derives + re-checks the final domain as
    defense-in-depth, but the prevention of the network I/O itself lives here.
    """

    def fetch(self, url: str) -> WebFetchResult: ...


class LlmCompletion(NamedTuple):
    """One LLM completion's text + raw bytes + token count."""

    text: str
    raw_bytes: bytes
    model: str
    tokens_used: int


class LlmClient(Protocol):
    """Injected LLM client (the only place an LLM is called in this layer)."""

    def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion: ...


class UsageReserver(Protocol):
    """Injected ``llm:usage:{utc_date}`` reserver — the ¥100/日 hard gate.

    ``reserve`` returns False when the reservation would cross the daily hard
    cap; the investigator then aborts fail-closed (no cross-threshold calls).
    """

    def reserve(self, estimated_tokens: int) -> bool: ...


@dataclass(frozen=True)
class ResearchRequest:
    """One scheduled investigation's inputs (deterministic; time injected)."""

    run_id: str
    started_at: datetime
    theme_hint: str
    seed_urls: tuple[str, ...] = ()
    prompt_version_hash: str = ""


@dataclass(frozen=True)
class ThemeResearchResult:
    """A run's product: provenance run + validated output (or a fail reason)."""

    run: ThemeResearchRun
    output: ThemeResearchOutput | None
    promotable: bool
    aborted_reason: str = ""
    snapshots: tuple[ThemeResearchSnapshot, ...] = field(default_factory=tuple)


class ThemeInvestigator:
    """Runs one bounded SOP investigation; pure logic over injected clients."""

    def __init__(
        self,
        *,
        prompt_registry_content: str,
        prompt_version_hash: str,
        store: ThemeResearchStore,
        budget: InvestigatorBudget,
        web_fetcher: WebFetcher,
        llm_client: LlmClient,
        usage_reserver: UsageReserver,
        source_allowlist: Sequence[str],
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self._prompt = prompt_registry_content
        self._prompt_version_hash = prompt_version_hash
        self._store = store
        self._budget = budget
        self._web = web_fetcher
        self._llm = llm_client
        self._reserver = usage_reserver
        self._allowlist = frozenset(source_allowlist)
        self._now = time_source
        # Tie the recorded prompt version to the ACTUAL prompt bytes (codex Y P2):
        # a stale/approved hash paired with different content would let an
        # unapproved SOP prompt masquerade as an approved version in later audit /
        # pin checks. Reject the mismatch at construction, fail-closed.
        expected = theme_sha256(prompt_registry_content.encode("utf-8"))
        if prompt_version_hash != expected:
            raise InvestigatorError(
                f"prompt_version_hash {prompt_version_hash} != sha256(prompt "
                f"content) {expected}; refusing to record a mismatched version"
            )

    def investigate(self, request: ResearchRequest) -> ThemeResearchResult:
        """Run one bounded investigation; fail-closed on any boundary breach."""
        start = self._now()
        snapshots: list[ThemeResearchSnapshot] = []

        # 1. Fetch allowlisted seed URLs (capture every byte; bound the count).
        try:
            self._fetch_evidence(request, snapshots, start)
        except InvestigatorError as exc:
            return self._aborted(request, snapshots, str(exc))

        # 2. Render + capture the prompt bytes (the exact prompt we sent).
        prompt_text = self._render_prompt(request, snapshots)
        self._capture(
            ThemeArtifactType.PROMPT,
            prompt_text.encode("utf-8"),
            request.started_at,
            snapshots,
        )

        # 3. Budget-reserve + call the LLM; capture the response bytes.
        # ``max_tokens_per_run`` is the per-run TOTAL (input + output) cap. Estimate
        # the prompt input tokens (codex Y P1 — the evidence-laden prompt costs too)
        # and enforce the cap BEFORE the call (codex Y P2): if the prompt alone
        # blows the budget, abort; otherwise the output is capped to the remainder
        # so prompt+output can never cross the ¥100/日 reservation.
        prompt_tokens = len(prompt_text) // _CHARS_PER_TOKEN
        if prompt_tokens >= self._budget.max_tokens_per_run:
            return self._aborted(
                request,
                snapshots,
                f"estimated prompt {prompt_tokens} tokens >= per-run cap "
                f"{self._budget.max_tokens_per_run} (no room for output)",
            )
        if not self._reserver.reserve(self._budget.max_tokens_per_run):
            return self._aborted(
                request, snapshots, "daily LLM budget reservation refused (¥100 hard)"
            )
        output_cap = self._budget.max_tokens_per_run - prompt_tokens
        # Pre-LLM deadline check returns _aborted (codex Y P2) rather than raising
        # an uncaught exception — every boundary breach yields a structured result.
        if self._now() - start > self._budget.timeout_seconds:
            return self._aborted(
                request,
                snapshots,
                f"run exceeded timeout_seconds {self._budget.timeout_seconds} "
                f"before the LLM call",
            )
        try:
            completion = self._llm.complete(
                prompt=prompt_text, max_tokens=output_cap
            )
        except Exception as exc:  # noqa: BLE001 — any LLM failure aborts the run
            # A provider/network/rate-limit failure must become an auditable
            # aborted run, not an uncaught exception (codex Y P2) — the fetch path
            # already does this; the LLM path must too so scheduled jobs fail closed.
            return self._aborted(
                request,
                snapshots,
                f"LLM call failed: {type(exc).__name__}: {exc}",
            )
        # Capture the response bytes BEFORE any abort check (codex Y P2): the
        # model already produced them, so the audit path must keep every byte
        # even when the run is rejected for being over budget.
        self._capture(
            ThemeArtifactType.LLM_RESPONSE,
            completion.raw_bytes,
            request.started_at,
            snapshots,
            model=completion.model,
        )
        if completion.tokens_used > self._budget.max_tokens_per_run:
            return self._aborted(
                request,
                snapshots,
                f"LLM used {completion.tokens_used} > "
                f"max_tokens_per_run {self._budget.max_tokens_per_run}",
            )
        # Re-check the deadline AFTER the call returns (codex Y P2): the per-run
        # timeout only gated starting the call, so a slow completion that still
        # came back under-token could otherwise be parsed + promoted past the
        # bound. The response bytes are already captured above (audit intact).
        if self._now() - start > self._budget.timeout_seconds:
            return self._aborted(
                request,
                snapshots,
                f"run exceeded timeout_seconds {self._budget.timeout_seconds} "
                f"during the LLM call",
            )

        # 4. Strict-parse the structured output; malformed ⇒ no candidates.
        output, parse_reason = self._parse_output(completion.text)
        if output is not None and (
            len(output.candidates) > self._budget.max_candidates_per_artifact
        ):
            # Fail-closed: an over-cap response is dropped wholesale rather than
            # silently truncated, keeping the job bounded as configured (codex Y P2).
            return self._aborted(
                request,
                snapshots,
                f"{len(output.candidates)} candidates > max_candidates_per_artifact "
                f"{self._budget.max_candidates_per_artifact}",
            )
        run = self._build_run(request, snapshots, output)
        self._store.put_run(run)
        # Single source of truth: is_promotable() now covers byte-capture AND
        # citation-domain consistency, so the durable record cannot disagree with
        # a later consumer's verdict (codex Y P1).
        promotable, reason = run.is_promotable()
        log.info(
            "theme_investigation_done",
            run_id=request.run_id,
            promotable=promotable,
            reason=reason if not promotable else parse_reason,
            candidates=len(output.candidates) if output else 0,
        )
        # Surface WHY a run is unusable: a parse failure (output is None) or a
        # non-promotable reason (uncited / domain-mismatch / missing bytes).
        if output is None:
            final_reason = parse_reason
        elif not promotable:
            final_reason = reason
        else:
            final_reason = ""
        return ThemeResearchResult(
            run=run,
            output=output,
            promotable=promotable,
            aborted_reason=final_reason,
            snapshots=tuple(snapshots),
        )

    # -- internals ----------------------------------------------------------

    def _fetch_evidence(
        self,
        request: ResearchRequest,
        snapshots: list[ThemeResearchSnapshot],
        start: float,
    ) -> None:
        if len(request.seed_urls) > self._budget.max_web_fetches_per_run:
            raise InvestigatorError(
                f"{len(request.seed_urls)} seed URLs > max_web_fetches_per_run "
                f"{self._budget.max_web_fetches_per_run}"
            )
        for url in request.seed_urls:
            self._check_deadline(start)
            parsed = urlparse(url)
            # Only http(s) (codex Y P2): reject file://, ftp://, data://, gopher://
            # etc. before any allowlist/fetch so a scheme trick cannot reach the
            # local filesystem or an internal service (SSRF containment).
            if parsed.scheme not in ("http", "https"):
                raise InvestigatorError(
                    f"refused non-HTTP url scheme {parsed.scheme!r} (url {url!r})"
                )
            domain = parsed.netloc
            if domain not in self._allowlist:
                raise InvestigatorError(
                    f"refused non-allowlisted source {domain!r} (url {url!r}); "
                    f"web text is adversarial — only allowlisted sources"
                )
            try:
                result = self._web.fetch(url)
            except Exception as exc:  # noqa: BLE001 — any fetch failure aborts
                # An ordinary network/parse failure must become a structured
                # aborted run (codex Y P2), not an uncaught exception out of
                # investigate(); the job is fail-closed at every boundary.
                raise InvestigatorError(
                    f"fetch of {url!r} failed: {type(exc).__name__}: {exc}"
                ) from exc
            # A no-follow fetcher surfaces a redirect as a 3xx — refuse to chase
            # it (codex Y P1: do not let an allowlisted seed bounce us onward).
            if 300 <= result.http_status < 400:
                raise InvestigatorError(
                    f"fetch of {url!r} returned redirect status "
                    f"{result.http_status}; refusing to follow (allowlist)"
                )
            # Re-check the FINAL domain derived from the returned URL (defense in
            # depth): a follow-redirect fetcher that landed off-allowlist would
            # otherwise smuggle adversarial bytes past the pre-fetch check. Derive
            # the domain from result.url ourselves — never trust result.domain.
            final_domain = urlparse(result.url).netloc
            if final_domain not in self._allowlist:
                raise InvestigatorError(
                    f"fetch of {url!r} resolved to non-allowlisted final domain "
                    f"{final_domain!r} (redirect bypass refused)"
                )
            self._capture(
                ThemeArtifactType.PAGE,
                result.raw_bytes,
                request.started_at,
                snapshots,
                source_url=result.url,
                source_domain=final_domain,
                http_status=result.http_status,
                encoding=result.encoding,
            )

    #: Per-page content excerpt cap in the prompt (keeps the prompt bounded; the
    #: FULL page is still byte-captured in the store, and the cited hash is the
    #: full-page hash).
    _MAX_EVIDENCE_CHARS = 8000

    def _render_prompt(
        self, request: ResearchRequest, snapshots: list[ThemeResearchSnapshot]
    ) -> str:
        # Give the model the ACTUAL captured evidence content, not just a domain +
        # hash (codex Y P1): otherwise an LLM could echo a hash it saw and produce
        # an ungrounded candidate that still passes the byte-captured promotability
        # check. The cited hash maps to the full captured page; the excerpt here is
        # bounded only to keep the prompt within token budget.
        blocks: list[str] = []
        for s in snapshots:
            if s.artifact_type != ThemeArtifactType.PAGE:
                continue
            try:
                text = s.raw_payload.decode(s.encoding, errors="replace")
            except LookupError:
                # An unknown/invalid codec label must not crash the run (codex Y
                # P2) — fall back to utf-8 replacement so rendering is total.
                text = s.raw_payload.decode("utf-8", errors="replace")
            excerpt = text[: self._MAX_EVIDENCE_CHARS]
            blocks.append(
                f"## source={s.source_domain} sha256={s.raw_payload_sha256}\n"
                f"{excerpt}"
            )
        evidence = "\n\n".join(blocks)
        return (
            f"{self._prompt}\n\n# THEME HINT\n{request.theme_hint}\n\n"
            f"# CAPTURED EVIDENCE (allowlisted, byte-pinned; cite the sha256 of "
            f"the source you used)\n{evidence}\n"
        )

    def _capture(
        self,
        artifact_type: ThemeArtifactType,
        raw_bytes: bytes,
        fetch_time: datetime,
        snapshots: list[ThemeResearchSnapshot],
        *,
        source_url: str = "",
        source_domain: str = "",
        http_status: int | None = None,
        model: str = "",
        encoding: str = "utf-8",
    ) -> ThemeResearchSnapshot:
        snap = ThemeResearchSnapshot.create(
            artifact_type=artifact_type,
            raw_payload=raw_bytes,
            encoding=encoding,
            compression="none",
            fetch_time_utc=fetch_time,
            source_url=source_url,
            source_domain=source_domain,
            http_status=http_status,
            model=model,
        )
        self._store.put_snapshot(snap)
        snapshots.append(snap)
        return snap

    @staticmethod
    def _parse_output(text: str) -> tuple[ThemeResearchOutput | None, str]:
        try:
            return ThemeResearchOutput.model_validate_json(text), "ok"
        except (ValidationError, ValueError) as exc:
            # Fail-closed: an unparseable / schema-violating LLM output yields NO
            # candidates rather than a best-effort parse of adversarial text.
            return None, f"output failed strict schema: {exc}"

    @staticmethod
    def _iter_citations(output: ThemeResearchOutput) -> list[SourceCitation]:
        cites: list[SourceCitation] = list(output.trend_citations)
        for cp in output.chokepoints:
            cites.extend(cp.citations)
        for cand in output.candidates:
            cites.extend(cand.citations)
        return cites

    def _build_run(
        self,
        request: ResearchRequest,
        snapshots: list[ThemeResearchSnapshot],
        output: ThemeResearchOutput | None,
    ) -> ThemeResearchRun:
        output_sha = (
            theme_sha256(output.model_dump_json(indent=None).encode("utf-8"))
            if output is not None
            else ""
        )
        captured_pages = tuple(
            (s.source_domain, s.raw_payload_sha256)
            for s in snapshots
            if s.artifact_type == ThemeArtifactType.PAGE
        )
        cited_pages = (
            tuple(
                (c.source_domain, c.snippet_sha256)
                for c in self._iter_citations(output)
            )
            if output is not None
            else ()
        )
        return ThemeResearchRun(
            run_id=request.run_id,
            started_at=request.started_at,
            prompt_version_hash=self._prompt_version_hash,
            snapshot_ids=tuple(s.snapshot_id for s in snapshots),
            captured_types=tuple(s.artifact_type for s in snapshots),
            captured_pages=captured_pages,
            output_sha256=output_sha,
            cited_pages=cited_pages,
        )

    def _aborted(
        self,
        request: ResearchRequest,
        snapshots: list[ThemeResearchSnapshot],
        reason: str,
    ) -> ThemeResearchResult:
        run = self._build_run(request, snapshots, None)
        try:
            self._store.put_run(run)
        except ValueError:
            pass  # duplicate run_id on abort path — record best-effort
        log.warning("theme_investigation_aborted", run_id=request.run_id, reason=reason)
        return ThemeResearchResult(
            run=run,
            output=None,
            promotable=False,
            aborted_reason=reason,
            snapshots=tuple(snapshots),
        )

    def _check_deadline(self, start: float) -> None:
        if self._now() - start > self._budget.timeout_seconds:
            raise InvestigatorError(
                f"run exceeded timeout_seconds {self._budget.timeout_seconds}"
            )


__all__ = [
    "InvestigatorBudget",
    "InvestigatorError",
    "LlmClient",
    "LlmCompletion",
    "ResearchRequest",
    "ThemeInvestigator",
    "ThemeResearchResult",
    "UsageReserver",
    "WebFetchResult",
    "WebFetcher",
]
