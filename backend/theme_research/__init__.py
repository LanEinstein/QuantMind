"""backend.theme_research — LLM+web 主题研究 peer-sourcing 层 (Phase Y).

定时·留痕·人工 pin 的 source-discovery 层 (P0-8-amendment-2026-06-01 方向①).
量化仍是资格权威; 本层只【追加】主题候选, 永不作全局 universe 过滤器, 永不进
Line-1 实时信号/replay 路径. 见 ``CLAUDE.md``.
"""

from backend.theme_research.candidate_artifact import (
    THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
    THEME_EVIDENCE_PREFIX,
    ThemeCandidateArtifact,
    ThemeCandidateEntry,
    build_theme_evidence_id,
    theme_evidence_text,
)
from backend.theme_research.candidate_registry import (
    ThemeCandidateLockFile,
    ThemeCandidateRegistry,
    ThemeCandidateRegistryError,
)
from backend.theme_research.investigator import (
    InvestigatorBudget,
    InvestigatorError,
    LlmClient,
    LlmCompletion,
    ResearchRequest,
    ThemeInvestigator,
    ThemeResearchResult,
    UsageReserver,
    WebFetcher,
    WebFetchResult,
)
from backend.theme_research.peer_sourcing import (
    PeerSourcedCandidate,
    verify_pinned_candidates,
)
from backend.theme_research.prompts_loader import (
    ThemePromptRegistry,
    ThemePromptRegistryError,
    compute_prompt_sha256,
    validate_sop_skeleton,
)
from backend.theme_research.provenance import (
    ThemeArtifactType,
    ThemeResearchRun,
    ThemeResearchSnapshot,
    ThemeResearchStore,
    theme_sha256,
)
from backend.theme_research.sop_schema import (
    THEME_SOP_STEPS,
    ChokePointFinding,
    SourceCitation,
    ThemeCandidate,
    ThemeResearchOutput,
    ThemeStep,
)

__all__ = [
    "THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION",
    "THEME_EVIDENCE_PREFIX",
    "THEME_SOP_STEPS",
    "ChokePointFinding",
    "InvestigatorBudget",
    "InvestigatorError",
    "LlmClient",
    "LlmCompletion",
    "PeerSourcedCandidate",
    "ResearchRequest",
    "SourceCitation",
    "ThemeArtifactType",
    "ThemeCandidate",
    "ThemeCandidateArtifact",
    "ThemeCandidateEntry",
    "ThemeCandidateLockFile",
    "ThemeCandidateRegistry",
    "ThemeCandidateRegistryError",
    "ThemeInvestigator",
    "ThemePromptRegistry",
    "ThemePromptRegistryError",
    "ThemeResearchOutput",
    "ThemeResearchResult",
    "ThemeResearchRun",
    "ThemeResearchSnapshot",
    "ThemeResearchStore",
    "ThemeStep",
    "UsageReserver",
    "WebFetcher",
    "WebFetchResult",
    "build_theme_evidence_id",
    "compute_prompt_sha256",
    "theme_evidence_text",
    "theme_sha256",
    "validate_sop_skeleton",
    "verify_pinned_candidates",
]
