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


class SecuritySettings(BaseModel):
    """FR-10.7 安全节结构视图（对齐设计 §6）：由顶层环境字段组装。

    注：pydantic-settings 嵌套模型的 validation_alias 解析不可靠（多字段时失效），
    故 AUTH_ENABLED/INTERNAL_TOKEN 以顶层扁平字段承接环境变量（与 Java 同源注入），
    本类仅作结构化视图。
    """

    auth_enabled: bool = True
    internal_token: str = ""


class ServiceSettings(BaseModel):
    port: int = 8001
    default_provider: str = Field(
        default="local-cpu", description="provider 提示头缺省时的兜底：remote / local-gpu / local-cpu",
    )


class SegmentationModelSettings(BaseModel):
    fp32: str = "fp32.onnx"   # 文件名约定 {name}/{version}/{fp32|int8}.onnx（§3.1.2，评审 P-02）
    int8: str = "int8.onnx"
    name: str = "landcover-seg"
    version: str = "v2.0"
    class_ids: list[int] = Field(default=[0, 1, 2, 3, 4], description="0=背景；与要素目录映射对齐（FR-6.2）")
    # 检测模型（YOLO 系）解码参数（P-014）：仅对 [1, 4+C, anchors] 检测输出格式生效；
    # 分割概率图模型（含伪模型）不使用。class_mapping 约定 = COCO 类别序号 + 1（0 保留为背景）
    conf_threshold: float = Field(default=0.25, gt=0, lt=1, description="检测置信度过滤阈值")
    nms_threshold: float = Field(default=0.45, gt=0, lt=1, description="逐类 NMS IoU 阈值")


class ChangeDetectionModelSettings(BaseModel):
    fp32: str = "fp32.onnx"
    int8: str = "int8.onnx"
    name: str = "change-detection"
    version: str = "v1.2"


class ModelsSettings(BaseModel):
    dir: str = Field(default="/models", description="模型文件挂载点（容器 volume），按 {name}/{version}/ 组织")
    segmentation: SegmentationModelSettings = SegmentationModelSettings()
    change_detection: ChangeDetectionModelSettings = ChangeDetectionModelSettings()


class TilingSettings(BaseModel):
    tile_size: int = Field(default=256, gt=0)
    overlap: int = Field(default=32, ge=0, description="重叠像素（stride=tile_size-overlap），保证拼接无接缝（FR-6.7）")


class ConcurrencySettings(BaseModel):
    cpu_slots: int = Field(default=2, gt=0)
    gpu_slots: int = Field(default=4, gt=0)


class RemoteSettings(BaseModel):
    endpoint: str = Field(default="", description="完整推理端点 URL，直调不拼接（约束 #10）；空=未配置远程（正常形态）")
    timeout_s: int = Field(default=25, gt=0, description="单次调用超时，略小于 Java 侧 30s，保证错误先在本层成形")
    batch_size: int = Field(default=8, gt=0, description="Tile 批量推理分组大小（FR-1.9）")

    _check_endpoint = field_validator("endpoint")(_validate_url)


class LimitsSettings(BaseModel):
    local_cpu_max_input: int = Field(default=2048, gt=0, description="local-cpu 模式输入边长上限，超限 INPUT_TOO_LARGE（R-04）")


class StorageReaderSettings(BaseModel):
    timeout_s: int = Field(default=30, gt=0)


class AlignmentSettings(BaseModel):
    """配准校验（§3.2.5，评审 D-03）：本期告警路径，自动配准列 M6。"""

    max_shift_px: int = Field(default=4, gt=0, description="整体平移偏差阈值（像素），超限返回 ALIGNMENT_FAILED")


class Settings(BaseSettings):
    """infer-service 配置根。环境变量嵌套分隔符 `__`（如 REMOTE__ENDPOINT）。"""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    # FR-10.7：与 Java 侧 security.auth.enabled 同一总开关，同一环境变量注入
    auth_enabled: bool = True
    internal_token: str = ""

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
