"""Legado CSS 选择器 → 标准 CSS 转换"""
import re


def css_conv(sel):
    """Legado CSS 选择器 → 标准 CSS

    支持转换：
    - @@ → 空格（后代选择器）
    - @  → ' > '（直接子选择器）
    - <  → ' ' （父选择器；BS4 无父选择器，降级为后代）
    - !N → :nth-child(n+N+1)（1-based 偏移）
    - !-N → :nth-last-child(N)（逆序）
    - :eq(N) → :nth-child(N+1)
    - :lt(N) → :nth-child(-n+N)
    - :gt(N) → :nth-child(n+N+2)
    - class.x → .x；id.x → #x；tag.x → x
    - @text / @src / @href 等属性后缀剥离
    """
    if not sel:
        return sel
    s = sel.strip()
    s = re.sub(r'^-', '', s)
    s = re.sub(r'^@css:', '', s)

    # 先剥离属性提取后缀（必须在 @→> 转换之前）
    for suf in ['@text', '@src', '@href', '@html', '@textNodes',
                '@outerHtml', '@innerHtml', '@data', '@all',
                '@ownText', '@attr']:
        s = re.sub(re.escape(suf) + r'(?=\s|$|\||#|&)', '', s)

    # 必须先处理 @@（后代）再处理 @（直接子）
    s = s.replace('@@', ' ')
    s = s.replace('@', ' > ')

    # 父选择器降级
    s = re.sub(r'\s*<\s*', ' ', s)

    # 索引：!0 → 剥离，!N → :nth-child，!-N → :nth-last-child
    s = re.sub(r'!0+$', '', s)
    s = re.sub(r'!-(\d+)', lambda m: f':nth-last-child({m.group(1)})', s)
    s = re.sub(r'!([1-9]\d*)$', lambda m: f':nth-child(n+{int(m.group(1))+1})', s)

    # jQuery 风格伪类
    s = re.sub(r':eq\((\d+)\)', lambda m: f':nth-child({int(m.group(1))+1})', s)
    s = re.sub(r':lt\((\d+)\)', lambda m: f':nth-child(-n+{m.group(1)})', s)
    s = re.sub(r':gt\((\d+)\)', lambda m: f':nth-child(n+{int(m.group(1))+2})', s)

    # Legado 索引后缀 .N
    s = re.sub(r'\.(\d+)(?=\s|$|>|\.|#|\[|:)', '', s)

    # class./id./tag. 前缀转换
    s = re.sub(r'(?<!\.)\bclass\.', '.', s)
    s = re.sub(r'(?<!#)\bid\.', '#', s)
    s = re.sub(r'\btag\.', '', s)
    s = re.sub(r'\btag\b', '', s)

    s = re.sub(r'\s+', ' ', s).strip()
    return s
