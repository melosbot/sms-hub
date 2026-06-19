import { ComposeForm } from "./ComposeForm"
import { OutboundTable } from "./OutboundTable"

export function SendView() {
  return (
    <div className="flex flex-col gap-4">
      <ComposeForm />
      <OutboundTable />
    </div>
  )
}
