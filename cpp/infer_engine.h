/**
 * @file infer_engine.h
 * @brief TensorRT 推理引擎 C++ 接口头文件
 * 
 * 提供给 Python ctypes 调用的 C 接口
 * 编译后生成 libinfer.so
 */

#ifndef INFER_ENGINE_H
#define INFER_ENGINE_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 检测结果结构
 */
typedef struct {
    int class_id;        // 类别ID
    float confidence;    // 置信度 [0, 1]
    float bbox[4];       // 边界框 [x1, y1, x2, y2]
} Detection;

/**
 * @brief 检测结果集合
 */
typedef struct {
    Detection* detections;  // 检测结果数组
    int num_detections;     // 检测数量
} DetectionResult;

/**
 * @brief 加载 TensorRT engine 文件
 * 
 * @param engine_path engine 文件路径
 * @return 模型句柄（失败返回 NULL）
 */
void* load_engine(const char* engine_path);

/**
 * @brief 执行推理
 * 
 * @param handle 模型句柄
 * @param image_data 图像数据（BGR格式，HWC）
 * @param width 图像宽度
 * @param height 图像高度
 * @param conf_threshold 置信度阈值
 * @return 检测结果（需要调用 free_results 释放）
 */
DetectionResult* infer(void* handle, 
                      unsigned char* image_data,
                      int width,
                      int height,
                      float conf_threshold);

/**
 * @brief 获取检测结果数量
 * 
 * @param results 检测结果
 * @return 检测数量
 */
int get_num_detections(DetectionResult* results);

/**
 * @brief 获取单个检测结果
 * 
 * @param results 检测结果
 * @param index 索引
 * @param class_id 输出：类别ID
 * @param confidence 输出：置信度
 * @param bbox 输出：边界框 [x1, y1, x2, y2]
 */
void get_detection(DetectionResult* results,
                  int index,
                  int* class_id,
                  float* confidence,
                  float* bbox);

/**
 * @brief 释放检测结果
 * 
 * @param results 检测结果
 */
void free_results(DetectionResult* results);

/**
 * @brief 释放模型
 * 
 * @param handle 模型句柄
 */
void release_engine(void* handle);

#ifdef __cplusplus
}
#endif

#endif // INFER_ENGINE_H




