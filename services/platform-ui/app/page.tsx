import { auth } from "../auth"
import { signOut } from "../auth"

export default async function Home() {
  const session = await auth()

  return (
    <main>
      <h1>AI Inference Platform</h1>
      {session?.user && (
        <div>
          <p>Signed in as: {session.user.email}</p>
          <p>Roles: {session.user.roles.join(", ") || "none"}</p>
          <p>Team: {session.user.team || "—"}</p>
          <form
            action={async () => {
              "use server"
              await signOut()
            }}
          >
            <button type="submit">Sign out</button>
          </form>
        </div>
      )}
    </main>
  )
}
