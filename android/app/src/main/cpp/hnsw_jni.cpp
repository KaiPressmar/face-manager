#include <jni.h>
#include <algorithm>
#include <cmath>
#include <memory>
#include <limits>
#include <stdexcept>
#include <vector>
#include "hnswlib/hnswlib.h"

namespace {
struct Index {
    int dim;
    size_t maximum;
    hnswlib::InnerProductSpace space;
    hnswlib::HierarchicalNSW<float> graph;
    Index(int d, int capacity, int ef, int m, long seed)
        : dim(d), maximum(capacity), space(d),
          graph(&space, std::min(capacity, 1024), m, ef, seed) {}
};
Index* ptr(jlong value) {
    if (!value) throw std::runtime_error("HNSW index is closed");
    return reinterpret_cast<Index*>(value);
}
void fail(JNIEnv* env, const std::exception& e) {
    env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"), e.what());
}
std::vector<float> vectors(JNIEnv* env, jfloatArray data, int dim) {
    if (!data || env->GetArrayLength(data) % dim)
        throw std::invalid_argument("Embedding matrix dimensions do not match index");
    std::vector<float> values(env->GetArrayLength(data));
    env->GetFloatArrayRegion(data, 0, values.size(), values.data());
    if (env->ExceptionCheck()) throw std::runtime_error("Cannot read embedding array");
    for (size_t start = 0; start < values.size(); start += dim) {
        double norm = 0;
        for (int i = 0; i < dim; i++) {
            float v = values[start + i];
            if (!std::isfinite(v)) throw std::invalid_argument("Embedding must contain finite values");
            norm += static_cast<double>(v) * v;
        }
        // Match upstream cosine normalization, including all-zero vectors.
        float scale = 1.0 / (std::sqrt(norm) + 1e-30);
        for (int i = 0; i < dim; i++) values[start + i] *= scale;
    }
    return values;
}
}
#define JNI_METHOD(name) Java_de_face_1manager_app_HnswIndex_##name
extern "C" JNIEXPORT jlong JNICALL JNI_METHOD(create)(JNIEnv* env, jclass, jint dim, jint maximum, jint ef, jint m, jlong seed) {
    try {
        if (dim <= 0 || maximum <= 0 || ef <= 0 || m < 2 || m > 10000 || seed < 0)
            throw std::invalid_argument("Invalid HNSW dimensions, capacity, construction parameters or seed");
        return reinterpret_cast<jlong>(new Index(dim, maximum, ef, m, seed));
    } catch (const std::exception& e) { fail(env, e); return 0; }
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(destroy)(JNIEnv*, jclass, jlong handle) { delete reinterpret_cast<Index*>(handle); }
extern "C" JNIEXPORT void JNICALL JNI_METHOD(setEfNative)(JNIEnv* env, jclass, jlong handle, jint ef) {
    try {
        if (ef <= 0) throw std::invalid_argument("ef must be positive");
        ptr(handle)->graph.setEf(ef);
    } catch (const std::exception& e) { fail(env, e); }
}
extern "C" JNIEXPORT jint JNICALL JNI_METHOD(sizeNative)(JNIEnv* env, jclass, jlong handle) {
    try { return ptr(handle)->graph.getCurrentElementCount(); }
    catch (const std::exception& e) { fail(env, e); return 0; }
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(addNative)(JNIEnv* env, jclass, jlong handle, jfloatArray data, jlongArray ids) {
    try {
        Index* index = ptr(handle);
        auto values = vectors(env, data, index->dim);
        size_t count = values.size() / index->dim;
        if (!ids || static_cast<size_t>(env->GetArrayLength(ids)) != count)
            throw std::invalid_argument("Labels must match embedding rows");
        std::vector<jlong> labels(count);
        env->GetLongArrayRegion(ids, 0, count, labels.data());
        if (env->ExceptionCheck()) return;
        for (jlong label : labels) if (label < 0) throw std::invalid_argument("Labels must be nonnegative");
        for (size_t row = 0; row < count; row++) {
            bool existing = index->graph.label_lookup_.count(labels[row]) != 0;
            size_t size = index->graph.getCurrentElementCount();
            if (!existing && size == index->maximum) throw std::runtime_error("HNSW index capacity exceeded");
            if (!existing && size == index->graph.getMaxElements())
                index->graph.resizeIndex(std::min(index->maximum, std::max(size + 1, size * 2)));
            index->graph.addPoint(values.data() + row * index->dim, static_cast<hnswlib::labeltype>(labels[row]));
        }
    } catch (const std::exception& e) { fail(env, e); }
}
extern "C" JNIEXPORT jobject JNICALL JNI_METHOD(queryNative)(JNIEnv* env, jclass, jlong handle, jfloatArray data, jint k) {
    try {
        Index* index = ptr(handle);
        if (k <= 0 || static_cast<size_t>(k) > index->graph.getCurrentElementCount())
            throw std::invalid_argument("k must be positive and cannot exceed the number of indexed items");
        auto values = vectors(env, data, index->dim);
        size_t rows = values.size() / index->dim;
        if (rows > static_cast<size_t>(std::numeric_limits<jsize>::max()) / k)
            throw std::invalid_argument("Query result exceeds Java array limit");
        std::vector<jlong> labels(rows * k);
        std::vector<float> distances(rows * k);
        for (size_t row = 0; row < rows; row++) {
            auto result = index->graph.searchKnn(values.data() + row * index->dim, k);
            if (result.size() != static_cast<size_t>(k)) throw std::runtime_error("HNSW returned too few neighbors; increase ef");
            for (int j = k - 1; j >= 0; j--) {
                distances[row * k + j] = result.top().first;
                labels[row * k + j] = result.top().second;
                result.pop();
            }
        }
        jlongArray jl = env->NewLongArray(labels.size());
        jfloatArray jd = env->NewFloatArray(distances.size());
        if (!jl || !jd) return nullptr;
        env->SetLongArrayRegion(jl, 0, labels.size(), labels.data());
        env->SetFloatArrayRegion(jd, 0, distances.size(), distances.data());
        jclass cls = env->FindClass("de/face_manager/app/HnswIndex$Result");
        return env->NewObject(cls, env->GetMethodID(cls, "<init>", "([J[F)V"), jl, jd);
    } catch (const std::exception& e) { if (!env->ExceptionCheck()) fail(env, e); return nullptr; }
}
