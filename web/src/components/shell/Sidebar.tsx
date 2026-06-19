import { MessageSquareTextIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { TABS, type TabKey } from "@/lib/tabs"
import { cn } from "@/lib/utils"

/** 桌面端左侧栏导航（≥ md 显示）。 */
export function Sidebar({ active, onChange }: { active: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <nav className="hidden md:fixed md:inset-y-0 md:left-0 md:z-30 md:flex md:w-sidebar md:flex-col md:border-r md:bg-background">
      <div className="flex h-header items-center gap-2 border-b px-4">
        <MessageSquareTextIcon className="size-5 text-primary" />
        <span className="font-heading text-base font-semibold">sms-hub</span>
      </div>
      <div className="flex flex-col gap-1 p-3">
        {TABS.map((t) => {
          const Icon = t.icon
          const on = active === t.key
          return (
            <Button
              key={t.key}
              variant={on ? "secondary" : "ghost"}
              onClick={() => onChange(t.key)}
              className={cn("h-9 w-full justify-start")}
            >
              <Icon data-icon="inline-start" />
              {t.label}
            </Button>
          )
        })}
      </div>
    </nav>
  )
}
