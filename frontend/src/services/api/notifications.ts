/**
 * Notifications — read-side helpers used by the bell icon + activity feed.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api } from './client';


// --- Notifications ---
export interface NotificationItem {
  id: number;
  type: string;
  title: string;
  body: string | null;
  source_type: string | null;
  source_id: number | null;
  host_id: number | null;
  actor_id: number | null;
  actor_username: string | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationListResponse {
  notifications: NotificationItem[];
  total: number;
  unread_count: number;
}

export const getNotifications = async (
  unreadOnly = false,
  limit = 50,
): Promise<NotificationListResponse> => {
  const response = await api.get(`/notifications/?unread_only=${unreadOnly}&limit=${limit}`);
  return response.data;
};

export const getUnreadNotificationCount = async (): Promise<number> => {
  const response = await api.get('/notifications/unread-count');
  return response.data.unread_count;
};

export const markNotificationsRead = async (notificationIds: number[]): Promise<number> => {
  const response = await api.post('/notifications/mark-read', { notification_ids: notificationIds });
  return response.data.marked_read;
};

export const markAllNotificationsRead = async (): Promise<number> => {
  const response = await api.post('/notifications/mark-all-read', {});
  return response.data.marked_read;
};
