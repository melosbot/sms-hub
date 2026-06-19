import { TABS, type TabKey } from "@/lib/tabs"
import { cn } from "@/lib/utils"

/**
 * 移动端底部标签栏（< md 显示）。
 * 刻意渲染为 DOM 中第一个 <nav>，确保冒烟测试 `nav button:has-text(...)` 在 390px 视口命中的是可见项。
 */
export function BottomNav({ active, onChange }: { active: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-30 flex border-t bg-background pb-[env(safe-area-inset-bottom)] md:hidden">
      {TABS.map((t) => {
        const Icon = t.icon
        const on = active === t.key
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            data-active={on}
            className={cn(
              "flex flex-1 flex-col items-center gap-0.5 py-2 text-xs",
              on ? "text-foreground" : "text-muted-foreground",
            )}
          >
            <Icon className="size-5" />
            <span>{t.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
