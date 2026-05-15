/** API client for the InstructionPlan pool + 3-tab reason drawer (G-003). */

import { apiGet } from './request'
import type {
  InstructionPlanDetailPayload,
  InstructionPlanListPayload,
} from '@/types/instructionPlan'

export const instructionPlansApi = {
  list(params: {
    limit?: number
    status?: string
    trade_date?: string
  } = {}): Promise<InstructionPlanListPayload> {
    return apiGet<InstructionPlanListPayload>(
      '/api/instruction-plans',
      params as Record<string, unknown>,
    )
  },

  get(instructionId: string): Promise<InstructionPlanDetailPayload> {
    return apiGet<InstructionPlanDetailPayload>(
      `/api/instruction-plans/${encodeURIComponent(instructionId)}`,
    )
  },
}
