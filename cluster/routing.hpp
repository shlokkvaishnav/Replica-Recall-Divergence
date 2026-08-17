#pragma once
#include <string>
#include <cstdint>

namespace nanodb {
namespace cluster {

// FNV-1a 64-bit. Fixed and deterministic across processes, compilers, and
// runs. std::hash<std::string> is explicitly NOT used here — it's only
// guaranteed consistent within a single process, and every coordinator
// instance must route the same external_id to the same shard.
inline uint64_t fnv1a_64(const std::string& s) {
    uint64_t h = 14695981039346656037ULL;
    for (unsigned char c : s) {
        h ^= c;
        h *= 1099511628211ULL;
    }
    return h;
}

inline uint64_t well_mixed_hash(const std::string& external_id) {
    uint64_t h = fnv1a_64(external_id);
    return fnv1a_64(std::to_string(h));
}

} // namespace cluster
} // namespace nanodb
