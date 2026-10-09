/**
 * Accounts — the installation's users (global admin) and the signed-in user's
 * own profile.  Server: `backend/app/api/v1/endpoints/users.py`, and
 * `auth.py` for creating one (`POST /auth/register`; there is no
 * `POST /users/`).
 *
 * Not one project's data: these take no `projectId`, and a query on them has
 * none in its key (`['listUsers']`).  The member-picker directory (`getUserDirectory`)
 * and a project's roster live in the barrel.
 */
import { api } from './client';

/** A row of `GET /users/` (the server's `UserListItem`). */
export interface UserAccount {
  id: number;
  username: string;
  email?: string | null;
  full_name: string | null;
  /** Global role: `admin` or `member`. */
  role: string;
  is_active: boolean;
  last_login: string | null;
  created_at: string;
  created_by_id: number | null;
  /** Whether the user has enrolled in 2FA. */
  totp_enabled: boolean;
}

/** What `POST /auth/register` answers (the server's `UserProfile`): the new
 *  account WITHOUT `created_by_id`, `totp_enabled` or `email`. */
export type RegisteredUser = Omit<UserAccount, 'email' | 'created_by_id' | 'totp_enabled'>;

export interface RegisterUserPayload {
  username: string;
  password: string;
  full_name?: string;
  /** `admin` or `member`; the server defaults to `member`. */
  role?: string;
}

/** `PUT /users/{id}` — every field optional on the server. */
export interface UserAccountUpdate {
  full_name?: string;
  role?: string;
  is_active?: boolean;
}

/** One project a user belongs to, with their role in it — the row of both
 *  `GET /users/profile/projects` and `GET /users/{id}/memberships`.
 *  `joined_at` is null for a global admin's implicit reach (no membership row). */
export interface UserProjectMembership {
  project_id: number;
  project_name: string;
  project_slug: string;
  project_status: string;
  project_is_default: boolean;
  project_is_archived: boolean;
  role: string;
  joined_at: string | null;
}

// --- Every account (global admin) ---

export const listUsers = async (signal?: AbortSignal): Promise<UserAccount[]> => {
  const response = await api.get<UserAccount[]>('/users/', { signal });
  return response.data;
};

export const registerUser = async (payload: RegisterUserPayload): Promise<RegisteredUser> => {
  const response = await api.post<RegisteredUser>('/auth/register', payload);
  return response.data;
};

export const updateUserAccount = async (userId: number, data: UserAccountUpdate): Promise<UserAccount> => {
  const response = await api.put<UserAccount>(`/users/${userId}`, data);
  return response.data;
};

export const deleteUser = async (userId: number): Promise<void> => {
  await api.delete(`/users/${userId}`);
};

/** Admin reset: also ends the user's sessions and agent sessions. */
export const resetUserPassword = async (userId: number, newPassword: string): Promise<void> => {
  await api.post(`/users/${userId}/reset-password`, { new_password: newPassword });
};

/** Admin reset: clears the user's 2FA enrollment and recovery codes. */
export const resetUserTwoFactor = async (userId: number): Promise<void> => {
  await api.post(`/users/${userId}/reset-2fa`);
};

export const getUserMemberships = async (
  userId: number, signal?: AbortSignal,
): Promise<UserProjectMembership[]> => {
  const response = await api.get<UserProjectMembership[]>(`/users/${userId}/memberships`, { signal });
  return response.data;
};

// --- The signed-in user's own ---

/** Own profile, WRITE (`PUT /users/profile`; the read is `GET /auth/profile`,
 *  which `AuthContext` makes).  The server answers a message, not the profile. */
export const updateOwnProfile = async (data: { full_name: string }): Promise<void> => {
  await api.put('/users/profile', data);
};

export const getOwnProjectMemberships = async (signal?: AbortSignal): Promise<UserProjectMembership[]> => {
  const response = await api.get<UserProjectMembership[]>('/users/profile/projects', { signal });
  return response.data;
};
