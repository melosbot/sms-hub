// 用 sharp 从 lucide message-square 生成 PWA 图标,写入 public/icons/。
//   - any:      深底白图标,图标占 ~60%
//   - maskable: 同上但图标占 ~50%,留出平台裁剪 safe zone
//   - apple:    180×180(方形,Apple 自裁圆角)
//
// 跑一次后产物提交进仓库;改图标时重跑 `npm run gen-icons`。
// Docker 构建不依赖本脚本(图标已是版本化静态资源),故 sharp 仅作本地 devDep。
import sharp from "sharp"
import { mkdir } from "node:fs/promises"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(__dirname, "../public/icons")

// 品牌色:中性近黑底 + 白色 message-square(浅/深启动器均清晰)。
const BG = "#0a0a0a"
const FG = "#fafafa"
const MSG_SQUARE_PATH =
  "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"

function iconSvg(size, scale) {
  const margin = (size * (1 - scale)) / 2
  const inner = size * scale
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
  <rect width="${size}" height="${size}" fill="${BG}"/>
  <svg x="${margin}" y="${margin}" width="${inner}" height="${inner}" viewBox="0 0 24 24" preserveAspectRatio="xMidYMid meet">
    <path fill="none" stroke="${FG}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="${MSG_SQUARE_PATH}"/>
  </svg>
</svg>`,
  )
}

async function render(svg, file) {
  await sharp(svg, { density: 384 }).png().toFile(resolve(OUT, file))
  console.log("  ✓", file)
}

await mkdir(OUT, { recursive: true })
console.log("生成 PWA 图标 → public/icons/")
await render(iconSvg(192, 0.6), "icon-192.png")
await render(iconSvg(512, 0.6), "icon-512.png")
await render(iconSvg(192, 0.5), "maskable-192.png")
await render(iconSvg(512, 0.5), "maskable-512.png")
await render(iconSvg(180, 0.62), "apple-touch-icon.png")
console.log("完成。")
