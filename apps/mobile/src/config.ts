const configuredApiUrl = process.env.EXPO_PUBLIC_API_URL?.trim();

// 检查是否配置了有效的后端地址
const hasValidApiUrl =
  configuredApiUrl !== undefined && /^https?:\/\//i.test(configuredApiUrl);

// 没有配置时用本地占位符，避免 App 启动崩溃
const effectiveUrl = hasValidApiUrl ? configuredApiUrl! : 'http://127.0.0.1:1';

export const API_URL = effectiveUrl.replace(/\/+$/, '').trim();

// 演示模式：未配置真实后端时启用，App 直接跳过登录进入聊天页
export const IS_DEMO_MODE = !hasValidApiUrl;
