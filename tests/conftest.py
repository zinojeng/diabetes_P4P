"""pytest 設定：將 src/ 加入 sys.path，讓測試可直接 import dm_eligibility。"""

import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
