import { useState, type FormEvent } from "react"
import { MessageSquareTextIcon } from "lucide-react"
import { Spinner } from "@/components/ui/spinner"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { api, setToken } from "@/lib/api"
import { errorToast } from "@/lib/toast"

export function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [user, setUser] = useState("admin")
  const [pass, setPass] = useState("")
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const token = await api.login(user, pass)
      setToken(token)
      onLoggedIn()
    } catch (err) {
      errorToast(err, "登录失败")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <div className="mb-1 flex size-12 items-center justify-center rounded-panel bg-primary/10 text-primary">
            <MessageSquareTextIcon className="size-6" />
          </div>
          <CardTitle className="font-heading text-xl font-semibold">sms-hub</CardTitle>
          <CardDescription>登录以管理你的短信网关</CardDescription>
        </CardHeader>
        <form onSubmit={submit} className="contents">
          <CardContent>
            <FieldGroup className="gap-4">
              <Field>
                <FieldLabel htmlFor="user">账号</FieldLabel>
                <Input
                  id="user"
                  autoComplete="username"
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="password">密码</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={pass}
                  onChange={(e) => setPass(e.target.value)}
                />
              </Field>
            </FieldGroup>
          </CardContent>
          <CardFooter>
            <Button className="w-full" disabled={loading}>
              {loading && <Spinner data-icon="inline-start" />}
              登录
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
