#pragma once

#include "graph_node.hpp"
#include "distance.hpp"
#include "../config/constants.hpp"
#include "../storage/memory_map.hpp"
#include "../concurrency/spinlock.hpp"
#include "../storage/metadata_store.hpp"
#include <queue>
#include <vector>
#include <random>
#include <cmath>
#include <algorithm>
#include <omp.h>
#include <mutex>
#include <memory>
#include <atomic>

namespace nanodb {

    struct FileHeader {
        uint32_t magic;
        uint32_t element_count;
        int32_t entry_point_id;
        int32_t max_layer;
        char reserved[48]; // pad to 64 bytes
    };

    static constexpr uint32_t NANODB_MAGIC = 0x4E444200; // "NDB\0"
    static constexpr size_t HEADER_SIZE = sizeof(FileHeader);

    // Internal search candidate: 8 bytes, trivially copyable.
    //
    // The search used to carry `Result` through both priority queues, and
    // Result holds a std::string for metadata -- 40 bytes with a constructor
    // and destructor. The string is always empty during traversal, yet every
    // sift-up and sift-down still move-constructed and destroyed one, and a
    // heap operation does O(log n) of those. Metadata is now attached only to
    // the k results that survive.
    struct Cand {
        float distance;
        id_t id;
        // Ordering is by distance only, so the default priority_queue
        // comparator gives a max-heap (top() == farthest), which is what the
        // bounded ef-nearest set needs.
        bool operator<(const Cand& o) const { return distance < o.distance; }
        bool operator>(const Cand& o) const { return distance > o.distance; }
    };

    class HNSW {
    public:
        // -----------------------------------------------------------------------
        // Constructor
        // metric: distance function to use for all comparisons in this index.
        //         Must be consistent across insert and search calls.
        // -----------------------------------------------------------------------
        HNSW(MMapHandler& storage,
             const std::string& meta_path = "data/metadata.bin",
             DistanceMetric metric = DistanceMetric::L2)
            : storage_(storage), metric_(metric)
        {
            metadata_storage_.open_file(meta_path);

            FileHeader* header = get_header();
            if (header->magic != NANODB_MAGIC) {
                // Fresh file — initialize header
                header->magic = NANODB_MAGIC;
                header->element_count = 0;
                header->entry_point_id = -1;
                header->max_layer = -1;
                entry_point_id_ = NO_ENTRY;
                current_max_layer_ = -1;
                element_count_ = 0;
            } else {
                // Existing index — restore state from header
                entry_point_id_ = header->entry_point_id;
                current_max_layer_ = header->max_layer;
                element_count_ = header->element_count;
            }

            // Fixed stripe pool -- see LOCK_STRIPES.
            node_locks_.reserve(LOCK_STRIPES);
            for (size_t i = 0; i < LOCK_STRIPES; ++i) {
                node_locks_.push_back(std::make_unique<SpinLock>());
            }
        }

        // -----------------------------------------------------------------------
        // Insert a vector with an integer ID and optional metadata string.
        // Thread-safe: multiple threads may call insert() concurrently.
        // -----------------------------------------------------------------------
        void insert(const std::vector<float>& vec_data, id_t id, const std::string& metadata = "") {
            int level = get_random_level();
            Node new_node(id, level, vec_data);

            // Expand storage if needed (double-checked locking).
            //
            // The lock array used to be grown here too, nested inside this
            // branch. Storage growth and lock coverage are independent, and
            // tying them together meant that whenever the file was
            // pre-allocated large enough to avoid a resize -- the shard node
            // opens 100 MB up front, room for ~94k nodes -- node_locks_ never
            // grew past its initial 10,000. add_link() then silently returned
            // for every src >= node_locks_.size(), so EVERY node past id
            // 10,000 was inserted with zero neighbours and was unreachable.
            //
            // Measured: recall@10 held at 0.85 through 10k vectors and fell
            // off a cliff to 0.21 at 20k. node_locks_ is a fixed stripe pool
            // now, so there is nothing left to fall behind.
            size_t offset = HEADER_SIZE + (size_t)id * sizeof(Node);
            if (offset + sizeof(Node) > storage_.get_size()) {
                std::lock_guard<std::mutex> lock(global_resize_lock_);
                if (offset + sizeof(Node) > storage_.get_size()) {
                    storage_.resize(grown_size(storage_.get_size(),
                                               offset + sizeof(Node)));
                }
            }

            Node* node_ptr = get_node(id);
            *node_ptr = new_node;

            // Handle first element
            if (entry_point_id_ == NO_ENTRY) {
                std::lock_guard<std::mutex> lock(init_lock_);
                if (entry_point_id_ == NO_ENTRY) {
                    entry_point_id_ = id;
                    current_max_layer_ = level;
                    #pragma omp atomic
                    element_count_++;
                    persist_header();
                    if (!metadata.empty()) metadata_storage_.save_metadata(id, metadata);
                    return;
                }
            }

            // Greedy search from entry point down to node's level
            id_t curr_obj = entry_point_id_;
            float dist = compute_distance(node_ptr->vector, get_node(curr_obj)->vector,
                                          config::VECTOR_DIM, metric_);

            for (int l = current_max_layer_; l > level; l--) {
                bool changed = true;
                while (changed) {
                    changed = false;
                    Node* curr_node = get_node(curr_obj);
                    for (int i = 0; i < curr_node->neighbor_counts[l]; i++) {
                        id_t n_id = curr_node->neighbors[l][i];
                        float d = compute_distance(node_ptr->vector, get_node(n_id)->vector,
                                                   config::VECTOR_DIM, metric_);
                        if (d < dist) { dist = d; curr_obj = n_id; changed = true; }
                    }
                }
            }

            // Connect neighbors at each layer
            for (int l = std::min(level, current_max_layer_.load()); l >= 0; l--) {
                std::priority_queue<Cand> candidates =
                    search_layer(curr_obj, node_ptr->vector, config::EF_CONSTRUCTION, l);

                // search_layer returns a bounded MAX-heap of the ef nearest
                // candidates, so top() is the FARTHEST of them. Draining it
                // directly therefore selected the M *farthest* of the
                // ef_construction candidates -- roughly ranks 185-200 out of
                // 200 -- and wired every new node to the worst neighbours
                // available. That is the exact inverse of what HNSW requires,
                // and it degrades with scale: at small N the graph is dense
                // enough (M_MAX0 = 32) to mask it, which is why the recall
                // benchmark reported ~95% while a 21k-vector shard measured
                // ~46%.
                //
                // Drain fully, then reverse to nearest-first -- the same
                // ordering fix search() already applies before truncating to
                // k -- and take the M nearest.
                std::vector<Cand> ordered;
                ordered.reserve(candidates.size());
                while (!candidates.empty()) {
                    ordered.push_back(candidates.top());
                    candidates.pop();
                }
                std::reverse(ordered.begin(), ordered.end());

                std::vector<id_t> selected_neighbors =
                    select_neighbors_heuristic(ordered, (size_t)config::M, id);

                for (id_t neighbor_id : selected_neighbors) {
                    add_link(id, neighbor_id, l);
                    add_link(neighbor_id, id, l);
                }

                if (!selected_neighbors.empty()) curr_obj = selected_neighbors[0];
            }

            // Double-checked under init_lock_: two threads inserting
            // high-level nodes concurrently could both pass a bare check and
            // race, leaving entry_point_id_ and current_max_layer_ describing
            // different nodes -- an entry point claiming a layer it has no
            // links on, which strands every subsequent descent.
            if (level > current_max_layer_) {
                std::lock_guard<std::mutex> lock(init_lock_);
                if (level > current_max_layer_) {
                    entry_point_id_ = id;
                    current_max_layer_ = level;
                }
            }

            #pragma omp atomic
            element_count_++;

            persist_header();

            if (!metadata.empty()) {
                metadata_storage_.save_metadata(id, metadata);
            }
        }

        // -----------------------------------------------------------------------
        // Search for the k nearest neighbors of a query vector.
        // Deleted (tombstoned) nodes are silently skipped.
        // -----------------------------------------------------------------------
        // ef controls the search beam width: higher costs latency and buys
        // recall. Passing 0 (the default) keeps the historical behaviour of
        // max(100, k).
        //
        // This was hardcoded, which made the recall/latency tradeoff
        // unreachable from outside. Measured on 20k uniform random 128-d
        // vectors, recall@10 goes 0.726 at ef=100, 0.876 at ef=200, and
        // 0.949 at ef=400 -- the knob works, it just wasn't exposed. Standard
        // ANN benchmarks sweep it, and the Big-ANN streaming harness requires
        // it as a query argument.
        std::vector<Result> search(const std::vector<float>& query, int k,
                                    int ef = 0) {
            if (entry_point_id_ == NO_ENTRY) return {};

            id_t curr_obj = entry_point_id_;
            float dist = compute_distance(query.data(), get_node(curr_obj)->vector,
                                          config::VECTOR_DIM, metric_);

            for (int l = current_max_layer_; l > 0; l--) {
                bool changed = true;
                while (changed) {
                    changed = false;
                    Node* curr_node = get_node(curr_obj);
                    for (int i = 0; i < curr_node->neighbor_counts[l]; i++) {
                        id_t n_id = curr_node->neighbors[l][i];
                        float d = compute_distance(query.data(), get_node(n_id)->vector,
                                                   config::VECTOR_DIM, metric_);
                        if (d < dist) { dist = d; curr_obj = n_id; changed = true; }
                    }
                }
            }

            int ef_search = (ef > 0) ? std::max(ef, k) : std::max(100, k);
            std::priority_queue<Cand> top_candidates =
                search_layer(curr_obj, query.data(), ef_search, 0);

            // Drain farthest-first, then reverse to nearest-first.
            std::vector<Cand> ordered;
            ordered.reserve(top_candidates.size());
            while (!top_candidates.empty()) {
                ordered.push_back(top_candidates.top());
                top_candidates.pop();
            }
            std::reverse(ordered.begin(), ordered.end());

            // Metadata is fetched only for the k results actually returned.
            //
            // This loop used to run over all ef candidates -- 100 by default
            // -- doing a seek and read against the metadata file for each,
            // under MetadataHandler's single mutex, and then truncate to
            // k=10. Ninety percent of that I/O was discarded, and because the
            // lock is global it serialised concurrent searches while doing
            // it. Truncating first turns 100 locked reads per query into 10.
            std::vector<Result> results;
            results.reserve(std::min((size_t)k, ordered.size()));
            for (const Cand& c : ordered) {
                if (results.size() >= (size_t)k) break;
                if (get_node(c.id)->is_deleted) continue;   // skip tombstones
                Result r;
                r.id = c.id;
                r.distance = c.distance;
                r.metadata = metadata_storage_.get_metadata(c.id);
                results.push_back(std::move(r));
            }
            return results;
        }

        // -----------------------------------------------------------------------
        // Lazy deletion (tombstoning).
        //
        // Design tradeoff: True graph repair on deletion would require re-linking
        // all neighbors of the deleted node — O(M * ef_construction) work per
        // deletion, and it must be done under locks, serializing concurrent inserts.
        // Most production systems (including FAISS IVF) use lazy deletion instead:
        //   - O(1) deletion cost
        //   - Deleted nodes remain in the graph structure but are filtered at query time
        //   - Recall degrades slightly as the fraction of deleted nodes grows
        //   - Periodic "compaction" (rebuild) is the standard remedy
        // -----------------------------------------------------------------------
        void delete_vector(id_t id) {
            size_t offset = HEADER_SIZE + (size_t)id * sizeof(Node);
            if (offset + sizeof(Node) > storage_.get_size()) return;
            Node* node = get_node(id);
            if (!node->is_deleted) {
                node->is_deleted = true;
                #pragma omp atomic
                element_count_--;
                persist_header();
            }
        }

        // Helper: retrieve metadata string for a given ID
        std::string get_metadata(id_t id) {
            return metadata_storage_.get_metadata(id);
        }

        // Helper: check if a node has been deleted
        bool is_deleted(id_t id) {
            size_t offset = HEADER_SIZE + (size_t)id * sizeof(Node);
            if (offset + sizeof(Node) > storage_.get_size()) return false;
            return get_node(id)->is_deleted;
        }

        // Helper: copy of the raw vector for a given id. Used by the Phase 2
        // migration path to move a vector's data to another shard. Returns
        // an empty vector if id is out of range or tombstoned.
        std::vector<float> get_vector_data(id_t id) {
            size_t offset = HEADER_SIZE + (size_t)id * sizeof(Node);
            if (offset + sizeof(Node) > storage_.get_size()) return {};
            Node* node = get_node(id);
            if (node->is_deleted) return {};
            return std::vector<float>(node->vector, node->vector + config::VECTOR_DIM);
        }

        // Helper: current number of live (non-deleted) elements
        size_t size() const { return element_count_; }

        // Helper: the distance metric this index was built with
        DistanceMetric metric() const { return metric_; }

    private:
        MMapHandler& storage_;
        MetadataHandler metadata_storage_;
        DistanceMetric metric_;
        // Sentinel for "no entry point yet". id_t is unsigned, so spell it
        // once here rather than comparing against a bare -1 at each use.
        static constexpr id_t NO_ENTRY = (id_t)-1;

        // Atomic because search() and insert() read these on every call
        // without holding init_lock_; only the update path takes the lock.
        std::atomic<id_t> entry_point_id_{NO_ENTRY};
        std::atomic<int> current_max_layer_{-1};
        size_t element_count_ = 0;
        std::mutex init_lock_;

        // Striped lock pool, sized once at construction. The previous scheme
        // kept one lock per node and grew the vector as ids advanced, which
        // had two defects: it could silently fall behind (see insert()), and
        // growing a vector while other threads index into it is a
        // reallocation race. A fixed pool cannot do either. Stripe collisions
        // just serialise two unrelated nodes briefly; add_link holds one lock
        // at a time and never nests, so sharing cannot deadlock.
        static constexpr size_t LOCK_STRIPES = 4096;
        std::vector<std::unique_ptr<SpinLock>> node_locks_;

        SpinLock& lock_for(id_t id) { return *node_locks_[id % LOCK_STRIPES]; }
        std::mutex global_resize_lock_;

        FileHeader* get_header() {
            return reinterpret_cast<FileHeader*>(storage_.get_data());
        }

        void persist_header() {
            FileHeader* h = get_header();
            h->element_count = (uint32_t)element_count_;
            h->entry_point_id = (int32_t)entry_point_id_;
            h->max_layer = (int32_t)current_max_layer_;
        }

        // Next file size that covers `needed`, growing geometrically.
        //
        // Two problems with the previous `get_size() + 10MB`:
        //
        // 1. Fixed-increment growth makes building an N-byte index O(N^2) in
        //    copying -- N/10MB resizes, each moving progressively more data.
        //    Doubling makes it O(N) amortised. Capped at MAX_GROWTH_STEP so a
        //    large index cannot double into wildly over-allocated space; past
        //    that point it grows linearly in big strides, which is O(N) too.
        //
        // 2. It added a single 10MB increment regardless of how far past the
        //    end `needed` was. A sparse or out-of-order id could land beyond
        //    the new size, and the caller would then write through get_node()
        //    past the mapping. Dense sequential ids never triggered it, so it
        //    stayed latent. Looping until the size actually covers `needed`
        //    removes the assumption.
        static size_t grown_size(size_t current, size_t needed) {
            static constexpr size_t MIN_GROWTH_STEP = 10u * 1024 * 1024;
            static constexpr size_t MAX_GROWTH_STEP = 256u * 1024 * 1024;
            size_t next = current;
            while (next < needed) {
                size_t step = next;                      // double...
                if (step > MAX_GROWTH_STEP) step = MAX_GROWTH_STEP;
                if (step < MIN_GROWTH_STEP) step = MIN_GROWTH_STEP;
                next += step;
            }
            return next;
        }

        Node* get_node(id_t id) {
            return reinterpret_cast<Node*>((char*)storage_.get_data() + HEADER_SIZE + (size_t)id * sizeof(Node));
        }

        int get_random_level() {
            // std::mt19937 is not thread-safe, and insert() is called
            // concurrently -- a shared engine here was a data race on every
            // parallel insert. Per-thread engines remove it. Measured impact
            // was real: at matched index size, 4 concurrent writers reached
            // materially lower recall than a single writer.
            static thread_local std::mt19937 tls_rng{std::random_device{}()};
            std::uniform_real_distribution<double> dist(0.0, 1.0);
            double r = dist(tls_rng);
            int level = 0;
            // Node::neighbors is [MAX_LAYERS][M_MAX0], so the highest legal
            // layer index is MAX_LAYERS - 1. The bound here used to be
            // config::M (16), which let this return a level of up to 16 and
            // let the connect loop write neighbors[l] for l >= 4 -- straight
            // past the array and into neighbor_counts.
            //
            // P(level >= 4) = 0.03^4 = 8.1e-7, so at ~21k inserts this fires
            // roughly 1.7% of runs and at 30M inserts about 24 times. Rare
            // enough never to have shown up at the scales tested so far, and
            // silent memory corruption when it does.
            while (r < 0.03 && level < MAX_LAYERS - 1) {
                level++;
                r = dist(tls_rng);
            }
            return level;
        }

        // Marks a node id as seen for the current traversal.
        //
        // The old form allocated a std::vector<bool> sized to the whole index
        // and zeroed it on every call -- ~95k entries at a 100 MB file,
        // several times per query, then thrown away. Here one thread-local
        // array of epoch stamps is reused: a node counts as visited when its
        // stamp equals the current epoch, so "clearing" the set is a single
        // increment. O(1) per traversal instead of O(index size).
        //
        // Thread-local rather than pooled because search_layer runs
        // concurrently and the cost is small: 4 bytes per addressable node
        // per searching thread.
        //
        // Sizing still comes from storage capacity, not element_count_.
        // element_count_ is the *live* count and delete_vector() decrements
        // it, so after deletions it under-covers the id space still present
        // in the graph, and the bounds check below then silently skips valid
        // neighbours -- bleeding recall in proportion to the delete count,
        // which is exactly the workload this index gets measured on.
        struct VisitedSet {
            std::vector<uint32_t> stamp;
            uint32_t epoch = 0;

            void begin(size_t capacity) {
                if (stamp.size() < capacity) {
                    stamp.assign(capacity, 0);
                    epoch = 0;
                }
                if (++epoch == 0) {                 // wrapped after 2^32 traversals
                    std::fill(stamp.begin(), stamp.end(), 0u);
                    epoch = 1;
                }
            }
            bool test_and_set(id_t id) {
                if (id >= stamp.size()) return true;   // out of range: treat as seen
                if (stamp[id] == epoch) return true;
                stamp[id] = epoch;
                return false;
            }
        };

        // Core beam-search within a single HNSW layer.
        // Tombstoned nodes are skipped during neighbor expansion.
        std::priority_queue<Cand> search_layer(id_t entry_point, const float* query_vec,
                                                int ef, int layer) {
            static thread_local VisitedSet visited;
            visited.begin((storage_.get_size() - HEADER_SIZE) / sizeof(Node));

            std::priority_queue<Cand, std::vector<Cand>, std::greater<Cand>> candidates;
            std::priority_queue<Cand> found_results;

            float d = compute_distance(query_vec, get_node(entry_point)->vector,
                                       config::VECTOR_DIM, metric_);
            Cand start_node{d, entry_point};
            candidates.push(start_node);
            // Only add to found_results if not deleted
            if (!get_node(entry_point)->is_deleted) found_results.push(start_node);
            visited.test_and_set(entry_point);

            while (!candidates.empty()) {
                Cand curr = candidates.top();
                candidates.pop();

                if (!found_results.empty() &&
                    curr.distance > found_results.top().distance &&
                    found_results.size() >= (size_t)ef) break;

                Node* curr_node = get_node(curr.id);
                for (int i = 0; i < curr_node->neighbor_counts[layer]; i++) {
                    id_t neighbor_id = curr_node->neighbors[layer][i];
                    if (visited.test_and_set(neighbor_id)) continue;

                    float dist = compute_distance(query_vec, get_node(neighbor_id)->vector,
                                                  config::VECTOR_DIM, metric_);
                    if (found_results.size() < (size_t)ef || dist < found_results.top().distance) {
                        candidates.push({dist, neighbor_id});
                        // Only add live nodes to results
                        if (!get_node(neighbor_id)->is_deleted) {
                            found_results.push({dist, neighbor_id});
                            if (found_results.size() > (size_t)ef) found_results.pop();
                        }
                    }
                }
            }
            return found_results;
        }

        // ---------------------------------------------------------------
        // HNSW Algorithm 4 -- SELECT-NEIGHBORS-HEURISTIC.
        //
        // Keeping simply the M *closest* candidates looks reasonable and is
        // catastrophically wrong at scale: every long-range link gets evicted
        // as nearer nodes arrive, neighbourhoods become locally clustered,
        // and greedy search can no longer traverse the graph. Measured here,
        // recall fell from 0.94 at 3k vectors to 0.61 at 12.5k on a
        // single-writer, fault-free cluster with completeness at 1.0 -- a
        // collapse purely in N.
        //
        // The heuristic keeps a candidate only if it is closer to the base
        // element than to any already-selected neighbour. That admits a
        // distant candidate lying in a direction not yet covered, while
        // rejecting a near one that merely duplicates an existing link, so
        // long-range connectivity survives. This is what hnswlib's
        // getNeighborsByHeuristic2 implements.
        //
        // `ordered` must be sorted nearest-first and each Result::distance
        // must be the distance to the base element being linked.
        std::vector<id_t> select_neighbors_heuristic(const std::vector<Cand>& ordered,
                                                      size_t max_keep,
                                                      id_t exclude_id) {
            std::vector<id_t> selected;
            selected.reserve(max_keep);

            // With no more candidates than slots there is nothing to choose
            // between: pruning here would only under-connect a young graph.
            const bool keep_all = ordered.size() <= max_keep;

            for (const Cand& cand : ordered) {
                if (selected.size() >= max_keep) break;
                if (cand.id == exclude_id) continue;

                if (!keep_all) {
                    const float* cand_vec = get_node(cand.id)->vector;
                    bool keep = true;
                    for (id_t chosen : selected) {
                        float d_to_chosen = compute_distance(
                            cand_vec, get_node(chosen)->vector,
                            config::VECTOR_DIM, metric_);
                        if (d_to_chosen < cand.distance) { keep = false; break; }
                    }
                    if (!keep) continue;
                }
                selected.push_back(cand.id);
            }
            return selected;
        }

        void add_link(id_t src, id_t dest, int layer) {
            SpinLock& guard = lock_for(src);
            guard.lock();

            Node* node = get_node(src);
            int count = node->neighbor_counts[layer];
            int max_conn = (layer == 0) ? config::M_MAX0 : config::M;

            if (count < max_conn) {
                node->neighbors[layer][count] = dest;
                node->neighbor_counts[layer]++;
            } else {
                // Full. Re-select from {existing neighbours} u {dest} using
                // the same diversity heuristic, rather than evicting whichever
                // neighbour happens to be farthest.
                //
                // Eviction-by-distance is the more damaging half of the
                // closest-M bug: it strips long-range links out of an
                // *established* node's list every time a nearer node appears
                // beside it. Over an insert stream that steadily removes the
                // graph's shortcuts, which is why recall decayed as a function
                // of N rather than staying flat.
                bool already_linked = false;
                for (int i = 0; i < count; ++i) {
                    if (node->neighbors[layer][i] == dest) { already_linked = true; break; }
                }

                if (!already_linked) {
                    std::vector<Cand> cands;
                    cands.reserve((size_t)count + 1);
                    for (int i = 0; i < count; ++i) {
                        id_t n = node->neighbors[layer][i];
                        cands.push_back({compute_distance(node->vector, get_node(n)->vector,
                                                          config::VECTOR_DIM, metric_), n});
                    }
                    cands.push_back({compute_distance(node->vector, get_node(dest)->vector,
                                                      config::VECTOR_DIM, metric_), dest});
                    std::sort(cands.begin(), cands.end(),
                              [](const Cand& a, const Cand& b) {
                                  return a.distance < b.distance;
                              });

                    // May return fewer than max_conn: the heuristic drops
                    // redundant links, freeing slots for diverse ones later.
                    std::vector<id_t> kept =
                        select_neighbors_heuristic(cands, (size_t)max_conn, src);
                    for (size_t i = 0; i < kept.size(); ++i) {
                        node->neighbors[layer][i] = kept[i];
                    }
                    node->neighbor_counts[layer] = (int)kept.size();
                }
            }

            guard.unlock();
        }
    };

} // namespace nanodb