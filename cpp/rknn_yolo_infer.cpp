/**
 * RKNN YOLO inference library for Python ctypes.
 *
 * Exported C ABI:
 *   void* model_init(const char* rknn_model_path)
 *   int   model_infer(void* handle, uint8_t* bgr, int w, int h, float conf,
 *                     int* class_ids, float* confs, float* boxes, int max_n)
 *   void  model_release(void* handle)
 */

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>

#include "rknn_api.h"

struct Detection {
    float x1;
    float y1;
    float x2;
    float y2;
    float confidence;
    int class_id;
};

struct ModelCtx {
    rknn_context rknn_ctx;
    int model_w;
    int model_h;
    int num_outputs;
    int num_classes;
    bool transposed;
    bool multi_output_yolo;
    int output_h[16];
    int output_w[16];
    int output_c[16];
};

static int attr_dim(const rknn_tensor_attr& attr, int logical_index_from_last) {
    const int idx = static_cast<int>(attr.n_dims) - 1 - logical_index_from_last;
    if (idx < 0 || idx >= static_cast<int>(attr.n_dims)) {
        return 1;
    }
    return static_cast<int>(attr.dims[idx]);
}

static int attr_element_count(const rknn_tensor_attr& attr) {
    int total = 1;
    for (uint32_t i = 0; i < attr.n_dims; ++i) {
        total *= std::max(1, static_cast<int>(attr.dims[i]));
    }
    return total;
}

static float sigmoid(float x) {
    return 1.0f / (1.0f + std::exp(-x));
}

static bool looks_like_logits(const float* data, int count) {
    const int sample = std::min(count, 64);
    for (int i = 0; i < sample; ++i) {
        if (data[i] < 0.0f || data[i] > 1.0f) {
            return true;
        }
    }
    return false;
}

static float score_value(const float* data, int offset, int count, bool logits) {
    if (offset < 0 || offset >= count) {
        return 0.0f;
    }
    const float v = data[offset];
    return logits ? sigmoid(v) : v;
}

static float dfl_distance(const float* box, int box_count, int channel_stride, int base_channel, int anchor_index) {
    const int reg_max = 16;
    float max_v = -1e30f;
    float vals[reg_max];
    for (int i = 0; i < reg_max; ++i) {
        const int offset = (base_channel + i) * channel_stride + anchor_index;
        vals[i] = (offset >= 0 && offset < box_count) ? box[offset] : 0.0f;
        if (vals[i] > max_v) {
            max_v = vals[i];
        }
    }

    float sum = 0.0f;
    float acc = 0.0f;
    for (int i = 0; i < reg_max; ++i) {
        const float e = std::exp(vals[i] - max_v);
        sum += e;
        acc += e * static_cast<float>(i);
    }
    return acc / (sum + 1e-6f);
}

static float box_iou(const Detection& a, const Detection& b) {
    const float ix1 = std::max(a.x1, b.x1);
    const float iy1 = std::max(a.y1, b.y1);
    const float ix2 = std::min(a.x2, b.x2);
    const float iy2 = std::min(a.y2, b.y2);
    const float inter_w = std::max(0.0f, ix2 - ix1);
    const float inter_h = std::max(0.0f, iy2 - iy1);
    const float inter = inter_w * inter_h;
    const float area_a = std::max(0.0f, a.x2 - a.x1) * std::max(0.0f, a.y2 - a.y1);
    const float area_b = std::max(0.0f, b.x2 - b.x1) * std::max(0.0f, b.y2 - b.y1);
    const float uni = area_a + area_b - inter;
    return inter / (uni + 1e-6f);
}

static std::vector<Detection> nms(std::vector<Detection>& dets, float nms_thresh) {
    std::sort(dets.begin(), dets.end(), [](const Detection& a, const Detection& b) {
        return a.confidence > b.confidence;
    });

    std::vector<bool> suppressed(dets.size(), false);
    std::vector<Detection> result;
    result.reserve(dets.size());

    for (size_t i = 0; i < dets.size(); ++i) {
        if (suppressed[i]) {
            continue;
        }
        result.push_back(dets[i]);
        for (size_t j = i + 1; j < dets.size(); ++j) {
            if (suppressed[j]) {
                continue;
            }
            if (dets[i].class_id != dets[j].class_id) {
                continue;
            }
            if (box_iou(dets[i], dets[j]) > nms_thresh) {
                suppressed[j] = true;
            }
        }
    }

    return result;
}

extern "C" {

void* model_init(const char* rknn_model_path) {
    if (rknn_model_path == nullptr) {
        std::fprintf(stderr, "[rknn_yolo] model path is null\n");
        return nullptr;
    }

    FILE* fp = std::fopen(rknn_model_path, "rb");
    if (fp == nullptr) {
        std::fprintf(stderr, "[rknn_yolo] failed to open model: %s\n", rknn_model_path);
        return nullptr;
    }

    std::fseek(fp, 0, SEEK_END);
    const long model_size = std::ftell(fp);
    std::fseek(fp, 0, SEEK_SET);
    if (model_size <= 0) {
        std::fprintf(stderr, "[rknn_yolo] invalid model size: %ld\n", model_size);
        std::fclose(fp);
        return nullptr;
    }

    unsigned char* model_data = static_cast<unsigned char*>(std::malloc(model_size));
    if (model_data == nullptr) {
        std::fprintf(stderr, "[rknn_yolo] malloc model buffer failed\n");
        std::fclose(fp);
        return nullptr;
    }

    const size_t read_size = std::fread(model_data, 1, static_cast<size_t>(model_size), fp);
    std::fclose(fp);
    if (read_size != static_cast<size_t>(model_size)) {
        std::fprintf(stderr, "[rknn_yolo] read model failed: %zu / %ld\n", read_size, model_size);
        std::free(model_data);
        return nullptr;
    }

    ModelCtx* ctx = new ModelCtx();
    std::memset(ctx, 0, sizeof(ModelCtx));

    const int ret = rknn_init(&ctx->rknn_ctx, model_data, model_size, 0, nullptr);
    std::free(model_data);
    if (ret < 0) {
        std::fprintf(stderr, "[rknn_yolo] rknn_init failed: %d\n", ret);
        delete ctx;
        return nullptr;
    }

    rknn_input_output_num io_num;
    std::memset(&io_num, 0, sizeof(io_num));
    if (rknn_query(ctx->rknn_ctx, RKNN_QUERY_IN_OUT_NUM, &io_num, sizeof(io_num)) < 0) {
        std::fprintf(stderr, "[rknn_yolo] query in/out num failed\n");
        rknn_destroy(ctx->rknn_ctx);
        delete ctx;
        return nullptr;
    }
    ctx->num_outputs = io_num.n_output;

    rknn_tensor_attr in_attr;
    std::memset(&in_attr, 0, sizeof(in_attr));
    in_attr.index = 0;
    if (rknn_query(ctx->rknn_ctx, RKNN_QUERY_INPUT_ATTR, &in_attr, sizeof(in_attr)) < 0) {
        std::fprintf(stderr, "[rknn_yolo] query input attr failed\n");
        rknn_destroy(ctx->rknn_ctx);
        delete ctx;
        return nullptr;
    }

    if (in_attr.fmt == RKNN_TENSOR_NCHW) {
        ctx->model_h = static_cast<int>(in_attr.dims[2]);
        ctx->model_w = static_cast<int>(in_attr.dims[3]);
    } else {
        ctx->model_h = static_cast<int>(in_attr.dims[1]);
        ctx->model_w = static_cast<int>(in_attr.dims[2]);
    }

    rknn_tensor_attr out_attr;
    std::memset(&out_attr, 0, sizeof(out_attr));
    out_attr.index = 0;
    if (rknn_query(ctx->rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &out_attr, sizeof(out_attr)) < 0) {
        std::fprintf(stderr, "[rknn_yolo] query output attr failed\n");
        rknn_destroy(ctx->rknn_ctx);
        delete ctx;
        return nullptr;
    }

    ctx->transposed = false;
    ctx->num_classes = 1;
    ctx->multi_output_yolo = ctx->num_outputs == 6 || ctx->num_outputs == 9;
    std::memset(ctx->output_h, 0, sizeof(ctx->output_h));
    std::memset(ctx->output_w, 0, sizeof(ctx->output_w));
    std::memset(ctx->output_c, 0, sizeof(ctx->output_c));

    for (int i = 0; i < ctx->num_outputs && i < 16; ++i) {
        rknn_tensor_attr attr;
        std::memset(&attr, 0, sizeof(attr));
        attr.index = i;
        if (rknn_query(ctx->rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &attr, sizeof(attr)) >= 0) {
            ctx->output_w[i] = attr_dim(attr, 0);
            ctx->output_h[i] = attr_dim(attr, 1);
            ctx->output_c[i] = attr_dim(attr, 2);
            std::fprintf(
                stderr,
                "[rknn_yolo] output[%d]: n_dims=%u dims=(%d,%d,%d,%d) fmt=%d elems=%d\n",
                i,
                attr.n_dims,
                attr.n_dims > 0 ? static_cast<int>(attr.dims[0]) : 0,
                attr.n_dims > 1 ? static_cast<int>(attr.dims[1]) : 0,
                attr.n_dims > 2 ? static_cast<int>(attr.dims[2]) : 0,
                attr.n_dims > 3 ? static_cast<int>(attr.dims[3]) : 0,
                static_cast<int>(attr.fmt),
                attr_element_count(attr));
        }
    }

    if (out_attr.n_dims == 3) {
        const int d1 = static_cast<int>(out_attr.dims[1]);
        const int d2 = static_cast<int>(out_attr.dims[2]);
        if (d2 == 8400) {
            ctx->num_classes = std::max(1, d1 - 4);
            ctx->transposed = false;
        } else if (d1 == 8400) {
            ctx->num_classes = std::max(1, d2 - 4);
            ctx->transposed = true;
        } else if (d1 < d2) {
            ctx->num_classes = std::max(1, d1 - 4);
            ctx->transposed = false;
        } else {
            ctx->num_classes = std::max(1, d2 - 4);
            ctx->transposed = true;
        }
    }

    std::fprintf(
        stderr,
        "[rknn_yolo] loaded: %s\n"
        "  input=%dx%d classes=%d outputs=%d transposed=%s multi_output=%s\n",
        rknn_model_path,
        ctx->model_w,
        ctx->model_h,
        ctx->num_classes,
        ctx->num_outputs,
        ctx->transposed ? "true" : "false",
        ctx->multi_output_yolo ? "true" : "false");

    return static_cast<void*>(ctx);
}

int model_infer(void* handle,
                unsigned char* bgr_data,
                int width,
                int height,
                float conf_threshold,
                int* out_class_ids,
                float* out_confidences,
                float* out_boxes,
                int max_detections) {
    if (handle == nullptr || bgr_data == nullptr || width <= 0 || height <= 0 ||
        out_class_ids == nullptr || out_confidences == nullptr || out_boxes == nullptr ||
        max_detections <= 0) {
        return -1;
    }

    ModelCtx* ctx = static_cast<ModelCtx*>(handle);
    const int mw = ctx->model_w;
    const int mh = ctx->model_h;

    const float scale = std::min(static_cast<float>(mw) / static_cast<float>(width),
                                 static_cast<float>(mh) / static_cast<float>(height));
    const int new_w = static_cast<int>(width * scale);
    const int new_h = static_cast<int>(height * scale);
    const int pad_x = (mw - new_w) / 2;
    const int pad_y = (mh - new_h) / 2;

    uint8_t* input_buf = static_cast<uint8_t*>(std::malloc(static_cast<size_t>(mh * mw * 3)));
    if (input_buf == nullptr) {
        return -1;
    }
    std::memset(input_buf, 114, static_cast<size_t>(mh * mw * 3));

    for (int y = 0; y < new_h; ++y) {
        const int sy = std::min(static_cast<int>(y / scale), height - 1);
        for (int x = 0; x < new_w; ++x) {
            const int sx = std::min(static_cast<int>(x / scale), width - 1);
            const uint8_t* src = bgr_data + (sy * width + sx) * 3;
            uint8_t* dst = input_buf + ((y + pad_y) * mw + (x + pad_x)) * 3;
            dst[0] = src[2];
            dst[1] = src[1];
            dst[2] = src[0];
        }
    }

    rknn_input inputs[1];
    std::memset(inputs, 0, sizeof(inputs));
    inputs[0].index = 0;
    inputs[0].type = RKNN_TENSOR_UINT8;
    inputs[0].fmt = RKNN_TENSOR_NHWC;
    inputs[0].size = static_cast<uint32_t>(mh * mw * 3);
    inputs[0].buf = input_buf;

    int ret = rknn_inputs_set(ctx->rknn_ctx, 1, inputs);
    if (ret < 0) {
        std::fprintf(stderr, "[rknn_yolo] rknn_inputs_set failed: %d\n", ret);
        std::free(input_buf);
        return -1;
    }

    ret = rknn_run(ctx->rknn_ctx, nullptr);
    std::free(input_buf);
    if (ret < 0) {
        std::fprintf(stderr, "[rknn_yolo] rknn_run failed: %d\n", ret);
        return -1;
    }

    rknn_output* outputs = static_cast<rknn_output*>(
        std::calloc(static_cast<size_t>(ctx->num_outputs), sizeof(rknn_output)));
    if (outputs == nullptr) {
        return -1;
    }
    for (int i = 0; i < ctx->num_outputs; ++i) {
        outputs[i].want_float = 1;
    }

    ret = rknn_outputs_get(ctx->rknn_ctx, ctx->num_outputs, outputs, nullptr);
    if (ret < 0) {
        std::fprintf(stderr, "[rknn_yolo] rknn_outputs_get failed: %d\n", ret);
        std::free(outputs);
        return -1;
    }

    std::vector<Detection> dets;
    dets.reserve(256);

    if (ctx->multi_output_yolo) {
        const int pair_per_branch = ctx->num_outputs / 3;
        for (int branch = 0; branch < 3; ++branch) {
            const int box_i = branch * pair_per_branch;
            const int cls_i = box_i + 1;
            const int obj_i = pair_per_branch == 3 ? box_i + 2 : -1;

            float* box = static_cast<float*>(outputs[box_i].buf);
            float* cls = static_cast<float*>(outputs[cls_i].buf);
            float* obj = obj_i >= 0 ? static_cast<float*>(outputs[obj_i].buf) : nullptr;
            const int grid_h = std::max(1, ctx->output_h[box_i]);
            const int grid_w = std::max(1, ctx->output_w[box_i]);
            const int anchors = grid_h * grid_w;

            rknn_tensor_attr box_attr;
            rknn_tensor_attr cls_attr;
            rknn_tensor_attr obj_attr;
            std::memset(&box_attr, 0, sizeof(box_attr));
            std::memset(&cls_attr, 0, sizeof(cls_attr));
            std::memset(&obj_attr, 0, sizeof(obj_attr));
            box_attr.index = box_i;
            cls_attr.index = cls_i;
            if (obj_i >= 0) {
                obj_attr.index = obj_i;
            }
            rknn_query(ctx->rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &box_attr, sizeof(box_attr));
            rknn_query(ctx->rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &cls_attr, sizeof(cls_attr));
            if (obj_i >= 0) {
                rknn_query(ctx->rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &obj_attr, sizeof(obj_attr));
            }

            const int box_elements = attr_element_count(box_attr);
            const int cls_elements = attr_element_count(cls_attr);
            const int obj_elements = obj_i >= 0 ? attr_element_count(obj_attr) : 0;
            const int cls_channels = std::max(1, cls_elements / std::max(1, anchors));
            const int stride = std::max(1, ctx->model_w / grid_w);
            const bool cls_logits = looks_like_logits(cls, cls_elements);
            const bool obj_logits = obj != nullptr && looks_like_logits(obj, obj_elements);

            for (int y = 0; y < grid_h; ++y) {
                for (int x = 0; x < grid_w; ++x) {
                    const int a = y * grid_w + x;
                    float best_score = 0.0f;
                    int best_cls = 0;
                    for (int c = 0; c < cls_channels; ++c) {
                        float s = score_value(cls, c * anchors + a, cls_elements, cls_logits);
                        if (obj != nullptr) {
                            s *= score_value(obj, a, obj_elements, obj_logits);
                        }
                        if (s > best_score) {
                            best_score = s;
                            best_cls = c;
                        }
                    }
                    if (best_score < conf_threshold) {
                        continue;
                    }

                    float l = 0.0f;
                    float t = 0.0f;
                    float r = 0.0f;
                    float b = 0.0f;
                    if (box_elements / std::max(1, anchors) >= 64) {
                        l = dfl_distance(box, box_elements, anchors, 0, a);
                        t = dfl_distance(box, box_elements, anchors, 16, a);
                        r = dfl_distance(box, box_elements, anchors, 32, a);
                        b = dfl_distance(box, box_elements, anchors, 48, a);
                    } else {
                        l = box[0 * anchors + a];
                        t = box[1 * anchors + a];
                        r = box[2 * anchors + a];
                        b = box[3 * anchors + a];
                    }

                    const float cx = (static_cast<float>(x) + 0.5f) * stride;
                    const float cy = (static_cast<float>(y) + 0.5f) * stride;
                    Detection det;
                    det.x1 = (cx - l * stride - static_cast<float>(pad_x)) / scale;
                    det.y1 = (cy - t * stride - static_cast<float>(pad_y)) / scale;
                    det.x2 = (cx + r * stride - static_cast<float>(pad_x)) / scale;
                    det.y2 = (cy + b * stride - static_cast<float>(pad_y)) / scale;
                    det.x1 = std::max(0.0f, std::min(det.x1, static_cast<float>(width)));
                    det.y1 = std::max(0.0f, std::min(det.y1, static_cast<float>(height)));
                    det.x2 = std::max(0.0f, std::min(det.x2, static_cast<float>(width)));
                    det.y2 = std::max(0.0f, std::min(det.y2, static_cast<float>(height)));
                    det.confidence = best_score;
                    det.class_id = best_cls;

                    if (det.x2 > det.x1 && det.y2 > det.y1) {
                        dets.push_back(det);
                    }
                }
            }
        }
    } else {
        float* raw = static_cast<float*>(outputs[0].buf);
        const int nc = ctx->num_classes;

        int num_anchors = 8400;
        rknn_tensor_attr out_attr;
        std::memset(&out_attr, 0, sizeof(out_attr));
        out_attr.index = 0;
        if (rknn_query(ctx->rknn_ctx, RKNN_QUERY_OUTPUT_ATTR, &out_attr, sizeof(out_attr)) >= 0 &&
            out_attr.n_dims == 3) {
            num_anchors = ctx->transposed ? static_cast<int>(out_attr.dims[1])
                                          : static_cast<int>(out_attr.dims[2]);
        }

        for (int a = 0; a < num_anchors; ++a) {
            float cx = 0.0f;
            float cy = 0.0f;
            float bw = 0.0f;
            float bh = 0.0f;
            float best_score = 0.0f;
            int best_cls = 0;

            if (!ctx->transposed) {
                cx = raw[0 * num_anchors + a];
                cy = raw[1 * num_anchors + a];
                bw = raw[2 * num_anchors + a];
                bh = raw[3 * num_anchors + a];
                for (int c = 0; c < nc; ++c) {
                    const float s = raw[(4 + c) * num_anchors + a];
                    if (s > best_score) {
                        best_score = s;
                        best_cls = c;
                    }
                }
            } else {
                const float* row = raw + a * (4 + nc);
                cx = row[0];
                cy = row[1];
                bw = row[2];
                bh = row[3];
                for (int c = 0; c < nc; ++c) {
                    const float s = row[4 + c];
                    if (s > best_score) {
                        best_score = s;
                        best_cls = c;
                    }
                }
            }

            if (best_score < conf_threshold) {
                continue;
            }

            Detection det;
            det.x1 = (cx - bw * 0.5f - static_cast<float>(pad_x)) / scale;
            det.y1 = (cy - bh * 0.5f - static_cast<float>(pad_y)) / scale;
            det.x2 = (cx + bw * 0.5f - static_cast<float>(pad_x)) / scale;
            det.y2 = (cy + bh * 0.5f - static_cast<float>(pad_y)) / scale;
            det.x1 = std::max(0.0f, std::min(det.x1, static_cast<float>(width)));
            det.y1 = std::max(0.0f, std::min(det.y1, static_cast<float>(height)));
            det.x2 = std::max(0.0f, std::min(det.x2, static_cast<float>(width)));
            det.y2 = std::max(0.0f, std::min(det.y2, static_cast<float>(height)));
            det.confidence = best_score;
            det.class_id = best_cls;

            if (det.x2 > det.x1 && det.y2 > det.y1) {
                dets.push_back(det);
            }
        }
    }

    std::vector<Detection> final_dets = nms(dets, 0.45f);
    const int count = std::min(static_cast<int>(final_dets.size()), max_detections);
    for (int i = 0; i < count; ++i) {
        out_class_ids[i] = final_dets[i].class_id;
        out_confidences[i] = final_dets[i].confidence;
        out_boxes[i * 4 + 0] = final_dets[i].x1;
        out_boxes[i * 4 + 1] = final_dets[i].y1;
        out_boxes[i * 4 + 2] = final_dets[i].x2;
        out_boxes[i * 4 + 3] = final_dets[i].y2;
    }

    rknn_outputs_release(ctx->rknn_ctx, ctx->num_outputs, outputs);
    std::free(outputs);
    return count;
}

void model_release(void* handle) {
    if (handle == nullptr) {
        return;
    }
    ModelCtx* ctx = static_cast<ModelCtx*>(handle);
    rknn_destroy(ctx->rknn_ctx);
    delete ctx;
}

}  // extern "C"
