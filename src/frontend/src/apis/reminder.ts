// 定时提醒 / 通知相关接口
import { request } from "../utils/request"

// 列出当前用户未结束的提醒
export function getReminderListAPI() {
  return request({
    url: '/api/v1/reminder/list',
    method: 'GET',
  })
}

// 取消一条提醒
export function cancelReminderAPI(reminderId: string) {
  return request({
    url: `/api/v1/reminder/${reminderId}`,
    method: 'DELETE',
  })
}

// 拉取通知（前端轮询未读 feed）。since 为上次拉取到的最大 create_time（ISO 字符串）。
export function getNotificationInboxAPI(since?: string) {
  return request({
    url: '/api/v1/notification/inbox',
    method: 'GET',
    params: since ? { since } : {},
  })
}

// 标记通知已读。ids 为空表示把全部未读标记为已读。
export function markNotificationReadAPI(notificationIds: string[] = []) {
  return request({
    url: '/api/v1/notification/read',
    method: 'POST',
    data: { notification_ids: notificationIds },
  })
}
