import * as React from "react"
import { Separator as SeparatorPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      data-slot="separator"
      decorative={decorative}
      orientation={orientation}
      className={cn(
        // border 比 bg+1px 像素对齐更稳,1px 线粗细一致。
        "shrink-0 border-border data-horizontal:border-t data-horizontal:w-full data-vertical:border-l data-vertical:self-stretch",
        className
      )}
      {...props}
    />
  )
}

export { Separator }
