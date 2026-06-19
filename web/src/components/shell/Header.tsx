import { LogOutIcon, MessageSquareTextIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { ThemeToggle } from "./ThemeToggle"
import { clearToken, UNAUTH_EVENT } from "@/lib/api"

export function Header() {
  const logout = () => {
    clearToken()
    window.dispatchEvent(new Event(UNAUTH_EVENT))
  }
  return (
    <header className="sticky top-0 z-20 flex h-header items-center gap-2 border-b bg-background/95 px-4 backdrop-blur supports-backdrop-filter:bg-background/80">
      <div className="flex items-center gap-2 font-heading font-semibold md:hidden">
        <MessageSquareTextIcon className="size-5 text-primary" />
        <span>sms-hub</span>
      </div>
      <div className="ml-auto flex items-center gap-0.5">
        <ThemeToggle />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" onClick={logout} aria-label="登出">
              <LogOutIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent>登出</TooltipContent>
        </Tooltip>
      </div>
    </header>
  )
}
