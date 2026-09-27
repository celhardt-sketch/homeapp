import { useCallback, useEffect, useState } from 'react'
import { api, getAdminToken, SESSION_EXPIRED_EVENT, setAdminToken, type Role } from './api'

/** `undefined` = checking stored token, `null` = logged out, otherwise the session's role. */
export function useAdminAuth() {
  const [role, setRole] = useState<Role | null | undefined>(() => (getAdminToken() ? undefined : null))

  useEffect(() => {
    if (getAdminToken()) {
      api
        .session()
        .then((s) => setRole(s.role))
        .catch(() => {
          setAdminToken(null)
          setRole(null)
        })
    }
    const onExpired = () => setRole(null)
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [])

  const login = useCallback(async (password: string) => {
    const s = await api.login(password)
    setAdminToken(s.token)
    setRole(s.role)
  }, [])

  const logout = useCallback(() => {
    setAdminToken(null)
    setRole(null)
  }, [])

  return { role, login, logout }
}
