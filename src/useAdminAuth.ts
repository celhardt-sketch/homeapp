import { useCallback, useEffect, useState } from 'react'
import { api, getAdminToken, setAdminToken } from './api'

export function useAdminAuth() {
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null)

  useEffect(() => {
    if (!getAdminToken()) {
      setLoggedIn(false)
      return
    }
    api
      .adminMe()
      .then(() => setLoggedIn(true))
      .catch(() => {
        setAdminToken(null)
        setLoggedIn(false)
      })
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
