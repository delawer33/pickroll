import { ClerkLoading, SignedIn, SignedOut, SignIn, UserButton } from '@clerk/clerk-react'

export function App() {
  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <span className="app-shell__brand">Court Vision</span>
        <SignedIn>
          <UserButton />
        </SignedIn>
      </header>
      <main className="app-shell__main">
        {/* Пока clerk-js грузится, SignedIn и SignedOut оба рендерят null — без этой
            ветки заблокированный скрипт Clerk выглядел бы как вечно пустая страница. */}
        <ClerkLoading>
          <p className="app-shell__loading">Загрузка сессии…</p>
        </ClerkLoading>
        <SignedIn>
          <p>AI-ассистент баскетбольного скаута</p>
        </SignedIn>
        <SignedOut>
          <div className="app-shell__sign-in">
            {/* hash-роутинг: TanStack Router появится в срезе #6, привязывать форму
                к путям пока не к чему. */}
            <SignIn routing="hash" />
          </div>
        </SignedOut>
      </main>
    </div>
  )
}
