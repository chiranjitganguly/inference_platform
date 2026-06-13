import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "AI Inference Platform",
  description: "Unified LLM inference gateway",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
