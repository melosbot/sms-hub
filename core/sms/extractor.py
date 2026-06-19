"""验证码提取:关键词邻近法,入库时执行一次,结果存 messages.code。
算法说明见 docs/V2_DESIGN.md §5.2。无关键词命中则不提取,
避免把取件码、金额、订单号误判成验证码。"""
import re

KEYWORDS = [
    "验证码", "校验码", "检验码", "确认码", "激活码", "动态码", "安全码",
    "验证代码", "校验代码", "检验代码", "确认代码", "激活代码", "动态代码", "安全代码",
    "登入码", "认证码", "识别码", "短信口令", "动态密码", "交易码", "上网密码",
    "随机码", "动态口令",
    "驗證碼", "校驗碼", "檢驗碼", "確認碼", "激活碼", "動態碼",
    "驗證代碼", "校驗代碼", "檢驗代碼", "確認代碼", "激活代碼", "動態代碼",
    "登入碼", "認證碼", "識別碼",
    "code", "Code", "CODE",
]

# 4-8 位纯数字,或含字母且前 5 字符内有数字的 4-8 位字母数字混合。
# 前后必须是非字母数字边界:避免把手机号/订单号等长数字串的前 8 位当成候选
CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    # 4-8 位纯数字,或含字母且前 5 字符内有数字的 4-8 位字母数字混合
    r"(?:(?=[A-Za-z0-9]*[A-Za-z])(?=.{0,4}\d)[A-Za-z0-9]{4,8}|\d{4,8})"
    r"|"
    # 带连字符的字母数字串(如 AHD-SUC / S1-FU-37),命中后去连字符
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+"
    r")"
    r"(?![A-Za-z0-9])"
)


def extract_code(text: str) -> str | None:
    candidates = []
    for m in CODE_RE.finditer(text):
        code = m.group().replace("-", "")
        if 4 <= len(code) <= 8:
            candidates.append((m.start(), code))
    if not candidates:
        return None

    best: str | None = None
    best_key: float | None = None
    for kw in KEYWORDS:
        pos = text.find(kw)
        while pos != -1:
            for cpos, code in candidates:
                # 候选串与关键词本身重叠(如 "Code1234" 里的 Code),跳过
                if pos <= cpos < pos + len(kw):
                    continue
                if cpos >= pos:
                    dist = float(cpos - pos - len(kw))
                else:
                    # 关键词之前的候选劣化半位,优先取关键词之后的
                    dist = float(pos - cpos) + 0.5
                if best_key is None or dist < best_key:
                    best, best_key = code, dist
            pos = text.find(kw, pos + len(kw))
    return best


# ── 发件人品牌名提取(短信【签名】)──
# 末尾签名优先:运营商强制签名在末尾,是权威发件人;首尾都有【】时,
# 首部多为主题/标题(如"服务密码变更提醒"),末尾才是品牌(如"中国移动")。
# 无签名则空串。前端列表显示与推送模板 {sender_name}/{brand} 共用此结果,
# 保证"看得到的名字"和"推送里的名字"一致。
_BRAND_TAIL_RE = re.compile(r"【([^】]{1,20})】\s*$")
_BRAND_HEAD_RE = re.compile(r"^【([^】]{1,20})】")


def extract_brand(text: str) -> str:
    """从短信文本提取【品牌名】签名:末尾优先,退回首部;无则空串。"""
    t = text or ""
    m = _BRAND_TAIL_RE.search(t)
    if m:
        return m.group(1)
    m = _BRAND_HEAD_RE.match(t)
    return m.group(1) if m else ""
