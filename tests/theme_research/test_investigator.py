"""Bounded investigator invariants (Y-002) — allowlist, budget, capture, parse."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest

from backend.theme_research.investigator import (
    InvestigatorBudget,
    InvestigatorError,
    LlmCompletion,
    ResearchRequest,
    ThemeInvestigator,
    WebFetchResult,
)
from backend.theme_research.provenance import ThemeResearchStore, theme_sha256
from backend.theme_research.sop_schema import (
    ChokePointFinding,
    SourceCitation,
    ThemeCandidate,
    ThemeResearchOutput,
)

_T = datetime(2026, 6, 11, 1, 0, tzinfo=UTC)
_ALLOWLIST = ("www.gov.cn", "www.miit.gov.cn")
_PROMPT = "version: v1\nfirst_principles_reverse_deduction\n"
_PROMPT_HASH = theme_sha256(_PROMPT.encode("utf-8"))


class StubFetcher:
    def __init__(self, pages: dict[str, bytes]) -> None:
        self._pages = pages

    def fetch(self, url: str) -> WebFetchResult:
        return WebFetchResult(
            url=url,
            domain=urlparse(url).netloc,
            http_status=200,
            raw_bytes=self._pages[url],
            encoding="utf-8",
        )


class StubLlm:
    def __init__(self, text: str, *, tokens: int = 100, model: str = "stub") -> None:
        self._text = text
        self._tokens = tokens
        self._model = model

    def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion:
        return LlmCompletion(
            text=self._text,
            raw_bytes=self._text.encode("utf-8"),
            model=self._model,
            tokens_used=self._tokens,
        )


class StubReserver:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.calls = 0

    def reserve(self, estimated_tokens: int) -> bool:
        self.calls += 1
        return self.allow


def _output_json(
    snippet_sha: str, *, code: str = "600519", n_candidates: int = 1
) -> str:
    cite = (SourceCitation(source_domain="www.gov.cn", snippet_sha256=snippet_sha),)
    candidates = tuple(
        ThemeCandidate(
            code=f"{int(code) + i:06d}",
            sector="半导体",
            chain_link="光刻机",
            rationale="代表",
            confidence=0.7,
            citations=cite,
        )
        for i in range(n_candidates)
    )
    out = ThemeResearchOutput(
        trend_direction="国产替代",
        beneficiary_sectors=("半导体设备",),
        chain_links=("光刻机",),
        chokepoints=(
            ChokePointFinding(
                chain_link="光刻机", rationale="难", confidence=0.8, citations=cite
            ),
        ),
        candidates=candidates,
        overall_confidence=0.6,
        trend_citations=cite,
    )
    return out.model_dump_json()


def _investigator(
    tmp_path,
    *,
    fetcher: StubFetcher,
    llm: StubLlm,
    reserver: StubReserver | None = None,
    budget: InvestigatorBudget | None = None,
    clock=None,
) -> ThemeInvestigator:
    return ThemeInvestigator(
        prompt_registry_content=_PROMPT,
        prompt_version_hash=_PROMPT_HASH,
        store=ThemeResearchStore(tmp_path),
        budget=budget or InvestigatorBudget(),
        web_fetcher=fetcher,
        llm_client=llm,
        usage_reserver=reserver or StubReserver(),
        source_allowlist=_ALLOWLIST,
        time_source=clock or (lambda: 0.0),
    )


def _request(urls: tuple[str, ...]) -> ResearchRequest:
    return ResearchRequest(
        run_id="run-1", started_at=_T, theme_hint="半导体", seed_urls=urls
    )


def test_happy_path_promotable_with_candidates(tmp_path) -> None:
    page = b"policy text from gov"
    url = "https://www.gov.cn/policy"
    snippet_sha = theme_sha256(page)
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(snippet_sha)),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is not None
    assert result.output.candidates[0].code == "600519"
    assert result.promotable, result.aborted_reason


def test_non_allowlisted_domain_aborts(tmp_path) -> None:
    url = "https://evil.example.com/inject"
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: b"x"}),
        llm=StubLlm(_output_json("a" * 64)),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert not result.promotable
    assert "non-allowlisted" in result.aborted_reason


def test_budget_reservation_refused_aborts(tmp_path) -> None:
    page = b"p"
    url = "https://www.gov.cn/p"
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(theme_sha256(page))),
        reserver=StubReserver(allow=False),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "budget" in result.aborted_reason


def test_malformed_output_yields_no_candidates(tmp_path) -> None:
    page = b"p"
    url = "https://www.gov.cn/p"
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm("this is adversarial free prose, not JSON"),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert not result.promotable
    assert "strict schema" in result.aborted_reason


def test_too_many_seed_urls_aborts(tmp_path) -> None:
    urls = tuple(f"https://www.gov.cn/{i}" for i in range(3))
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({u: b"x" for u in urls}),
        llm=StubLlm(_output_json("a" * 64)),
        budget=InvestigatorBudget(max_web_fetches_per_run=2),
    )
    result = inv.investigate(_request(urls))
    assert result.output is None
    assert "max_web_fetches_per_run" in result.aborted_reason


def test_timeout_aborts(tmp_path) -> None:
    page = b"p"
    url = "https://www.gov.cn/p"
    ticks = iter([0.0, 999.0, 999.0, 999.0])
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(theme_sha256(page))),
        budget=InvestigatorBudget(timeout_seconds=10.0),
        clock=lambda: next(ticks),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "timeout" in result.aborted_reason


def test_tokens_over_budget_aborts(tmp_path) -> None:
    page = b"p"
    url = "https://www.gov.cn/p"
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(theme_sha256(page)), tokens=999_999),
        budget=InvestigatorBudget(max_tokens_per_run=1000),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "max_tokens_per_run" in result.aborted_reason


def test_cited_snippet_not_captured_is_non_promotable(tmp_path) -> None:
    """Adversarial: LLM cites a snippet hash that was never fetched."""
    page = b"real page"
    url = "https://www.gov.cn/p"
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json("9" * 64)),  # cites a hash we never captured
    )
    result = inv.investigate(_request((url,)))
    assert result.output is not None  # parsed fine
    assert not result.promotable  # but cannot be pinned


class RedirectFetcher:
    """Fetcher whose returned URL redirects to a non-allowlisted final domain."""

    def __init__(self, final_url: str, payload: bytes) -> None:
        self._final_url = final_url
        self._payload = payload

    def fetch(self, url: str) -> WebFetchResult:
        # Pretends the allowlisted seed redirected elsewhere; even reports an
        # allowlisted domain string — the investigator must derive its own.
        return WebFetchResult(
            url=self._final_url,
            domain="www.gov.cn",  # lie
            http_status=200,
            raw_bytes=self._payload,
            encoding="utf-8",
        )


def test_redirect_to_non_allowlisted_domain_aborts(tmp_path) -> None:
    """Adversarial: an allowlisted seed redirects to an off-allowlist site."""
    seed = "https://www.gov.cn/policy"
    inv = _investigator(
        tmp_path,
        fetcher=RedirectFetcher("https://evil.example.com/x", b"adversarial"),
        llm=StubLlm(_output_json("a" * 64)),
    )
    result = inv.investigate(_request((seed,)))
    assert result.output is None
    assert "redirect bypass" in result.aborted_reason


def test_prompt_hash_must_match_content(tmp_path) -> None:
    """A prompt_version_hash that does not hash the prompt bytes is refused."""
    with pytest.raises(InvestigatorError, match="!= sha256"):
        ThemeInvestigator(
            prompt_registry_content=_PROMPT,
            prompt_version_hash="a" * 64,  # does not match sha256(_PROMPT)
            store=ThemeResearchStore(tmp_path),
            budget=InvestigatorBudget(),
            web_fetcher=StubFetcher({}),
            llm_client=StubLlm("{}"),
            usage_reserver=StubReserver(),
            source_allowlist=_ALLOWLIST,
        )


def test_redirect_status_aborts(tmp_path) -> None:
    """A no-follow fetcher surfacing a 3xx redirect is refused (no chase)."""

    class RedirectStatusFetcher:
        def fetch(self, url: str) -> WebFetchResult:
            return WebFetchResult(
                url=url,
                domain="www.gov.cn",
                http_status=302,
                raw_bytes=b"redirect",
                encoding="utf-8",
            )

    inv = _investigator(
        tmp_path,
        fetcher=RedirectStatusFetcher(),
        llm=StubLlm(_output_json("a" * 64)),
    )
    result = inv.investigate(_request(("https://www.gov.cn/p",)))
    assert result.output is None
    assert "redirect status" in result.aborted_reason


def test_deadline_before_llm_aborts_without_raising(tmp_path) -> None:
    """A deadline already blown before the LLM call returns _aborted, not raise."""
    page = b"p"
    url = "https://www.gov.cn/p"
    # start=0, fetch-check=0, pre-LLM-check=999 -> abort before the LLM call
    ticks = iter([0.0, 0.0, 999.0, 999.0])
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(theme_sha256(page))),
        budget=InvestigatorBudget(timeout_seconds=10.0),
        clock=lambda: next(ticks),
    )
    result = inv.investigate(_request((url,)))  # must not raise
    assert result.output is None
    assert "before the LLM call" in result.aborted_reason


def test_deadline_rechecked_after_llm(tmp_path) -> None:
    """A slow LLM call that returns past the deadline aborts before promotion."""
    page = b"p"
    url = "https://www.gov.cn/p"
    # start=0, fetch-check=0, pre-LLM-check=0, post-LLM-check=999 -> abort there
    ticks = iter([0.0, 0.0, 0.0, 999.0])
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(theme_sha256(page))),
        budget=InvestigatorBudget(timeout_seconds=10.0),
        clock=lambda: next(ticks),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "during the LLM call" in result.aborted_reason


def test_over_budget_response_is_captured(tmp_path) -> None:
    """Over-budget run still captures the produced response bytes (audit)."""
    page = b"p"
    url = "https://www.gov.cn/p"
    store = ThemeResearchStore(tmp_path)
    text = _output_json(theme_sha256(page))
    inv = ThemeInvestigator(
        prompt_registry_content=_PROMPT,
        prompt_version_hash=_PROMPT_HASH,
        store=store,
        budget=InvestigatorBudget(max_tokens_per_run=1000),
        web_fetcher=StubFetcher({url: page}),
        llm_client=StubLlm(text, tokens=999_999),
        usage_reserver=StubReserver(),
        source_allowlist=_ALLOWLIST,
        time_source=lambda: 0.0,
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "max_tokens_per_run" in result.aborted_reason
    # the response bytes were captured before the abort
    assert store.get_payload(theme_sha256(text.encode("utf-8"))) == text.encode("utf-8")


def test_too_many_candidates_aborts(tmp_path) -> None:
    page = b"p"
    url = "https://www.gov.cn/p"
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(_output_json(theme_sha256(page), code="600001", n_candidates=4)),
        budget=InvestigatorBudget(max_candidates_per_artifact=2),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "max_candidates_per_artifact" in result.aborted_reason


def test_fetch_exception_becomes_aborted_run(tmp_path) -> None:
    """An ordinary fetcher failure aborts the run, it does not propagate."""

    class FailingFetcher:
        def fetch(self, url: str) -> WebFetchResult:
            raise ConnectionError("network down")

    inv = _investigator(
        tmp_path,
        fetcher=FailingFetcher(),
        llm=StubLlm(_output_json("a" * 64)),
    )
    result = inv.investigate(_request(("https://www.gov.cn/p",)))  # must not raise
    assert result.output is None
    assert "fetch of" in result.aborted_reason and "failed" in result.aborted_reason


def test_citation_domain_mismatch_is_non_promotable(tmp_path) -> None:
    """A citation whose domain ≠ the captured snapshot's domain is not promotable."""
    page = b"policy bytes"
    url = "https://www.gov.cn/p"  # captured domain = www.gov.cn
    snippet_sha = theme_sha256(page)
    cite = (
        SourceCitation(source_domain="www.miit.gov.cn", snippet_sha256=snippet_sha),
    )
    out = ThemeResearchOutput(
        trend_direction="国产替代",
        beneficiary_sectors=("半导体设备",),
        chain_links=("光刻机",),
        chokepoints=(
            ChokePointFinding(
                chain_link="光刻机", rationale="难", confidence=0.8, citations=cite
            ),
        ),
        candidates=(
            ThemeCandidate(
                code="600519",
                sector="半导体",
                chain_link="光刻机",
                rationale="代表",
                confidence=0.7,
                citations=cite,
            ),
        ),
        overall_confidence=0.6,
        trend_citations=cite,
    )
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=StubLlm(out.model_dump_json()),
    )
    result = inv.investigate(_request((url,)))
    assert result.output is not None  # parsed fine + bytes captured
    assert not result.promotable  # but domain mismatch blocks promotion
    assert "does not match" in result.aborted_reason or "citation domain" in str(
        result.aborted_reason
    )


def test_llm_failure_becomes_aborted_run(tmp_path) -> None:
    """A provider/network LLM failure aborts auditable, it does not propagate."""
    page = b"p"
    url = "https://www.gov.cn/p"

    class FailingLlm:
        def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion:
            raise RuntimeError("provider 500")

    inv = _investigator(
        tmp_path, fetcher=StubFetcher({url: page}), llm=FailingLlm()
    )
    result = inv.investigate(_request((url,)))  # must not raise
    assert result.output is None
    assert "LLM call failed" in result.aborted_reason


def test_oversized_prompt_aborts_before_llm(tmp_path) -> None:
    """A prompt whose estimated tokens alone exceed the per-run cap aborts."""
    page = b"x" * 60_000  # huge evidence -> prompt estimate blows a small cap
    url = "https://www.gov.cn/p"

    class NeverCalledLlm:
        def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion:
            raise AssertionError("LLM must not be called when the prompt is too big")

    inv = ThemeInvestigator(
        prompt_registry_content=_PROMPT,
        prompt_version_hash=_PROMPT_HASH,
        store=ThemeResearchStore(tmp_path),
        budget=InvestigatorBudget(max_tokens_per_run=1000),
        web_fetcher=StubFetcher({url: page}),
        llm_client=NeverCalledLlm(),
        usage_reserver=StubReserver(),
        source_allowlist=_ALLOWLIST,
        time_source=lambda: 0.0,
    )
    result = inv.investigate(_request((url,)))
    assert result.output is None
    assert "per-run cap" in result.aborted_reason


def test_output_cap_is_budget_minus_prompt(tmp_path) -> None:
    """The LLM output cap is the per-run budget minus the prompt estimate."""
    page = b"x" * 200
    url = "https://www.gov.cn/p"
    seen: dict[str, int] = {}

    class CapCheckLlm:
        def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion:
            seen["max_tokens"] = max_tokens
            text = _output_json(theme_sha256(page))
            return LlmCompletion(text, text.encode("utf-8"), "stub", 10)

    inv = ThemeInvestigator(
        prompt_registry_content=_PROMPT,
        prompt_version_hash=_PROMPT_HASH,
        store=ThemeResearchStore(tmp_path),
        budget=InvestigatorBudget(max_tokens_per_run=40_000),
        web_fetcher=StubFetcher({url: page}),
        llm_client=CapCheckLlm(),
        usage_reserver=StubReserver(),
        source_allowlist=_ALLOWLIST,
        time_source=lambda: 0.0,
    )
    inv.investigate(_request((url,)))
    assert seen["max_tokens"] < 40_000  # reduced by the prompt estimate


def test_unknown_page_encoding_does_not_crash(tmp_path) -> None:
    """A bogus codec label on a captured page falls back, never crashes."""

    class BogusEncodingFetcher:
        def fetch(self, url: str) -> WebFetchResult:
            return WebFetchResult(
                url=url,
                domain="www.gov.cn",
                http_status=200,
                raw_bytes=b"\xff\xfe some bytes",
                encoding="x-not-a-real-codec",
            )

    inv = _investigator(
        tmp_path,
        fetcher=BogusEncodingFetcher(),
        llm=StubLlm(_output_json(theme_sha256(b"\xff\xfe some bytes"))),
    )
    result = inv.investigate(_request(("https://www.gov.cn/p",)))  # must not raise
    assert result.output is not None  # rendered + parsed despite bogus encoding


def test_non_http_scheme_aborts(tmp_path) -> None:
    """A file:// (or other non-HTTP) scheme is refused before any fetch."""
    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({}),
        llm=StubLlm(_output_json("a" * 64)),
    )
    result = inv.investigate(_request(("file:///etc/passwd",)))
    assert result.output is None
    assert "non-HTTP url scheme" in result.aborted_reason


def test_prompt_includes_fetched_evidence_content(tmp_path) -> None:
    """Grounding: the model receives the actual captured page content, not just
    a domain + hash (so a hash cannot be echoed without seeing the evidence)."""
    page = b"UNIQUE_POLICY_MARKER lithography localization mandate"
    url = "https://www.gov.cn/policy"

    seen: dict[str, str] = {}

    class CapturingLlm:
        def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion:
            seen["prompt"] = prompt
            text = _output_json(theme_sha256(page))
            return LlmCompletion(text, text.encode("utf-8"), "stub", 10)

    inv = _investigator(
        tmp_path,
        fetcher=StubFetcher({url: page}),
        llm=CapturingLlm(),
    )
    inv.investigate(_request((url,)))
    assert "UNIQUE_POLICY_MARKER lithography localization mandate" in seen["prompt"]
    assert theme_sha256(page) in seen["prompt"]  # and the sha to cite


def test_run_persisted_to_store(tmp_path) -> None:
    page = b"p"
    url = "https://www.gov.cn/p"
    store = ThemeResearchStore(tmp_path)
    inv = ThemeInvestigator(
        prompt_registry_content=_PROMPT,
        prompt_version_hash=_PROMPT_HASH,
        store=store,
        budget=InvestigatorBudget(),
        web_fetcher=StubFetcher({url: page}),
        llm_client=StubLlm(_output_json(theme_sha256(page))),
        usage_reserver=StubReserver(),
        source_allowlist=_ALLOWLIST,
        time_source=lambda: 0.0,
    )
    result = inv.investigate(_request((url,)))
    # the captured page bytes are retrievable from the store, content-addressed
    assert store.get_payload(theme_sha256(page)) == page
    assert result.run.run_id == "run-1"
