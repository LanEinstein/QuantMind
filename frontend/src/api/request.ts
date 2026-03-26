/** Axios wrapper for QuantMind backend API. */

import axios from 'axios'
import type { AxiosInstance } from 'axios'
import type { ApiEnvelope } from '@/types/market'

const instance: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

instance.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error('API error:', error.response?.status, error.config?.url)
    return Promise.reject(error)
  },
)

/** Typed GET helper. */
export async function apiGet<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const envelope = (await instance.get(url, { params })) as unknown as ApiEnvelope<T>
  if (envelope.status === 'error') {
    throw new Error(envelope.error ?? 'Unknown API error')
  }
  return envelope.data
}

/** Typed POST helper. */
export async function apiPost<T>(url: string, data?: unknown): Promise<T> {
  const envelope = (await instance.post(url, data)) as unknown as ApiEnvelope<T>
  if (envelope.status === 'error') {
    throw new Error(envelope.error ?? 'Unknown API error')
  }
  return envelope.data
}

/** Download helper for binary responses (bypasses envelope unwrapping). */
export async function apiDownload(url: string, params?: Record<string, unknown>): Promise<Blob> {
  const response = await axios.get(url, {
    baseURL: instance.defaults.baseURL,
    timeout: instance.defaults.timeout,
    params,
    responseType: 'blob',
  })
  return response.data as Blob
}

export default instance
