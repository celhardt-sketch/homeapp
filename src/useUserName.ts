import { useCallback, useState } from 'react'

const KEY = 'hm:userName'

export function useUserName() {
  const [name, setNameState] = useState<string>(() => localStorage.getItem(KEY) ?? '')
  const setName = useCallback((value: string) => {
    const trimmed = value.trim()
    if (trimmed) localStorage.setItem(KEY, trimmed)
    else localStorage.removeItem(KEY)
    setNameState(trimmed)
  }, [])
  return { name, setName }
}
