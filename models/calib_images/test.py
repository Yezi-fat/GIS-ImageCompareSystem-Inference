# step5_deploy.py
import os
import cv2
import numpy as np
import onnxruntime as ort

def load_structured_model(base_dir, model_name, version, precision):
    """ 按照 {name}/{version}/{precision}.onnx 规范动态加载模型 """
    model_path = os.path.join(base_dir, model_name, version, precision, f"{precision}.onnx")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"❌ 找不到模型: {model_path}")
        
    print(f"⚙️ 正在加载模型并编译 4080s 专属算子: {model_path}")
    
    # 4080s 生产级加速配置
    providers = [
        ('TensorrtExecutionProvider', {
            'device_id': 0,
            'trt_max_workspace_size': 4294967296, # 4GB 显存分配
            'trt_fp16_enable': True if precision == "fp32" else False # fp32模型开启trt的fp16加速
        }),
        ('CUDAExecutionProvider', {'device_id': 0}),
        'CPUExecutionProvider'
    ]
    return ort.InferenceSession(model_path, providers=providers)

def preprocess(img_path, input_size=640):
    """ 图像前处理：保持比例缩放并居中填充 (Letterbox) """
    img = cv2.imread(img_path)
    h, w, _ = img.shape
    scale = min(input_size / h, input_size / w)
    nh, nw = int(h * scale), int(w * scale)
    img_resized = cv2.resize(img, (nw, nh))
    
    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    pad_x, pad_y = (input_size - nw) // 2, (input_size - nh) // 2
    canvas[pad_y : pad_y + nh, pad_x : pad_x + nw, :] = img_resized
    
    img_in = canvas[:, :, ::-1].transpose(2, 0, 1)
    img_in = np.ascontiguousarray(img_in, dtype=np.float32) / 255.0
    return np.expand_dims(img_in, axis=0), img, scale, pad_x, pad_y

def postprocess(outputs, orig_img, scale, pad_x, pad_y, conf_thresh=0.25, iou_thresh=0.45):
    """ 后处理：解析 [1, 84, 8400] 输出并渲染边界框 """
    preds = np.squeeze(outputs).T # [8400, 84]
    boxes = preds[:, :4]
    scores = preds[:, 4:]
    
    class_ids = np.argmax(scores, axis=1)
    confidences = np.max(scores, axis=1)
    
    mask = confidences > conf_thresh
    boxes, confidences, class_ids = boxes[mask], confidences[mask], class_ids[mask]
    
    if len(boxes) == 0:
        return orig_img

    # 坐标转换与缩放还原
    x_c, y_c, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    x1 = (x_c - w / 2 - pad_x) / scale
    y1 = (y_c - h / 2 - pad_y) / scale
    w_orig, h_orig = w / scale, h / scale
    
    re_boxes = np.stack([x1, y1, w_orig, h_orig], axis=1).astype(int).tolist()
    indices = cv2.dnn.NMSBoxes(re_boxes, confidences.tolist(), conf_thresh, iou_thresh)
    
    for i in indices:
        idx = i[0] if isinstance(i, (list, np.ndarray)) else i
        box = re_boxes[idx]
        cv2.rectangle(orig_img, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), (0, 255, 0), 2)
        cv2.putText(orig_img, f"ID:{class_ids[idx]} {confidences[idx]:.2f}", (box[0], box[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    return orig_img

if __name__ == "__main__":
    # 配置你想调用的模型信息
    MODEL_DIR = "/home/bowserzhang/PythonProject/GIS-ImageCompareSystem-Inference/models"
    NAME = "yolo11"
    VERSION = "v1.0"
    PRECISION = "fp32"   # 💎 随时可以修改为 "fp32" 或是 "int8"
    
    TEST_IMAGE = "./val2017/000000100238.jpg" # 换成你校准集里的某张图片路径验证
    
    if not os.path.exists(TEST_IMAGE):
        print(f"❌ 找不到测试图片 {TEST_IMAGE}，请先在代码里指定一张真实存在的图片路径。")
        exit()

    session = load_structured_model(MODEL_DIR, NAME, VERSION, PRECISION)
    input_name = session.get_inputs()[0].name
    
    # 执行推理
    img_in, orig_img, scale, pad_x, pad_y = preprocess(TEST_IMAGE)
    outputs = session.run(None, {input_name: img_in})
    
    # 渲染并保存
    res_img = postprocess(outputs, orig_img, scale, pad_x, pad_y)
    cv2.imwrite("./deploy_result.jpg", res_img)
    print("🚀 推理完全成功！渲染结果已保存至：./deploy_result.jpg")