import { create } from 'zustand';
import { UserSession } from '@shared/types';

interface AuthStore {
  session: UserSession | null;
  status: 'checking' | 'signed_out' | 'signed_in';
  error: string | null;

  restoreSession: () => Promise<void>;
  login: (baseUrl: string, username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthStore>((set) => ({
  session: null,
  status: 'checking',
  error: null,

  restoreSession: async () => {
    const session = await window.chukaApi.getSession();
    set({ session, status: session ? 'signed_in' : 'signed_out' });
  },

  login: async (baseUrl, username, password) => {
    set({ error: null });
    try {
      const session = await window.chukaApi.login(baseUrl, username, password);
      set({ session, status: 'signed_in' });
    } catch (err: any) {
      set({ error: err.message || 'Login failed.' });
      throw err;
    }
  },

  logout: async () => {
    await window.chukaApi.logout();
    set({ session: null, status: 'signed_out' });
  }
}));
