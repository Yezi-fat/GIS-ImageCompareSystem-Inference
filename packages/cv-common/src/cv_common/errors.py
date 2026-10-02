"""统一错误模型（设计 §2.2/§4.6）。

CvError 基类 + 九个具体异常（评审 D-03/D-04/D-05 落定）：
Java 侧 PythonErrorDecoder 将内部码映射为对外错误码（映射表见
《Python推理计算服务对Java侧服务接口需求文档》J-07）。

实现里程碑：P-009（register_error_handlers 落地）。
统一响应包对齐 Java 口径：{code, message, trace_id}（task_id 由 Java 侧补）；
兜底异常 → 500 INTERNAL_ERROR，不产生未捕获异常（需求验收第 8 条）。
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class CvError(Exception):
    """业务异常基类：携带内部错误码与 HTTP 状态（错误码表见设计 §4.6）。"""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class UnsupportedElementError(CvError):
    """要素类别超出模型可识别范围（FR-6.6，不静默忽略）。"""

    def __init__(self, message: str):
        super().__init__("UNSUPPORTED_ELEMENT", message, 400)


class ImageDecodeFailedError(CvError):
    """影像拉取/解码失败（含预签名 URL 过期——message 携带存储端 HTTP 状态供 Java 重签判定，J-02）。"""

    def __init__(self, message: str):
        super().__init__("IMAGE_DECODE_FAILED", message, 400)


class GeoExtentMismatchError(CvError):
    """双期影像尺寸/地理范围不一致（FR-8.7 辅助）。

    V2.5 更名（原 EXTENT_MISMATCH），与对外码 GEO_EXTENT_MISMATCH 恒等映射（评审 D-05）；
    双期尺寸不一致与多期范围不一致共用一码，差异由 message 承载。
    """

    def __init__(self, message: str):
        super().__init__("GEO_EXTENT_MISMATCH", message, 400)


class ModelNotReadyError(CvError):
    """模型未加载/未就绪（FR-5.5）。"""

    def __init__(self, message: str):
        super().__init__("MODEL_NOT_READY", message, 503)


class InferenceFailedError(CvError):
    """推理执行异常。"""

    def __init__(self, message: str):
        super().__init__("INFERENCE_FAILED", message, 500)


class NluUnavailableError(CvError):
    """NLU/地理编码能力部署期即不可用（FR-9 裁剪场景，FR-9.6）。"""

    def __init__(self, message: str):
        super().__init__("NLU_UNAVAILABLE", message, 503)


class InputTooLargeError(CvError):
    """输入规模超当前部署形态可处理上限（local-cpu 模式 >2048，R-04 语义）。"""

    def __init__(self, message: str):
        super().__init__("INPUT_TOO_LARGE", message, 400)


class LocationUnresolvedError(CvError):
    """意图与位置均缺失、解析完全不可执行（FR-9.8，评审 D-04）。

    与 503 NLU_UNAVAILABLE（能力不可用）明确区分：本码表示能力正常但内容无法定位。
    """

    def __init__(self, message: str):
        super().__init__("LOCATION_UNRESOLVED", message, 422)


class AlignmentFailedError(CvError):
    """配准偏差超阈值（FR-1.2/7.7 告警路径，评审 D-03）。

    message 须注明“自动配准能力本期未启用（M6 交付）”——不静默忽略（§3.2.5）。
    """

    def __init__(self, message: str):
        super().__init__("ALIGNMENT_FAILED", message, 422)


def register_error_handlers(app: FastAPI) -> None:
    """FastAPI 统一异常处理：CvError → {code, message, trace_id} JSON；兜底 500。"""

    @app.exception_handler(CvError)
    async def _cv_error_handler(request: Request, exc: CvError) -> JSONResponse:
        logger.warning(
            "cv_error",
            code=exc.code,
            http_status=exc.http_status,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "trace_id": request.headers.get("X-Trace-Id") or uuid.uuid4().hex,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", error=str(exc), path=request.url.path, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": f"内部错误：{exc}",
                "trace_id": request.headers.get("X-Trace-Id") or uuid.uuid4().hex,
            },
        )
