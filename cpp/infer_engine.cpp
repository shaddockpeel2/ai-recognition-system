/**
 * @file infer_engine.cpp
 * @brief TensorRT 推理引擎 C++ 实现
 * 
 * 依赖:
 * - TensorRT 8.x+
 * - CUDA 11.x+
 * - cuDNN 8.x+
 * 
 * 编译命令:
 * g++ -shared -fPIC \
 *     -o libinfer.so \
 *     infer_engine.cpp \
 *     -I/usr/local/cuda/include \
 *     -I/usr/include/x86_64-linux-gnu \
 *     -L/usr/local/cuda/lib64 \
 *     -L/usr/lib/x86_64-linux-gnu \
 *     -lnvinfer -lcudart -lcudnn \
 *     -std=c++14 -O3
 */

#include "infer_engine.h"
#include <NvInfer.h>
#include <cuda_runtime_api.h>
#include <fstream>
#include <iostream>
#include <memory>
#include <vector>
#include <cstring>

using namespace nvinfer1;

/**
 * @brief TensorRT Logger
 */
class Logger : public ILogger {
public:
    void log(Severity severity, const char* msg) noexcept override {
        // 只输出警告和错误
        if (severity <= Severity::kWARNING) {
            std::cout << "[TensorRT] " << msg << std::endl;
        }
    }
} gLogger;

/**
 * @brief 推理引擎上下文
 */
struct EngineContext {
    IRuntime* runtime;
    ICudaEngine* engine;
    IExecutionContext* context;
    
    // CUDA 内存
    void* input_device;
    void* output_device;
    
    // 模型参数
    int input_size;      // 输入大小（如 640x640x3）
    int output_size;     // 输出大小
    int batch_size;
    
    EngineContext() : runtime(nullptr), engine(nullptr), context(nullptr),
                     input_device(nullptr), output_device(nullptr),
                     input_size(0), output_size(0), batch_size(1) {}
    
    ~EngineContext() {
        // 释放 CUDA 内存
        if (input_device) cudaFree(input_device);
        if (output_device) cudaFree(output_device);
        
        // 释放 TensorRT 对象
        if (context) context->destroy();
        if (engine) engine->destroy();
        if (runtime) runtime->destroy();
    }
};

/**
 * @brief 加载 TensorRT engine 文件
 */
void* load_engine(const char* engine_path) {
    if (!engine_path) {
        std::cerr << "[libinfer] Error: engine_path is null" << std::endl;
        return nullptr;
    }
    
    std::cout << "[libinfer] Loading engine: " << engine_path << std::endl;
    
    // 读取 engine 文件
    std::ifstream file(engine_path, std::ios::binary);
    if (!file.good()) {
        std::cerr << "[libinfer] Error: Cannot open engine file: " << engine_path << std::endl;
        return nullptr;
    }
    
    file.seekg(0, file.end);
    size_t size = file.tellg();
    file.seekg(0, file.beg);
    
    std::vector<char> engine_data(size);
    file.read(engine_data.data(), size);
    file.close();
    
    std::cout << "[libinfer] Engine file size: " << size / (1024*1024) << " MB" << std::endl;
    
    // 创建上下文
    EngineContext* ctx = new EngineContext();
    
    // 创建 runtime
    ctx->runtime = createInferRuntime(gLogger);
    if (!ctx->runtime) {
        std::cerr << "[libinfer] Error: Failed to create runtime" << std::endl;
        delete ctx;
        return nullptr;
    }
    
    // 反序列化 engine
    ctx->engine = ctx->runtime->deserializeCudaEngine(engine_data.data(), size);
    if (!ctx->engine) {
        std::cerr << "[libinfer] Error: Failed to deserialize engine" << std::endl;
        delete ctx;
        return nullptr;
    }
    
    // 创建执行上下文
    ctx->context = ctx->engine->createExecutionContext();
    if (!ctx->context) {
        std::cerr << "[libinfer] Error: Failed to create execution context" << std::endl;
        delete ctx;
        return nullptr;
    }
    
    // 获取输入输出尺寸
    // 假设: input binding index = 0, output binding index = 1
    Dims input_dims = ctx->engine->getBindingDimensions(0);
    Dims output_dims = ctx->engine->getBindingDimensions(1);
    
    // 计算输入输出大小
    ctx->batch_size = input_dims.d[0];
    int input_c = input_dims.d[1];
    int input_h = input_dims.d[2];
    int input_w = input_dims.d[3];
    ctx->input_size = ctx->batch_size * input_c * input_h * input_w;
    
    ctx->output_size = ctx->batch_size;
    for (int i = 1; i < output_dims.nbDims; i++) {
        ctx->output_size *= output_dims.d[i];
    }
    
    std::cout << "[libinfer] Input shape: [" << ctx->batch_size << ", " 
              << input_c << ", " << input_h << ", " << input_w << "]" << std::endl;
    std::cout << "[libinfer] Output size: " << ctx->output_size << std::endl;
    
    // 分配 CUDA 内存
    cudaMalloc(&ctx->input_device, ctx->input_size * sizeof(float));
    cudaMalloc(&ctx->output_device, ctx->output_size * sizeof(float));
    
    std::cout << "[libinfer] Engine loaded successfully" << std::endl;
    
    return static_cast<void*>(ctx);
}

/**
 * @brief 执行推理
 */
DetectionResult* infer(void* handle,
                      unsigned char* image_data,
                      int width,
                      int height,
                      float conf_threshold) {
    if (!handle || !image_data) {
        std::cerr << "[libinfer] Error: Invalid parameters" << std::endl;
        return nullptr;
    }
    
    EngineContext* ctx = static_cast<EngineContext*>(handle);
    
    // 1. 预处理：BGR -> RGB, HWC -> CHW, normalize
    std::vector<float> input_data(ctx->input_size);
    
    // 简化版预处理（实际应该更复杂）
    // TODO: 实现完整的图像预处理
    for (int i = 0; i < ctx->input_size; i++) {
        input_data[i] = static_cast<float>(image_data[i]) / 255.0f;
    }
    
    // 2. 拷贝到 GPU
    cudaMemcpy(ctx->input_device, input_data.data(), 
               ctx->input_size * sizeof(float), cudaMemcpyHostToDevice);
    
    // 3. 执行推理
    void* bindings[] = {ctx->input_device, ctx->output_device};
    bool status = ctx->context->executeV2(bindings);
    
    if (!status) {
        std::cerr << "[libinfer] Error: Inference failed" << std::endl;
        return nullptr;
    }
    
    // 4. 拷贝结果回 CPU
    std::vector<float> output_data(ctx->output_size);
    cudaMemcpy(output_data.data(), ctx->output_device,
               ctx->output_size * sizeof(float), cudaMemcpyDeviceToHost);
    
    // 5. 后处理：解析 YOLO 输出
    // TODO: 实现 YOLO 输出解析和 NMS
    // 这里简化为固定返回格式
    
    DetectionResult* result = new DetectionResult();
    
    // 示例：假设检测到 0 个目标
    result->num_detections = 0;
    result->detections = nullptr;
    
    // 实际应该解析 output_data 并填充 detections
    // 格式通常为: [x_center, y_center, width, height, conf, class_scores...]
    
    return result;
}

/**
 * @brief 获取检测结果数量
 */
int get_num_detections(DetectionResult* results) {
    if (!results) return 0;
    return results->num_detections;
}

/**
 * @brief 获取单个检测结果
 */
void get_detection(DetectionResult* results,
                  int index,
                  int* class_id,
                  float* confidence,
                  float* bbox) {
    if (!results || index < 0 || index >= results->num_detections) {
        return;
    }
    
    Detection& det = results->detections[index];
    
    if (class_id) *class_id = det.class_id;
    if (confidence) *confidence = det.confidence;
    if (bbox) {
        bbox[0] = det.bbox[0];
        bbox[1] = det.bbox[1];
        bbox[2] = det.bbox[2];
        bbox[3] = det.bbox[3];
    }
}

/**
 * @brief 释放检测结果
 */
void free_results(DetectionResult* results) {
    if (results) {
        if (results->detections) {
            delete[] results->detections;
        }
        delete results;
    }
}

/**
 * @brief 释放模型
 */
void release_engine(void* handle) {
    if (handle) {
        EngineContext* ctx = static_cast<EngineContext*>(handle);
        delete ctx;
        std::cout << "[libinfer] Engine released" << std::endl;
    }
}




