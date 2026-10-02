# step4_quantize.py
import os
import cv2
import numpy as np
import onnxruntime as ort
from onnxruntime.quantization import CalibrationDataReader, quantize_static, QuantType
from tqdm import tqdm  # 💎 引入进度条库

class YOLO11DataReader(CalibrationDataReader):
    """ 专为 YOLOv11 设计的校准数据读取器（带进度条显示） """
    def __init__(self, image_dir, input_name, input_size=(640, 640)):
        super().__init__()
        self.image_paths = [os.path.join(image_dir, f) for f in os.listdir(image_dir) if f.endswith(('.jpg', '.jpeg', '.png'))]
        self.input_name = input_name
        self.input_size = input_size
        self.cursor = 0
        
        # 💎 初始化进度条，总数为校准图片的总量
        self.pbar = tqdm(total=len(self.image_paths), desc="🚀 INT8 量化校准进度", unit="img")

    def preprocess(self, img_path):
        img = cv2.imread(img_path)
        h, w, _ = img.shape
        
        # 解包元组，防止 tuple 和 int 相除报错
        target_h, target_w = self.input_size
        scale = min(target_h / h, target_w / w)
        nh, nw = int(h * scale), int(w * scale)
        img_resized = cv2.resize(img, (nw, nh))
        
        # 使用整数尺寸创建画布
        canvas = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (target_w - nw) // 2, (target_h - nh) // 2
        canvas[pad_y : pad_y + nh, pad_x : pad_x + nw, :] = img_resized
        
        img_in = canvas[:, :, ::-1].transpose(2, 0, 1)
        img_in = np.ascontiguousarray(img_in, dtype=np.float32) / 255.0
        return np.expand_dims(img_in, axis=0)

    def get_next(self):
        if self.cursor >= len(self.image_paths):
            self.pbar.close()  # 💎 校准结束时，关闭进度条
            return None
            
        img_path = self.image_paths[self.cursor]
        self.cursor += 1
        
        # 💎 每次引擎成功读取一张图，进度条就前进 1 格
        self.pbar.update(1)
        
        return {self.input_name: self.preprocess(img_path)}

if __name__ == "__main__":
    # 绝对路径配置
    fp32_path = "/home/bowserzhang/PythonProject/GIS-ImageCompareSystem-Inference/models/yolo11/v1.0/fp32/fp32.onnx"
    int8_path = "/home/bowserzhang/PythonProject/GIS-ImageCompareSystem-Inference/models/yolo11/v1.0/int8/int8.onnx"
    calib_images_dir = "/home/bowserzhang/PythonProject/GIS-ImageCompareSystem-Inference/models/calib_images/val2017"
    
    if not os.path.exists(calib_images_dir) or not os.listdir(calib_images_dir):
        raise ValueError(f"❌ 请先在 {calib_images_dir} 文件夹中放入一些校准图片！")

    # 获取输入节点名称
    # session = ort.InferenceSession(fp32_path, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
    session = ort.InferenceSession(fp32_path, providers=['CUDAExecutionProvider'])
    print(session.get_inputs())
    input_name = session.get_inputs()[0].name
    
    # 启动数据读取器（进度条会自动在控制台打印出来）
    data_reader = YOLO11DataReader(calib_images_dir, input_name)
    
    print("⚡ 4080s GPU 已就绪，正在启动静态量化推理...")
    quantize_static(
        model_input=fp32_path,
        model_output=int8_path,
        calibration_data_reader=data_reader,
        quant_format=QuantType.QInt8,
        per_channel=True,
        weight_type=QuantType.QInt8,
        extra_options={
            'ExecutionProvider': 'CUDAExecutionProvider'  # 强行指定校准推理引擎转移至 GPU
        }
    )
    print(f"\n🎉 INT8 量化成功！模型已成功保存至: {int8_path}")