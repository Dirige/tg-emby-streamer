"""番号（JAV / FC2 等）识别。

用于 Bot 手动入库流程：转发视频可能来自不同频道、命名各异，
在启发式命名之前先尝试识别番号 —— 番号是 18+ 内容唯一稳定的标识。
"""
import re

# 边界：非字母数字（下划线/空格/标点/开头均可），兼容 SSIS-806 / MIDA_031 / FPRE 183
_LETTERS = r"(?<![A-Za-z0-9])([A-Za-z]{2,6})"
_DIG = r"(\d{2,5})(?!\d)"
_SKIP_START = "(?<![A-Za-z0-9])"

# (pattern, 命中后生成标题)
_JAV_PATTERNS: list[tuple[re.Pattern, object]] = [
    # FC2-PPV-1234567 / fc2 ppv 1234567
    (
        re.compile(_SKIP_START + r"(?:FC2)[-_ ]?(?:PPV)[-_ ]?(\d{6,10})(?!\d)", re.I),
        lambda m: f"FC2-PPV-{m.group(1)}",
    ),
    # 带分隔符：SSIS-806 / MIDA_031 / FPRE 183
    (
        re.compile(_LETTERS + r"[-_ ]" + _DIG),
        lambda m: f"{m.group(1).upper()}-{m.group(2)}",
    ),
    # 无分隔符：ssis806 / MIDA031（字母紧连 3 位数字，避免匹配年份/时间戳）
    (
        re.compile(r"\b([A-Za-z]{2,6})(\d{3})(?![0-9A-Za-z])"),
        lambda m: f"{m.group(1).upper()}-{m.group(2)}",
    ),
]

# 常见误命中词（编码/容器/水印等）
_EXCLUDE = {
    "X264", "X265", "H264", "H265", "HEVC", "AAC", "MP4", "MKV",
    "AVI", "WMV", "FLV", "WEBM", "BD", "DVD", "HD", "SD", "TV",
    "VOL", "EP", "PT", "PART", "PART1", "PART2", "BGM", "OP", "ED",
    "CC", "TVB", "HDR", "DV", "ATM", "VBR", "CBR", "MD5", "USB",
    # 无分隔符模式常见误命中词
    "VIDEO", "FINAL", "CLIP", "FILE", "SRT", "CJK", "RGB",
}


def detect_jav_code(*texts: str | None) -> str | None:
    """从多段文本（文件名、说明等）中识别番号，按传入顺序优先。

    返回标准化 `CODE-123`；无识别结果返回 None。
    """
    for text in texts:
        if not text:
            continue
        for pattern, fmt in _JAV_PATTERNS:
            for m in pattern.finditer(text):
                code = fmt(m)
                if code.split("-")[0] in _EXCLUDE:
                    continue
                return code
    return None
