from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
XHS_SERVER = "http://127.0.0.1:11901"  # only used by xhs-related flows
LOCAL_CHROME_PATH = ""  # optional, e.g. C:/Program Files/Google/Chrome/Application/chrome.exe
LOCAL_CHROME_HEADLESS = True  # default headless behavior for uploader/examples
DEBUG_MODE = True  # default debug behavior

# HumanBehavior module configuration
# aggression_level: "low" (轻量反检测) / "medium" (推荐) / "high" (重度反检测，流程慢)
HUMAN_BEHAVIOR_AGGRESSION = "medium"
HUMAN_BEHAVIOR_DELAY_RANGE = (0.3, 2.5)  # 操作间随机延迟范围 (秒)
HUMAN_BEHAVIOR_TYPING_SPEED_RANGE = (50, 150)  # 每字符延迟 (ms)
HUMAN_BEHAVIOR_CLICK_OFFSET = (-5.0, 5.0)  # 点击坐标随机偏移 (px)
HUMAN_BEHAVIOR_RANDOMIZE_VIEWPORT = True
HUMAN_BEHAVIOR_ROTATE_UA = True
HUMAN_BEHAVIOR_RANDOMIZE_FINGERPRINT = True  # 时区/语言/Canvas/WebGL 指纹随机化
