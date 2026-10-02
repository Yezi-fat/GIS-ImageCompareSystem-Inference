"""infer-service L1 部署配置（设计 §6，pydantic-settings，环境变量注入，重启生效）。

配置责任划分（FR-10）：本服务只持有 L1 部署配置；threshold/min_area/类别映射/
配色等业务策略参数由 Java 运行配置持有、逐请求下发（见 schema 请求体）。

URL 完整性（需求约束 #10）：remote.endpoint 为可直接发起请求的完整 URL
（协议+主机+端口+完整路径），代码不得拼接固定路径；启动时校验（非空时）
可解析为绝对地址，非法即拒绝启动。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_url(v: str) -> str:
    """URL 配置项启动校验：空串表示未配置（正常形态）；非空须为 http/https 绝对地址。"""
    if v and not v.startswith(("http://", "https://")):
        raise ValueError(f"URL 配置项须为完整绝对地址（含 http/https scheme），当前值：{v!r}")
    return v


# 合法推理提供方取值（小写连字符；registry 与配置校验共用，bug-2026-09-28 Q1-P1）
PROVIDERS: tuple[str, ...] = ("remote", "local-gpu", "local-cpu")


def _validate_provider(v: str) -> str:
    """provider 取值启动校验：非法值（如 Java 枚举风格 local_gpu 下划线形式）拒绝启动。

    历史排坑（bug-2026-09-28 Function-Q1）：下划线形式曾静默兜底 local-cpu 且无告警，
    表现为"改配置不生效"；故此处 fail-fast，与 URL 启动校验同一纪律。
    """
    if v not in PROVIDERS:
        raise ValueError(f"provider 取值须为 {list(PROVIDERS)} 之一（小写连字符），当前值：{v!r}")
    return v


class SecuritySettings(BaseModel):
    """FR-10.7 安全节结构视图（对齐设计 §6）：由顶层环境字段组装。

    注：pydantic-settings 嵌套模型的 validation_alias 解析不可靠（多字段时失效），
    故 AUTH_ENABLED/INTERNAL_TOKEN 以顶层扁平字段承接环境变量（与 Java 同源注入），
    本类仅作结构化视图。
    """

    auth_enabled: bool = True
    internal_token: str = ""


class ServiceSettings(BaseModel):
    port: int = Field(default=8001, description="服务监听端口")
    default_provider: str = Field(
        default="local-cpu",
        description="X-Inference-Provider 提示头缺省时的兜底提供方：remote / local-gpu / local-cpu；"
                    "注意 Java 正常路径必带提示头，本配置仅兜底（远程熔断回放等场景），"
                    "端到端切换提供方的入口在 Java 运行配置（bug-2026-09-28 Q1）",
    )

    _check_default_provider = field_validator("default_provider")(_validate_provider)


class SegmentationModelSettings(BaseModel):
    fp32: str = Field(default="fp32.onnx", description="FP32 产物文件名（{name}/{version}/ 下，GPU 路径使用）")
    int8: str = Field(default="int8.onnx", description="INT8 产物文件名（CPU 路径使用）")
    name: str = Field(default="landcover-seg", description="默认分割模型名（目录名）")
    version: str = Field(default="v2.0", description="默认分割模型版本（目录名）")
    class_ids: list[int] | None = Field(
        default=None,
        description="分割模型输出类别 ID 表（与 Java 要素目录 model_class_id 对齐，FR-6.2）；"
                    "缺省（null）时从模型 ONNX 元数据 names 自动推导——检测模型（YOLO 系）"
                    "类别序号直用、末位通道为背景；显式配置则以配置为准并做通道错配校验",
    )
    # 检测模型（YOLO 系）解码参数（P-014）：仅对 [1, 4+C, anchors] 检测输出格式生效；
    # 分割概率图模型（含伪模型）不使用。class_mapping 约定 = 模型类别序号直用（末位通道保留为背景）
    conf_threshold: float = Field(default=0.25, gt=0, lt=1, description="检测置信度过滤阈值")
    nms_threshold: float = Field(default=0.45, gt=0, lt=1, description="逐类 NMS IoU 阈值")


class ChangeDetectionModelSettings(BaseModel):
    fp32: str = Field(default="fp32.onnx", description="FP32 产物文件名")
    int8: str = Field(default="int8.onnx", description="INT8 产物文件名")
    name: str = Field(default="change-detection", description="默认变化检测模型名（目录名）")
    version: str = Field(default="v1.2", description="默认变化检测模型版本（目录名）")


class ModelsSettings(BaseModel):
    dir: str = Field(default="/models", description="模型文件挂载点（容器 volume），按 {name}/{version}/ 组织")
    segmentation: SegmentationModelSettings = SegmentationModelSettings()
    change_detection: ChangeDetectionModelSettings = ChangeDetectionModelSettings()


class TilingSettings(BaseModel):
    tile_size: int = Field(default=256, gt=0, description="Tile 边长（像素），推理按此流式切块")
    overlap: int = Field(default=32, ge=0, description="重叠像素（stride=tile_size-overlap），保证拼接无接缝（FR-6.7）")


class ConcurrencySettings(BaseModel):
    cpu_slots: int = Field(default=2, gt=0, description="CPU 推理信号量槽位数；intra_op_num_threads=核数/槽数")
    gpu_slots: int = Field(default=4, gt=0, description="GPU 推理信号量槽位数（remote 路径同用 CPU 槽位）")


class RemoteSettings(BaseModel):
    endpoint: str = Field(default="", description="完整推理端点 URL，直调不拼接（约束 #10）；空=未配置远程（正常形态）")
    timeout_s: int = Field(default=25, gt=0, description="单次调用超时，略小于 Java 侧 30s，保证错误先在本层成形")
    batch_size: int = Field(default=8, gt=0, description="Tile 批量推理分组大小（FR-1.9）")

    _check_endpoint = field_validator("endpoint")(_validate_url)


class LimitsSettings(BaseModel):
    local_cpu_max_input: int = Field(default=2048, gt=0, description="local-cpu 模式输入边长上限，超限 INPUT_TOO_LARGE（R-04）")


class StorageReaderSettings(BaseModel):
    timeout_s: int = Field(default=30, gt=0, description="预签名 URL 拉图超时（秒）")


class AlignmentSettings(BaseModel):
    """配准校验（§3.2.5，评审 D-03）：本期告警路径，自动配准列 M6。"""

    max_shift_px: int = Field(default=4, gt=0, description="整体平移偏差阈值（像素），超限返回 ALIGNMENT_FAILED")


class Settings(BaseSettings):
    """infer-service 配置根。环境变量嵌套分隔符 `__`（如 REMOTE__ENDPOINT）。"""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    # FR-10.7：与 Java 侧 security.auth.enabled 同一总开关，同一环境变量注入
    auth_enabled: bool = Field(default=True, description="内部令牌鉴权总开关（与 Java 同源注入；/health 恒豁免）")
    internal_token: str = Field(default="", description="内部共享密钥（X-Internal-Token 校验值，须与 Java 同值）")

    service: ServiceSettings = ServiceSettings()
    models: ModelsSettings = ModelsSettings()
    tiling: TilingSettings = TilingSettings()
    concurrency: ConcurrencySettings = ConcurrencySettings()
    remote: RemoteSettings = RemoteSettings()
    limits: LimitsSettings = LimitsSettings()
    storage_reader: StorageReaderSettings = StorageReaderSettings()
    alignment: AlignmentSettings = AlignmentSettings()

    @property
    def security(self) -> SecuritySettings:
        """安全节结构视图（见 SecuritySettings 注释）。"""
        return SecuritySettings(auth_enabled=self.auth_enabled, internal_token=self.internal_token)


settings = Settings()
