"""番号识别测试（覆盖常见命名变体与误命中排除）。"""
from app.media.jav import detect_jav_code
from app.telegram.bot import _looks_like_jav


def test_standard_code_with_dash():
    assert detect_jav_code("SSIS-806.mp4") == "SSIS-806"
    assert detect_jav_code("MIDA_031.mp4") == "MIDA-031"
    assert detect_jav_code("FPRE 183.mp4") == "FPRE-183"


def test_code_from_caption():
    assert detect_jav_code(None, None, "SSIS-806 最新流出") == "SSIS-806"
    # 文件名优先于说明
    assert detect_jav_code("MIDA_031.mp4", None, "SSIS-806") == "MIDA-031"


def test_code_without_separator():
    assert detect_jav_code("ssis806 1080p.mp4") == "SSIS-806"
    assert detect_jav_code("MIDA031.mp4") == "MIDA-031"


def test_fc2():
    assert detect_jav_code("fc2-ppv-1234567.mp4") == "FC2-PPV-1234567"
    assert detect_jav_code("FC2 PPV 1234567") == "FC2-PPV-1234567"


def test_lowercased_code_uppercased():
    assert detect_jav_code("ssis-806.mp4") == "SSIS-806"


def test_codec_excluded():
    assert detect_jav_code("x265 1080p.mkv") is None
    assert detect_jav_code("h264_aac.mp4") is None


def test_leading_zero_serial_excluded():
    # 0 开头的流水号/时间戳不算番号
    assert detect_jav_code("video20240101.mp4") is None
    assert detect_jav_code("abc0123.mp4") is None


def test_no_code_at_all():
    assert detect_jav_code("庆余年 第二季 第01集.mp4") is None
    assert detect_jav_code(None) is None


def test_looks_like_jav():
    assert _looks_like_jav("SSIS-806") is True
    assert _looks_like_jav("SSIS806") is True
    assert _looks_like_jav("庆余年 第二季") is False


def test_parse_code_list():
    from app.telegram.bot import _parse_code_list
    text = (
        "1\n"
        "ABP-108\n"
        "ABP-108\n"
        "https://t.me/c/123/456\n"
        "ADN165 部分文本\n"
        "sonE-228 4K\n"
        "乱码 20240101\n"
        "ABF-017"
    )
    codes = _parse_code_list(text)
    assert codes == ["ABP-108", "ADN-165", "SONE-228", "ABF-017"]


def test_parse_code_list_cap():
    from app.telegram.bot import _parse_code_list
    text = " ".join(f"SSIS-{i}" for i in range(100, 600) if len(str(i)) >= 3)
    codes = _parse_code_list(text, cap=10)
    assert len(codes) == 10


def test_safe_text_surrogates():
    from app.telegram.jav_fetcher import safe_text
    assert safe_text("abc SSIS-806") == "abc SSIS-806"
    assert safe_text("") == ""
