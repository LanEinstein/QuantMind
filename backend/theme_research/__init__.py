"""backend.theme_research — LLM+web 主题研究 peer-sourcing 层 (Phase Y).

定时·留痕·人工 pin 的 source-discovery 层 (P0-8-amendment-2026-06-01 方向①).
量化仍是资格权威; 本层只【追加】主题候选, 永不作全局 universe 过滤器, 永不进
Line-1 实时信号/replay 路径. 见 ``CLAUDE.md``.
"""

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
    "THEME_SOP_STEPS",
    "ChokePointFinding",
    "InvestigatorBudget",
    "InvestigatorError",
    "LlmClient",
    "LlmCompletion",
    "ResearchRequest",
    "SourceCitation",
    "ThemeArtifactType",
    "ThemeCandidate",
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
    "compute_prompt_sha256",
    "theme_sha256",
    "validate_sop_skeleton",
]
