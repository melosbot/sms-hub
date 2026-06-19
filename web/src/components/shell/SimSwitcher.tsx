import { IdCardIcon } from "lucide-react"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useSim } from "@/components/shell/SimProvider"
import { carrierName } from "@/lib/format"
import type { SimView } from "@/lib/types"

function simLabel(s: SimView): string {
  const main = s.name || s.msisdn || `IMSI …${s.imsi_tail}` || s.sim_id
  return s.operator ? `${main} · ${carrierName(s.operator)}` : main
}

/** 当前卡片切换器：仅多张启用卡时显示（docs/guide.md §5.2 单卡自动选中）。 */
export function SimSwitcher() {
  const { enabledSims, currentSimId, setCurrentSimId, multiple } = useSim()
  if (!multiple) return null
  return (
    <Select value={currentSimId} onValueChange={setCurrentSimId}>
      <SelectTrigger className="flex-1 min-w-0 gap-1.5" aria-label="当前卡片">
        <IdCardIcon className="size-3.5 text-muted-foreground" />
        <SelectValue className="mr-auto" />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {enabledSims.map((s) => (
            <SelectItem key={s.sim_id} value={s.sim_id}>
              {simLabel(s)}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}
