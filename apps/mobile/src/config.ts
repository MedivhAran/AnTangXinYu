const configuredApiUrl = process.env.EXPO_PUBLIC_API_URL?.trim();

if (!configuredApiUrl) {
  throw new Error('缺少 EXPO_PUBLIC_API_URL，请在 apps/mobile/.env 中配置后端地址。');
}

if (!/^https?:\/\//i.test(configuredApiUrl)) {
  throw new Error('EXPO_PUBLIC_API_URL 必须是完整的 http:// 或 https:// 地址。');
}

export const API_URL = configuredApiUrl.replace(/\/+$/, '');
