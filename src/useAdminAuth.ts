import { useCallback, useEffect, useState } from 'react'
import { api, getAdminToken, SESSION_EXPIRED_EVENT, setAdminToken, type Role, type User } from './api'

export interface Session {
  role: Role
  user: User | null
}

/** `undefined` = checking stored token, `null` = logged out, otherwise who is logged in. */
export function useAdminAuth() {
  const [session, setSession] = useState<Session | null | undefined>(() => (getAdminToken() ? undefined : null))

  useEffect(() => {
    if (getAdminToken()) {
      api
        .session()
        .then((s) => setSession({ role: s.role, user: s.user }))
        .catch(() => {
          setAdminToken(null)
          setSession(null)
        })
    }
    const onExpired = () => setSession(null)
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [])

  const login = useCallback(async (name: string, password: string) => {
    const s = await api.login(name, password)
    setAdminToken(s.token)
    setSession({ role: s.role, user: s.user })
  }, [])

  const logout = useCallback(() => {
    setAdminToken(null)
    setSession(null)
  }, [])

  return { session, login, logout }
}
