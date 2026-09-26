const configuredApiUrl = process.env.EXPO_PUBLIC_API_URL?.trim();

// 是否真的配置了后端地址。没配置时给一个必定连不上的占位地址，
// 让 App 能启动并在请求时给出明确错误，而不是启动就崩。
const hasValidApiUrl =
  configuredApiUrl !== undefined && /^https?:\/\//i.test(configuredApiUrl);

const effectiveUrl = hasValidApiUrl ? configuredApiUrl! : 'http://127.0.0.1:1';

export const API_URL = effectiveUrl.replace(/\/+$/, '').trim();

/**
 * false 表示构建时没有注入 EXPO_PUBLIC_API_URL（通常是 eas.json 的 env 漏了）。
 * 调用方据此直接报"后端地址未配置"，不要静默跳过请求。
 */
export const API_URL_CONFIGURED = hasValidApiUrl;
