# Vendored HNSW

The `hnswlib` headers are unmodified upstream v0.8.0 from
https://github.com/nmslib/hnswlib/tree/v0.8.0/hnswlib.
They are distributed under Apache License 2.0; see `HNSWLIB-LICENSE`.

`../hnsw_jni.cpp` provides the Android cosine index bridge. Vectors are normalized
before insertion and query, as in upstream Python hnswlib. Java serializes calls
and closes native indexes. The index grows its allocation on demand to the
requested maximum so an empty library does not reserve hundreds of megabytes.
