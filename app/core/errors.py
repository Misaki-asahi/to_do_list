# -*- coding: utf-8 -*-
r"""
errors.py -- 统一的异常类型。

【为什么要单独一个文件？】

    以前 BusinessError 和 NotFoundError 定义在 task_service.py 里，
    但这两种错误【不止任务模块会用】——
    提醒模块要抛它们、日历模块要抛它们、设置模块也要抛它们。

    结果就是：每个模块都 from app.services.task_service import BusinessError，
    形成一堆没必要的依赖关系。谁想用个异常，得先去 import 某个业务模块 —— 很别扭。

【现在的分层】

    AppError            所有自定义异常的基类
      ├─ BusinessError    业务规则不通过      -> HTTP 400
      ├─ NotFoundError    要操作的东西不存在  -> HTTP 404
      └─ ValidationError  格式/取值不合法      -> HTTP 422

    它们都会被 app/main.py 里的【全局异常处理器】翻译成对应的 HTTP 响应。
    所以路由函数里【不需要】再写 try/except 了 —— 直接让异常往上冒就行。
"""


class AppError(Exception):
    """所有自定义异常的基类。

    单独建一个基类的意义：
        以后想加"记录所有业务异常"这类全局行为时，只需要写一处。
    """

    #: 默认的 HTTP 状态码（子类可以覆盖）
    status_code = 500
    #: 给前端看的错误类型标记
    error_type = "app_error"

    def __init__(self, message="", **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class BusinessError(AppError):
    """业务规则不通过。

    例：提醒时间填成了过去、开了提醒却没填时间。

    这类错误的特征是：**请求本身没毛病，是用户的判断不合理。**
    所以返回 400，并且错误信息可以直接展示给用户看。
    """

    status_code = 400
    error_type = "business_error"


class NotFoundError(AppError):
    """要操作的数据不存在。

    返回 404，让前端能区分"这条被删了"和"你填错了"。
    """

    status_code = 404
    error_type = "not_found"


class ValidationError(AppError):
    """取值不合法（业务层面的校验，不是 Pydantic 的格式校验）。

    返回 422。
    """

    status_code = 422
    error_type = "validation_error"
