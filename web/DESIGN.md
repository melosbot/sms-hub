# sms-hub 前端设计规范（shadcn/ui 对齐）

本文件是 `web/` 前端**唯一权威**的 UI 设计规范，依据 [shadcn/ui 官方约定](https://ui.shadcn.com/docs) + 本项目实际令牌制定。所有组件（含 AI 协作）须遵循。令牌定义在 `src/index.css` 的 `@theme` / `:root`，改一处即全局生效。

> 基础原则：**优先复用已有组件，组合而非重造；先用内置 variant，再考虑 className；只用语义色。**

---

## 一、令牌（Tokens）

### 1. 圆角（4 级语义令牌）
| 令牌 | 工具类 | 用途 |
|------|--------|------|
| `--radius-panel` | `rounded-panel` | 卡片 / 行 / 弹层 / 骨架块 |
| `--radius-control` | `rounded-control` | 控件（输入/选择/按钮，基元已自带）与内嵌面板 |
| `--radius-chip` | `rounded-chip` | 行内标签（验证码、推送记录条） |
| — | `rounded-full` | 状态圆点、胶囊 |

> 自定义容器一律用 `rounded-panel` / `rounded-control` / `rounded-chip`，不直接写 `rounded-xl/lg/md`。

### 2. 高度
| 用途 | 高度 |
|------|------|
| 控件（输入/选择/按钮，全局统一） | `h-8`（32px）|
| 图标按钮 | `size-8`（`size="icon"`）|
| 顶栏 | `h-header`（令牌 = 14）|
| 侧栏项 | `h-9` |

> **所有按钮、输入框、下拉框高度一致 = 32px。** 按钮不要传 `size="sm/xs"`；图标按钮用 `size="icon"`。Switch 开关是独立控件，不在此列。

### 3. 字号 / 字重
| 角色 | 字号 | 字重 |
|------|------|------|
| 登录标题（唯一大标题） | `text-xl` | `font-semibold` |
| 卡片/区块标题 | `text-base` | `font-medium`（`CardTitle` 默认，勿重复声明）|
| 正文 / 标签 | `text-sm` | `font-normal`（标签 `font-medium`，`Label` 默认）|
| 辅助/元信息/时间戳 | `text-xs` | `font-normal` + `text-muted-foreground` |

等宽（MAC、验证码、AT 响应）用 `font-mono`。

### 4. 宽度
| 用途 | 宽度 |
|------|------|
| 主内容最大宽度 | `max-w-app`（64rem，令牌）|
| 桌面侧栏 | `w-sidebar`（令牌 = 56）|
| 状态页卡片选择 | `flex-1 min-w-0`（自适应填满按钮组左侧剩余宽度）|
| 行内短输入（备注名） | `min-w-32 flex-1` |
| 登录卡 | `max-w-sm` |
| 弹窗 | `sm:max-w-md`（终端）/ `sm:max-w-lg`（短信详情）|

> **按钮宽度**——shadcn Button 无内建宽度设定（只有 `variant`/`size`，默认按内容收缩 `inline-flex shrink-0`），宽度全靠布局约定；**禁止** `sm:w-auto` / `flex-1 sm:flex-none` 等响应式宽度切换：
> - **表单提交单按钮**（`CardFooter` 内的保存/发送）：`className="w-full"`（永远满宽）。
> - **多按钮操作行**（工具条 / 对话框页脚）：按钮自然宽，容器 `flex flex-wrap items-center gap-2` 排列，窄屏自动换行；**不**用 `flex-1` 等分。
> - **按钮 + 输入/下拉组合**（备注名、AT 命令、拆分按钮）：输入 `min-w-0 flex-1` 填满、按钮自然宽；拆分按钮（split）内主按钮 `flex-1` + 图标按钮 `size="icon"`。

### 5. 间距
| 场景 | 间距 |
|------|------|
| 板块/卡片之间 | `gap-4` |
| 卡内分组、表单字段 | `gap-4` |
| 行内元素 | `gap-2` |
| 紧凑行内 | `gap-1` |

> 列表项分隔用**内缩细线**：`<div className="px-4"><Separator /></div>`（包一层 `px-4` 保证左右对称 16px 留白，且与各行内容对齐）。

> **卡片类型间距——优先用 shadcn 默认，别覆盖**：
> - **表单卡**：`Card` 默认 `gap-4`/`py-4`（即 `--card-spacing`）。当 `<form>` 包裹 `CardHeader/Content/Footer` 时，**必须**给 form 加 `className="contents"`——否则 form 成为 Card 的唯一 flex 子项，内置 `gap` 失效、各段会贴边。（涉及 `Login` / `GlobalConfigForm` / `ComposeForm`。）
> - **列表卡**：`<Card className="gap-0 py-0">` + `<CardContent className="px-0">`，行用 `px-4 py-2.5` 自负间距、贴边；有标题时标题放 `<CardHeader className="pt-4">`，首行的 `py` 即标题↔首项间距。（涉及 `InboxView` / `OutboundTable`。）

### 6. 颜色（只用语义色）
`bg-background` `text-foreground` `text-muted-foreground` `bg-muted` `bg-card` `border` `text-primary` `text-destructive` `bg-online`（在线/正向状态点）。

> 状态点（在线）用 `bg-online`（自定义令牌 `--online`），**不要**写 `bg-emerald-500` 等裸色。需要新的状态色时，在 `index.css` 加 CSS 变量并映射到 `@theme`，不要临时用 Tailwind 调色板。

---

## 二、Styling 规则（shadcn）

1. **只用语义色**：`bg-primary text-primary-foreground`、`text-muted-foreground`，不写 `bg-blue-500` / `text-gray-600`。
2. **状态/指标用 Badge 变体或语义 token**：`<Badge variant="secondary">`、`text-destructive`；不写 `text-emerald-600`。
3. **先用内置 variant**：`<Button variant="outline">`，不要手写 `border border-input bg-transparent`。
4. **className 只管布局**：`max-w-md mx-auto mt-4`。改颜色用 variant / 语义 token / CSS 变量，不要在 className 覆盖组件颜色或字重。
5. **间距用 `gap-*`，不用 `space-x/space-y-*`**：纵向 `flex flex-col gap-4`。
6. **等宽高用 `size-*`**：`size-10`，不写 `w-10 h-10`。
7. **截断用 `truncate`**，不写 `overflow-hidden text-ellipsis whitespace-nowrap`。
8. **不要手写 `dark:` 颜色覆盖**：语义 token 自带明暗。
9. **条件类用 `cn()`**：`cn("flex", cond && "bg-primary")`，不要模板字符串三元。
10. **覆盖层组件不要手写 `z-index`**：Dialog/Sheet/Drawer/AlertDialog/DropdownMenu/Popover/Tooltip 自管理堆叠。

**按钮 variant 按操作语义选用**（不靠感觉、不混用）：

| 操作语义 | variant | 典型 |
|---|---|---|
| 正向主提交（表单 footer 单确认） | `default` | 登录 / 发送 / 保存设置 / 执行 / 添加 / 回复 /「确认发送」 |
| 次要 / 辅助 | `outline` | 测试 / 选择 / 导出 / 加载更多 / 复制 / 强制拉取 / AT 预设 / 行内保存 |
| 工具图标（导航 / 切换 / 关闭 / 重发） | `ghost` + `size="icon"` | 登出 / 主题 / 关闭提醒 / 取消 / 重发 |
| **破坏性**（删除 / 排空） | `destructive` | **触发按钮与确认按钮（`AlertDialogAction`）都用** |
| 选中 / 激活态 | `secondary` | 侧栏当前项 / 验证筛选激活 |

> 行内**纯图标删除**用 `ghost`（低调），其**确认框**用 `destructive`——图标不吵、确认强警告，是 shadcn 标准模式；不要把图标删除也涂红。

---

## 三、组合规则（shadcn）

1. **Item 必须在 Group 内**：`SelectItem`→`SelectGroup`、`DropdownMenuItem`→`DropdownMenuGroup`、`CommandItem`→`CommandGroup`。不要把 item 直接放进 content。
2. **Dialog/Sheet/Drawer 必须有 Title**：`DialogTitle`/`SheetTitle`/`DrawerTitle`（可 `className="sr-only"` 隐藏）。
3. **Card 用完整组合**：`CardHeader`/`CardTitle`/`CardDescription`/`CardContent`/`CardFooter`，不要全塞 `CardContent`。
4. **Button 无 `isPending`/`isLoading`**：用 `Spinner` + `data-icon` + `disabled` 组合。
5. **`TabsTrigger` 必须在 `TabsList` 内**。
6. **`Avatar` 必须带 `AvatarFallback`**。
7. **用组件而非自造标记**：分隔用 `Separator`（不用 `<hr>`/`border-t`）；加载用 `Skeleton`（不用 `animate-pulse` div）；标签用 `Badge`（不用自造 span）；空状态用 `Empty`；提示用 `Alert`；toast 用 `sonner`。

---

## 四、表单规则（shadcn）

1. **表单用 `FieldGroup` + `Field`**，不要 `div` + `space-y-*` / `grid gap-*`。
2. **`InputGroup` 内用 `InputGroupInput`/`InputGroupTextarea`**，按钮用 `InputGroupAddon`。
3. **2–7 选项集用 `ToggleGroup`**，不要循环 `Button` 手动管 active。
4. **相关复选/单选用 `FieldSet` + `FieldLegend`**。
5. **校验**：`Field` 上 `data-invalid`，控件上 `aria-invalid`；禁用：`Field` 上 `data-disabled`，控件上 `disabled`。

---

## 五、图标规则（shadcn）

1. **Button 内图标用 `data-icon`**：`<SearchIcon data-icon="inline-start" />`，不写 `size-4`/`w-4 h-4`（组件自带尺寸）。
2. **图标作为对象传入**：`icon={CheckIcon}`，不要传字符串 key。
3. 图标库固定 `lucide-react`（见 `components.json` `iconLibrary`）。

---

## 六、本项目专有约定

- **移动优先**：所有响应式断点写 `… sm:…`（移动端先，`sm`(640px) 起桌面）。视口目标含 390px。
- **无 SPA 路由**：视图用 `?tab=` query 切换（`main.py` 用 `StaticFiles` 无兜底）；不引入 router。
- **卡片为中心**：SIM 卡是业务主体，瘦终端从属（只作「承载终端」显示）；离线卡片在设置页隐藏并提示。
- **认证**：`Authorization: Bearer <token>`（`localStorage`）；SSE 走 `?token=`。
- **实时刷新**：SSE（`useSSE`）+ 断线降级轮询（`useFallbackPolling`）。
- **冒烟契约**：登录 `#user`/`#password` + 按钮「登录」；`<nav>` 四标签 收件/发送/状态/设置；消息项 `main div[role="button"]`；搜索框 placeholder 含「搜索」。改动须保持 `test/browser-smoke.cjs` 0 错误。
