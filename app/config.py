# -*- coding: utf-8 -*-
"""
config.py -- 模块 M1：全局配置。

设计思路：
    整个项目里所有"写死的值"（路径、端口、时间格式）都只允许出现在这一个文件。
    好处：换电脑、换端口、发布给别人用的时候，只需要改这里，不用到处翻代码。

命名规范：
    全大写的变量（如 PORT）在 Python 里表示"常量"，约定俗成不要去修改它。
"""

import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 一、路径
# --------------------------------------------------------------------------
# 【打包后会怎样？】
#   用 PyInstaller 打包成 exe 之后，情况完全变了：
#     ① 代码和资源被塞进 exe（或 exe 旁边的 _internal 目录），
#        __file__ 指向的不再是项目目录 —— 所以【不能用它来定位数据目录】；
#     ② exe 通常装在 C:\Program Files 下，那个目录【默认不可写】，
#        把数据库写在旁边会直接失败。
#
#   所以路径分成两类，这很重要：
#     · 只读资源（HTML / CSS / JS）-> 跟着程序走（打包后在 exe 内部）
#     · 可写数据（数据库 / 备份 / 日志）-> 放到用户的 AppData 目录
#
#   这也是所有 Windows 桌面程序的标准做法。
# --------------------------------------------------------------------------

FROZEN = getattr(sys, "frozen", False)      # True 表示"现在是打包后的 exe"

# ---- 1) 只读资源目录 ----
if FROZEN:
    # PyInstaller 把资源解压到 sys._MEIPASS 指向的目录
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    # __file__          = 本文件位置 .../to_do_list/app/config.py
    # .resolve()        = 绝对路径
    # .parent.parent    = .../to_do_list   <- 项目根目录
    RESOURCE_DIR = Path(__file__).resolve().parent.parent

APP_DIR = RESOURCE_DIR / "app"

# ---- 2) 可写数据目录 ----
if FROZEN:
    # 打包后：放到 %LOCALAPPDATA%\OfflineTodoList
    # （每个用户独立，不需要管理员权限，卸载时可直接删）
    _local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    BASE_DIR = Path(_local) / "OfflineTodoList"
else:
    # 源码运行：就是项目根目录（保持"项目自包含"原则）
    BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 所有"运行产生的文件"一律放在 data/ 下面，并按用途分好类。
# 项目自包含原则：换台电脑、打包压缩、备份，只需要复制整个 to_do_list 文件夹。
#
#   data/
#     ├─ todo.db         主数据库（你真正的待办都在这里）
#     ├─ todo.db-wal     SQLite 的预写日志（自动生成，不要手删）
#     ├─ todo.db-shm     SQLite 的共享内存（自动生成，不要手删）
#     ├─ backups/        自动备份（第三阶段实现）
#     ├─ test/           测试用数据库（与真实数据完全隔离）
#     ├─ logs/           运行日志（第三阶段实现）
#     └─ exports/        导出的 JSON / 备份文件（第三阶段实现）
# ---------------------------------------------------------------------------
# 数据根目录。
# 允许用环境变量 TODO_DATA_DIR 整体覆盖 —— 测试时把它指向一个临时目录，
# 数据库、备份、日志就全都隔离了，一个都不会碰到你的真实数据。
DATA_DIR = Path(os.getenv("TODO_DATA_DIR", str(BASE_DIR / "data")))

# 主数据库文件位置。
# 允许用环境变量 TODO_DB 单独覆盖（scripts/check_repo.py 用它把测试数据
# 指向 data/test/ 下的独立文件）。
DB_PATH = Path(os.getenv("TODO_DB", str(DATA_DIR / "todo.db")))

BACKUP_DIR = DATA_DIR / "backups"             # 自动备份
TEST_DIR = DATA_DIR / "test"                  # 测试数据（随时可删）
TEST_DB_PATH = TEST_DIR / "test_repo.db"      # 自检脚本专用的数据库
LOG_DIR = DATA_DIR / "logs"                   # 运行日志
EXPORT_DIR = DATA_DIR / "exports"             # 导出文件

# ---- 日志（第三阶段新增：打包版没有控制台，日志是唯一的线索）----
LOG_FILE = LOG_DIR / "app.log"      # 日志文件（按天滚动）
LOG_LEVEL = "INFO"                  # DEBUG / INFO / WARNING / ERROR
LOG_KEEP_DAYS = 14                  # 保留最近多少天的日志
LOG_TO_CONSOLE = True               # 是否同时打印到控制台（源码运行时用）

# ---- 自动备份（因为一次真实的数据丢失事故而提前实现，见 BUG-011）----
BACKUP_KEEP = 7                    # 最多保留几份备份（超出的自动删最旧的）
BACKUP_MIN_INTERVAL_MINUTES = 60   # 两次自动备份之间至少间隔多少分钟
BACKUP_ON_STARTUP = True           # 是否在程序启动时自动备份

STATIC_DIR = APP_DIR / "static"                # CSS / JS（只读资源）
TEMPLATE_DIR = APP_DIR / "templates"          # HTML 页面（只读资源）

# --------------------------------------------------------------------------
# 二、应用信息（会显示在页面标题和接口文档上）
# --------------------------------------------------------------------------
APP_NAME = "离线待办清单"
APP_VERSION = "0.2.0"

# --------------------------------------------------------------------------
# 三、服务地址
#    用 os.getenv 读取环境变量：本地双击运行时用默认值；
#    将来部署到公网服务器时，只要设置环境变量就能改，不用改代码。
# --------------------------------------------------------------------------
HOST = os.getenv("TODO_HOST", "127.0.0.1")
PORT = int(os.getenv("TODO_PORT", "8000"))

# --------------------------------------------------------------------------
# 四、时间格式（对应第一阶段决策 3B：纯本地时间字符串）
#    存进数据库的样子：2026-02-20 08:30
#    只精确到"分"，秒不需要，界面也不会让用户填秒。
# --------------------------------------------------------------------------
DATETIME_FORMAT = "%Y-%m-%d %H:%M"   # 数据库 / API 统一使用的格式
DATE_FORMAT = "%Y-%m-%d"             # 只用到日期的场合（如日历视图）

# --------------------------------------------------------------------------
# 五、业务规则
# --------------------------------------------------------------------------
TITLE_MAX_LENGTH = 200        # 待办标题最大长度
DESCRIPTION_MAX_LENGTH = 2000 # 描述最大长度

# ---- 提醒相关 ----
# 后端【后台线程】每隔多少秒扫描一次"到点的提醒"。
#
# 【命名变更说明】原来叫 REMINDER_POLL_SECONDS（前端轮询间隔）。
#   现在提醒由后端线程负责（发 Windows 系统通知），
#   所以改名为 SCAN（扫描间隔），语义更准确。
#   背景见 docs/阶段二-05-提醒系统.md 和 BUG-012。
REMINDER_SCAN_SECONDS = 20

# ---- Windows 通知的"应用身份"(AppUserModelID) ----
# 【为什么需要它？】
#   Windows 对"没有身份的"应用发通知是很严格的 —— 很可能【静默丢弃】：
#   接口返回成功、PowerShell 也没报错，但屏幕上一个通知都不出现。
#
#   解决办法是给应用注册一个身份（只写 HKCU，不需要管理员权限）：
#       HKCU\Software\Classes\AppUserModelId\OfflineTodoList.Desktop
#           DisplayName = 离线待办清单
#   注册之后用这个名字发通知，Windows 才认。
# 通知的应用身份：三种模式。
#
# 【实测结论，2026-09-13】详见 BUG-013：
#   往注册表注册自有身份后，**通知不会立刻生效** ——
#   Windows 需要一段时间刷新身份缓存（实测约几十分钟）。
#   刚注册完马上发通知 -> 不显示；过一阵再发 -> 正常显示。
#
# 所以提供三种模式：
#
#   "auto"（默认）自动：本次启动时身份【已经注册过】-> 用自有身份（显示"离线待办清单"）
#                       本次启动才新注册的      -> 先用兼容模式（保证立刻能用）
#                       下次启动就自动升级成自有身份
#
#   "system" 兼容模式：借用系统已注册的 PowerShell 身份
#                      -> 100% 立刻能用，但通知顶部写着"Windows PowerShell"
#
#   "own"    自有身份：始终用 OfflineTodoList.Desktop
#                      -> 通知里显示"离线待办清单"
NOTIFY_APP_ID_MODE = "auto"

NOTIFY_APP_ID_OWN = "OfflineTodoList.Desktop"
NOTIFY_APP_ID_SYSTEM = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
                        + chr(92) + "WindowsPowerShell" + chr(92) + "v1.0"
                        + chr(92) + "powershell.exe")

NOTIFY_REGISTER_APP_ID = True     # 用"自有身份"时，发通知前自动注册（已存在则跳过）

REMINDER_SNOOZE_MINUTES = 10  # 点"稍后提醒"时，推迟多少分钟

# 一次最多返回多少条待弹提醒。
# 防止"关了三天再打开"时一次弹出上百个窗口把浏览器卡住。
# 超出的部分会在下一轮轮询里继续返回，所以不会丢。
REMINDER_MAX_BATCH = 5

PRIORITY_LOW = 0
PRIORITY_NORMAL = 1
PRIORITY_HIGH = 2
PRIORITY_NAMES = {PRIORITY_LOW: "低", PRIORITY_NORMAL: "中", PRIORITY_HIGH: "高"}

STATUS_TODO = "todo"
STATUS_DONE = "done"
STATUS_NAMES = {STATUS_TODO: "未完成", STATUS_DONE: "已完成"}


def ensure_dirs():
    """
    确保运行需要的目录都存在。

    为什么要有这个函数？
        项目传到 GitHub 上时，data/ 目录里的数据库不会被上传（里面是私人数据）。
        别人下载后第一次运行，data/ 可能不存在 —— 这个函数负责自动建好，
        让程序"第一次运行就能跑"，不给使用者制造麻烦。

    exist_ok=True 表示：目录已经存在也不要报错。
    parents=True  表示：如果上级目录也不存在，一起创建。
    """
    for folder in (DATA_DIR, BACKUP_DIR, TEST_DIR, LOG_DIR, EXPORT_DIR):
        folder.mkdir(parents=True, exist_ok=True)
