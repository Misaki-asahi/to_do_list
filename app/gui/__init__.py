# -*- coding: utf-8 -*-
"""
app/gui/ -- 桌面悬浮窗的界面代码（v0.3.0 新增）。

【为什么单独一个包，而不是塞进 app/core/？】

    因为 app/core/ 里放的是"和界面无关的基础设施"
    （时间工具、日志、备份、通知……），
    而这里的代码是【纯粹为了让用户看见东西】——

        client.py   悬浮窗进程访问后端的唯一出口（相当于桌面版的 api.js）
        window.py   用 tkinter 画的窗口本身

    混在 core 里会让人以为"服务端的核心逻辑要用到 tkinter"，
    实际上服务端【完全不 import 这个包】：
    它只是启动一个独立的进程去跑 window.main()。

【一个重要的约定】

    这个包里的代码【只允许】通过 client.FloatingApi 访问数据，
    绝不允许 import repositories / 直连数据库。
    原因见 app/services/floating_service.py 开头的"整体数据流"说明。
"""
