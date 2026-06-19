# CLAUDE.md — web/ 前端

`sms-hub` 的 Web UI，Vite + React 19 + TypeScript + shadcn/ui（Tailwind v4，nova 主题）。构建产物 `dist/` 由 Hub（`core/main.py`）作为 `StaticFiles` 提供。

## 必读

- **UI 设计规范见 [`DESIGN.md`](./DESIGN.md)**——唯一权威，所有组件改动须遵循。下面只列最易违反的几条。

## 关键铁律（违反即返工）

- **只用语义色**：`bg-primary`/`text-muted-foreground`/`bg-online`…，**禁止**裸色（`bg-emerald-500`、`text-gray-600` 等）。
- **间距用 `gap-*`**，禁止 `space-x/space-y-*`。等宽高用 `size-*`。
- **控件高度统一 `h-8`**：按钮不要传 `size="sm/xs"`；图标按钮 `size="icon"`。
- **圆角用语义令牌** `rounded-panel/control/chip`，不写 `rounded-xl/lg/md`。
- **列表内缩分隔**：`<div className="px-4"><Separator /></div>`。
- **`SelectItem` 必须在 `SelectGroup` 内**；`Dialog/Sheet` 必须有 `Title`。
- **Button 内图标用 `data-icon`**，不加 `size-*`。
- **`className` 只管布局**，不覆盖组件颜色/字重；条件类用 `cn()`。
- **用组件不自造**：`Separator`/`Skeleton`/`Badge`/`Empty`/`Alert`/`sonner`。

## 常用命令

```bash
npm run build        # tsc -b && vite build → dist/（Hub 直接服务）
npm run dev          # 本地开发（需 Hub 后端在 :8025，见根目录 test/demo/demo）
NODE_PATH=$(npm root -g) node test/browser-smoke.cjs   # 冒烟（须保持 0 错误）
npx shadcn@latest docs <component>                     # 查组件官方用法
npx shadcn@latest add <component>                      # 加组件
```

## 架构要点

- 认证：`Authorization: Bearer <token>`（`localStorage`）；SSE 走 `/api/events?token=`。
- 视图切换用 `?tab=`（**无 SPA 路由**，`main.py` 静态挂载无兜底）。
- 数据：React Query（`hooks/queries.ts`、`hooks/mutations.ts`）；SSE 失效缓存（`lib/sse.ts`）。
- 卡片为中心：SIM 卡为主体，瘦终端从属；离线卡片在设置页隐藏。
