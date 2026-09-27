import { useCallback, useEffect, useState } from 'react'
import { api, getAdminToken, SESSION_EXPIRED_EVENT, setAdminToken } from './api'

export function useAdminAuth() {
  const [loggedIn, setLoggedIn] = useState<boolean | null>(() => (getAdminToken() ? null : false))

  useEffect(() => {
    if (getAdminToken()) {
      api
        .adminMe()
        .then(() => setLoggedIn(true))
        .catch(() => {
          setAdminToken(null)
          setLoggedIn(false)
        })
    }
    const onExpired = () => setLoggedIn(false)
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [])

  const login = useCallback(async (password: string) => {
    const { token } = await api.adminLogin(password)
    setAdminToken(token)
    setLoggedIn(true)
  }, [])

  const logout = useCallback(() => {
    setAdminToken(null)
    setLoggedIn(false)
  }, [])

  return { loggedIn, login, logout }
}
