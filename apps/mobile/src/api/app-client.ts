import { secureTokenStore } from '../auth/token-store';
import { API_URL } from '../config';
import { ApiClient } from './client';

let sessionExpiredListener: (() => void) | null = null;

export const appClient = new ApiClient(API_URL, secureTokenStore, () => {
  sessionExpiredListener?.();
});

export function setSessionExpiredListener(listener: () => void): () => void {
  sessionExpiredListener = listener;
  return () => {
    if (sessionExpiredListener === listener) sessionExpiredListener = null;
  };
}
