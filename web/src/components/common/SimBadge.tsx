import { Badge } from "@/components/ui/badge"

/** 卡片来源徽章。 */
export function SimBadge({ name, className }: { name: string; className?: string }) {
  return (
    <Badge variant="secondary" className={className}>
      {name || "未命名卡"}
    </Badge>
  )
}
