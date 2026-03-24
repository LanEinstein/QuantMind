/** TypeScript interfaces for agent debate visualization. */

export type EvidenceStatus = 'positive' | 'mixed' | 'negative'

export type ModelLabel = 'DeepSeek' | 'Qwen' | 'MiniMax' | 'MiroFish'

export type AuthMode = 'suggest' | 'confirm' | 'auto'

export type AnalysisStatus = 'pending' | 'running' | 'completed' | 'failed'

export type AgentRole =
  | 'news_crawler'
  | 'sentiment_analyst'
  | 'fundamental_analyst'
  | 'technical_analyst'
  | 'intelligence_officer'
  | 'bull_researcher'
  | 'bear_researcher'
  | 'risk_officer'
  | 'fund_manager'

export type SSEStatus = 'thinking' | 'done'

export interface EvidenceItem {
  readonly label: string
  readonly model: ModelLabel
  readonly status: EvidenceStatus
  readonly detail: string
}

export interface DebateArgument {
  readonly role: 'bull' | 'bear'
  readonly round: number
  readonly content: string
  readonly evidence: readonly EvidenceItem[]
  readonly model: ModelLabel
  readonly timestamp: string
}

export interface DebateRound {
  readonly round: number
  readonly bull: DebateArgument | null
  readonly bear: DebateArgument | null
}

export interface RiskCheck {
  readonly label: string
  readonly passed: boolean
}

export interface RiskAssessment {
  readonly model: ModelLabel
  readonly checks: readonly RiskCheck[]
  readonly position_limit: string
  readonly raw_text: string
}

export interface FundManagerDecision {
  readonly model: ModelLabel
  readonly score: number
  readonly score_label: string
  readonly action: string
  readonly target_price: number | null
  readonly stop_loss: number | null
  readonly position_pct: number | null
  readonly reasoning: string
  readonly confidence: number
  readonly risk_score: number
}

export interface AnalysisSummary {
  readonly id: string
  readonly stock_code: string
  readonly stock_name: string
  readonly trade_date: string
  readonly status: AnalysisStatus
  readonly action: string
  readonly score: number
  readonly created_at: string
}

export interface AnalysisDetail {
  readonly id: string
  readonly stock_code: string
  readonly stock_name: string
  readonly trade_date: string
  readonly status: AnalysisStatus
  readonly max_rounds: number
  readonly current_round: number
  readonly debates: readonly DebateRound[]
  readonly risk_assessment: RiskAssessment | null
  readonly decision: FundManagerDecision | null
  readonly created_at: string
}

export interface SSEEvent {
  readonly agent: AgentRole
  readonly round: number
  readonly content: string
  readonly status: SSEStatus
  readonly evidence?: readonly EvidenceItem[]
  readonly timestamp: string
}
