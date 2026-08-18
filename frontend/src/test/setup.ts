import '@testing-library/jest-dom/vitest'

// jsdom не реализует scrollTo, а роутер восстанавливает позицию скролла на переходе.
window.scrollTo = () => undefined

// jsdom не реализует matchMedia, а тема читает системную настройку при инициализации.
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList
}
