import { SignIn } from '@clerk/clerk-react'
import { createFileRoute } from '@tanstack/react-router'

/** Вход — единственный экран вне каркаса. Splat в пути обязателен: Clerk уводит на
 * подпути (`/sign-in/factor-two`, `/sign-in/sso-callback`) при верификации. */
export const Route = createFileRoute('/sign-in/$')({
  component: SignInScreen,
})

function SignInScreen() {
  return (
    <div className="flex min-h-dvh flex-col items-center gap-6 bg-paper px-4 pt-[10vh]">
      <p className="t-micro text-ink-muted">Court Vision</p>
      <SignIn routing="path" path="/sign-in" />
    </div>
  )
}
