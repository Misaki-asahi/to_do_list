# -*- coding: utf-8 -*-
"""
_console.py -- 命令行输出的编码保护。

【为什么需要它？—— 一个真实的 Bug】

    同一个脚本，直接运行正常，把输出重定向到文件就崩：

        python scripts/check_errors.py            -> 正常
        python scripts/check_errors.py > out.txt  -> UnicodeEncodeError

    原因：Python 的 sys.stdout 用哪个编码，取决于【输出去了哪里】：

        直接运行    -> 用控制台编码（现代终端多半是 UTF-8）
        重定向文件  -> 用系统 locale 编码（中文 Windows = GBK / cp936）

    只要输出里出现 GBK 装不下的字符（emoji、部分生僻字、用户自己输入的符号），
    重定向时就会抛 UnicodeEncodeError。

    更坑的是：这类字符往往出现在【最后一行汇总】里，
    于是"前面几十项检查全跑完了，结果一个字都没写进文件"。

    详见 docs/问题记录与解决方案.md 的 BUG-019。

【怎么用】

    在 import 之后、第一次 print 之前调用一次即可：

        from scripts._console import use_safe_output
        use_safe_output()
"""

import sys


def use_safe_output():
    """
    让 stdout 遇到【当前编码装不下的字符】时用 ? 顶替，而不是抛异常。

    【注意：这里不改变编码本身】
        如果强行改成 UTF-8，在中文控制台里直接运行时会显示乱码
        （终端按 GBK 去解码 UTF-8 的字节）。
        只加一个 errors="replace"，两种场景就都能正常跑完。

    返回 True 表示已启用保护；返回 False 表示当前环境不支持（不影响程序继续跑）。

    这个调用在任何情况下都不会让脚本失败，可以放心添加。
    """
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return False
    try:
        stream.reconfigure(errors="replace")
        return True
    except (AttributeError, ValueError, OSError):
        # Python 太老没有 reconfigure，或者 stdout 被换成了不支持的对象 —— 忽略即可
        return False
