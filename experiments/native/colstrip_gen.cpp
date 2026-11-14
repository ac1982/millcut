// C++ Column + Strip Pricing & Generation Engine for 3-Stage NonExact Guillotine Cutting (v2).
// Key capabilities:
//   - Arbitrary sheet width W > 0 (exact 1D DP for W <= 4096; ceiling-scaled DP + exact
//     height-band-preserving residual completion for W > 4096).
//   - Arbitrary item demand copies >= 1.
//   - Exact unperturbed width subset-sum sampling over shuffled height-band pools (preserving
//     exact sum(w) == W shelf witness discovery at C++ speed).
//   - Controlled height-matched 2-item and 3-item vertical column stacks (limiting stacks per
//     DP pass so stacked columns bridge height gaps without fragmenting master exact cover).
//   - HiGHS LP shadow-price dual-guided pricing.

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <numeric>
#include <random>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

constexpr int DP_MAX_CAP = 4096;
constexpr int MAX_DP_ITEMS = 320;

struct CandCol {
    int w;
    int h;
    long long area;
    int n_items;
    int items[3];
    double eff;
};

struct StripRec {
    int height;
    long long area;
    long long waste;
    int max_ub;
    std::vector<std::pair<int, int>> item_counts; // sorted (item_type, mult)
    std::vector<int> col_ids;                     // indices into Pool::cols
};

static inline uint64_t splitmix64(uint64_t x) {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

static uint64_t hash_item_counts(const std::vector<std::pair<int, int>>& ic) {
    uint64_t h = 0x243f6a8885a308d3ULL ^ (uint64_t)ic.size();
    for (const auto& p : ic) {
        uint64_t v = ((uint64_t)(uint32_t)p.first << 32) | (uint32_t)p.second;
        h = splitmix64(h ^ splitmix64(v));
    }
    return h;
}

struct Pool {
    int M;
    int W;
    int H;
    long long waste_budget;
    int allow_stacks;
    int dp_cap;

    std::vector<int> iw;
    std::vector<int> ih;
    std::vector<int> icopies;

    std::vector<CandCol> cols;
    std::vector<int> singleton_ids;
    std::vector<int> stack_ids;
    std::vector<int> singleton_heights;
    std::vector<int> anchor_heights;

    std::vector<StripRec> strips;
    std::unordered_map<uint64_t, std::vector<int>> multiset_map;
    std::vector<int> item_freq;
    long long total_nnz = 0;

    std::vector<long long> dp_val;
    std::vector<uint8_t> dp_take;
    std::vector<int> scratch_counts;
    std::vector<int> scratch_avail;

    Pool(int m, const int* w, const int* h, const int* copies,
         int sheet_w, int sheet_h, long long w_budget, int stacks)
        : M(m), W(sheet_w), H(sheet_h), waste_budget(w_budget), allow_stacks(stacks),
          iw(w, w + m), ih(h, h + m), icopies(copies, copies + m),
          item_freq(m, 0), scratch_counts(m, 0), scratch_avail(m, 0) {
        dp_cap = (W <= DP_MAX_CAP) ? W : DP_MAX_CAP;
        dp_val.resize(dp_cap + 1, 0);
        dp_take.resize((size_t)MAX_DP_ITEMS * (size_t)(dp_cap + 1), 0);
        build_columns();
    }

    int scale_w(int w_actual) const {
        if (W <= DP_MAX_CAP) return w_actual;
        long long num = (long long)w_actual * DP_MAX_CAP + W - 1;
        int sw = (int)(num / W);
        return std::max(1, sw);
    }

    void build_columns() {
        cols.clear();
        singleton_ids.clear();
        stack_ids.clear();
        singleton_heights.clear();

        for (int t = 0; t < M; ++t) {
            if (iw[t] <= 0 || iw[t] > W || ih[t] <= 0 || ih[t] > H || icopies[t] <= 0) continue;
            CandCol c;
            c.w = iw[t];
            c.h = ih[t];
            c.area = (long long)iw[t] * ih[t];
            c.n_items = 1;
            c.items[0] = t;
            c.items[1] = -1;
            c.items[2] = -1;
            c.eff = 1.0;
            singleton_ids.push_back((int)cols.size());
            singleton_heights.push_back(c.h);
            cols.push_back(c);
        }
        std::sort(singleton_heights.begin(), singleton_heights.end());
        singleton_heights.erase(std::unique(singleton_heights.begin(), singleton_heights.end()), singleton_heights.end());

        if (allow_stacks) {
            std::vector<CandCol> stacks2;
            for (int i = 0; i < M; ++i) {
                if (iw[i] > W || ih[i] > H || icopies[i] <= 0) continue;
                for (int j = i; j < M; ++j) {
                    if (i == j && icopies[i] < 2) continue;
                    if (iw[j] > W || ih[j] > H || icopies[j] <= 0) continue;
                    int hc = ih[i] + ih[j];
                    if (hc > H) continue;
                    int wc = std::max(iw[i], iw[j]);
                    long long ac = (long long)iw[i] * ih[i] + (long long)iw[j] * ih[j];
                    long long box = (long long)wc * hc;
                    if (ac * 100 >= box * 92) {
                        CandCol c;
                        c.w = wc;
                        c.h = hc;
                        c.area = ac;
                        c.n_items = 2;
                        c.items[0] = i;
                        c.items[1] = j;
                        c.items[2] = -1;
                        c.eff = (double)ac / (double)box;
                        stacks2.push_back(c);
                    }
                }
            }
            std::sort(stacks2.begin(), stacks2.end(), [](const CandCol& a, const CandCol& b) {
                if (std::abs(a.eff - b.eff) > 1e-9) return a.eff > b.eff;
                return a.area > b.area;
            });
            if (stacks2.size() > 900) stacks2.resize(900);

            std::vector<CandCol> stacks3;
            int lim2 = std::min((int)stacks2.size(), 180);
            if (M <= 160) {
                for (int s = 0; s < lim2; ++s) {
                    const CandCol& base = stacks2[s];
                    int i = base.items[0], j = base.items[1];
                    for (int k = j; k < M; ++k) {
                        int need_k = 1 + (k == i ? 1 : 0) + (k == j ? 1 : 0);
                        if (icopies[k] < need_k) continue;
                        int hc = base.h + ih[k];
                        if (hc > H) continue;
                        int wc = std::max(base.w, iw[k]);
                        long long ac = base.area + (long long)iw[k] * ih[k];
                        long long box = (long long)wc * hc;
                        if (ac * 100 >= box * 95) {
                            CandCol c;
                            c.w = wc;
                            c.h = hc;
                            c.area = ac;
                            c.n_items = 3;
                            c.items[0] = i;
                            c.items[1] = j;
                            c.items[2] = k;
                            c.eff = (double)ac / (double)box;
                            stacks3.push_back(c);
                        }
                    }
                }
                std::sort(stacks3.begin(), stacks3.end(), [](const CandCol& a, const CandCol& b) {
                    if (std::abs(a.eff - b.eff) > 1e-9) return a.eff > b.eff;
                    return a.area > b.area;
                });
                if (stacks3.size() > 300) stacks3.resize(300);
            }

            for (const auto& c : stacks2) {
                stack_ids.push_back((int)cols.size());
                cols.push_back(c);
            }
            for (const auto& c : stacks3) {
                stack_ids.push_back((int)cols.size());
                cols.push_back(c);
            }
        }

        anchor_heights.clear();
        for (const auto& c : cols) anchor_heights.push_back(c.h);
        std::sort(anchor_heights.begin(), anchor_heights.end());
        anchor_heights.erase(std::unique(anchor_heights.begin(), anchor_heights.end()), anchor_heights.end());
    }

    inline bool can_use_col(const CandCol& c, const std::vector<int>& avail) const {
        if (c.n_items == 1) return avail[c.items[0]] >= 1;
        if (c.n_items == 2) {
            if (c.items[0] == c.items[1]) return avail[c.items[0]] >= 2;
            return avail[c.items[0]] >= 1 && avail[c.items[1]] >= 1;
        }
        int i0 = c.items[0], i1 = c.items[1], i2 = c.items[2];
        if (i0 == i1 && i1 == i2) return avail[i0] >= 3;
        if (i0 == i1) return avail[i0] >= 2 && avail[i2] >= 1;
        if (i1 == i2) return avail[i1] >= 2 && avail[i0] >= 1;
        if (i0 == i2) return avail[i0] >= 2 && avail[i1] >= 1;
        return avail[i0] >= 1 && avail[i1] >= 1 && avail[i2] >= 1;
    }

    bool add_strip(const std::vector<int>& chosen_cols, bool relax_waste = false) {
        if (chosen_cols.empty()) return false;
        long long sum_w = 0;
        int max_h = 0;
        long long area = 0;
        std::vector<int> touched;
        touched.reserve(chosen_cols.size() * 2);

        bool ok = true;
        for (int cid : chosen_cols) {
            const CandCol& c = cols[cid];
            sum_w += c.w;
            if (c.h > max_h) max_h = c.h;
            area += c.area;
            for (int k = 0; k < c.n_items; ++k) {
                int t = c.items[k];
                if (scratch_counts[t] == 0) touched.push_back(t);
                scratch_counts[t] += 1;
                if (scratch_counts[t] > icopies[t]) ok = false;
            }
        }
        if (!ok || sum_w <= 0 || sum_w > W || max_h <= 0 || max_h > H) {
            for (int t : touched) scratch_counts[t] = 0;
            return false;
        }
        long long waste = (long long)W * max_h - area;
        long long max_allowed_waste = relax_waste ? ((long long)W * H) : waste_budget;
        if (waste > max_allowed_waste) {
            for (int t : touched) scratch_counts[t] = 0;
            return false;
        }

        std::sort(touched.begin(), touched.end());
        std::vector<std::pair<int, int>> ic;
        ic.reserve(touched.size());
        int ub = 1000000000;
        for (int t : touched) {
            int mult = scratch_counts[t];
            scratch_counts[t] = 0;
            ic.emplace_back(t, mult);
            int u = icopies[t] / mult;
            if (u < ub) ub = u;
        }
        if (ub <= 0) return false;

        uint64_t hval = hash_item_counts(ic);
        auto it = multiset_map.find(hval);
        if (it != multiset_map.end()) {
            for (int idx : it->second) {
                if (strips[idx].item_counts == ic) {
                    if (max_h < strips[idx].height) {
                        strips[idx].height = max_h;
                        strips[idx].area = area;
                        strips[idx].waste = waste;
                        strips[idx].col_ids = chosen_cols;
                        return true;
                    }
                    return false;
                }
            }
        }

        int new_idx = (int)strips.size();
        StripRec rec;
        rec.height = max_h;
        rec.area = area;
        rec.waste = waste;
        rec.max_ub = ub;
        rec.item_counts = std::move(ic);
        rec.col_ids = chosen_cols;
        for (const auto& p : rec.item_counts) item_freq[p.first] += 1;
        total_nnz += (long long)rec.item_counts.size();
        strips.push_back(std::move(rec));
        multiset_map[hval].push_back(new_idx);
        return true;
    }

    void greedy_complete(std::vector<int>& chosen, const std::vector<int>& order, int min_h_cap, int max_h_cap) {
        long long used_w = 0;
        std::copy(icopies.begin(), icopies.end(), scratch_avail.begin());
        for (int cid : chosen) {
            const CandCol& c = cols[cid];
            used_w += c.w;
            for (int k = 0; k < c.n_items; ++k) scratch_avail[c.items[k]] -= 1;
        }
        if (used_w >= W) return;

        for (int cid : order) {
            const CandCol& c = cols[cid];
            if (c.h < min_h_cap || c.h > max_h_cap || used_w + c.w > W) continue;
            while (used_w + c.w <= W && can_use_col(c, scratch_avail)) {
                chosen.push_back(cid);
                used_w += c.w;
                for (int k = 0; k < c.n_items; ++k) scratch_avail[c.items[k]] -= 1;
            }
            if (used_w == W) break;
        }
    }

    void run_dp_pass(int anchor_h, int min_h, const std::vector<int>& col_order,
                     const std::vector<long long>& col_scores, int max_stacks_admit = 4) {
        std::copy(icopies.begin(), icopies.end(), scratch_avail.begin());
        int dp_col_ids[MAX_DP_ITEMS];
        int dp_cw[MAX_DP_ITEMS];
        long long dp_cval[MAX_DP_ITEMS];
        int K = 0;
        int admitted_stacks = 0;

        for (int cid : col_order) {
            if (K >= MAX_DP_ITEMS) break;
            const CandCol& c = cols[cid];
            if (c.h > anchor_h || c.h < min_h) continue;
            if (c.n_items > 1) {
                if (admitted_stacks >= max_stacks_admit) continue;
                // Only admit stacks whose height closely matches the strip band [0.90*anchor_h, anchor_h]
                if (c.h * 100 < anchor_h * 90) continue;
            }
            long long sc = col_scores[cid];
            if (sc <= 0) continue;
            int sw = scale_w(c.w);
            if (sw > dp_cap) continue;

            int max_fit = W / c.w;
            int copies_here = 0;
            while (copies_here < max_fit && K < MAX_DP_ITEMS && can_use_col(c, scratch_avail)) {
                for (int x = 0; x < c.n_items; ++x) scratch_avail[c.items[x]] -= 1;
                dp_col_ids[K] = cid;
                dp_cw[K] = sw;
                dp_cval[K] = sc;
                ++K;
                ++copies_here;
                if (c.n_items > 1) {
                    ++admitted_stacks;
                    if (admitted_stacks >= max_stacks_admit) break;
                }
            }
        }

        if (K == 0) return;

        const int stride = dp_cap + 1;
        std::fill(dp_val.begin(), dp_val.end(), 0);
        for (int idx = 0; idx < K; ++idx) {
            uint8_t* row = &dp_take[(size_t)idx * (size_t)stride];
            std::memset(row, 0, (size_t)stride);
            int sw = dp_cw[idx];
            long long sv = dp_cval[idx];
            for (int w = dp_cap; w >= sw; --w) {
                long long cand = dp_val[w - sw] + sv;
                if (cand > dp_val[w]) {
                    dp_val[w] = cand;
                    row[w] = 1;
                }
            }
        }

        int best_w = 0;
        long long best_v = 0;
        for (int w = 1; w <= dp_cap; ++w) {
            if (dp_val[w] > best_v) {
                best_v = dp_val[w];
                best_w = w;
            }
        }
        if (best_v <= 0) return;

        std::vector<int> chosen;
        chosen.reserve(24);
        int cur_w = best_w;
        int max_chosen_h = 0;
        long long sum_actual_w = 0;
        for (int idx = K - 1; idx >= 0; --idx) {
            if (dp_take[(size_t)idx * (size_t)stride + cur_w]) {
                int cid = dp_col_ids[idx];
                chosen.push_back(cid);
                sum_actual_w += cols[cid].w;
                if (cols[cid].h > max_chosen_h) max_chosen_h = cols[cid].h;
                cur_w -= dp_cw[idx];
            }
        }
        if (chosen.empty()) return;

        // 1. Always record the exact pure DP strip first!
        add_strip(chosen, false);

        // 2. Only run residual completion when W > DP_MAX_CAP (ceiling scaling slack) or stacks left conflict slack
        if ((W > DP_MAX_CAP || admitted_stacks > 0) && sum_actual_w < W) {
            std::vector<int> comp = chosen;
            greedy_complete(comp, col_order, min_h, max_chosen_h);
            if (comp.size() > chosen.size()) {
                add_strip(comp, false);
            }
        }
    }

    void ensure_all_items_covered(std::mt19937_64& rng) {
        int num_cols = (int)cols.size();
        std::vector<int> order = singleton_ids;
        std::sort(order.begin(), order.end(), [&](int a, int b) {
            return cols[a].h > cols[b].h;
        });

        for (int t = 0; t < M; ++t) {
            if (icopies[t] <= 0 || iw[t] > W || ih[t] > H) continue;
            int self_cid = -1;
            for (int cid : singleton_ids) {
                if (cols[cid].items[0] == t) {
                    self_cid = cid;
                    break;
                }
            }
            if (self_cid < 0) continue;

            int attempts = 0;
            while (item_freq[t] < 1 && attempts < 8) {
                ++attempts;
                std::vector<int> chosen = {self_cid};
                int anchor_h = cols[self_cid].h;
                int min_h = (int)(anchor_h * 0.75);
                if (attempts % 2 == 0 && !singleton_heights.empty()) {
                    auto it = std::lower_bound(singleton_heights.begin(), singleton_heights.end(), anchor_h);
                    int idx = (int)(it - singleton_heights.begin());
                    int span = std::min((int)singleton_heights.size() - idx, 6);
                    if (span > 1) anchor_h = singleton_heights[idx + (rng() % span)];
                    min_h = (int)(anchor_h * 0.75);
                }
                std::vector<int> local_order = order;
                if (attempts > 2) std::shuffle(local_order.begin(), local_order.end(), rng);
                greedy_complete(chosen, local_order, min_h, anchor_h);
                if (!add_strip(chosen, false)) {
                    add_strip(chosen, true);
                }
            }
        }
    }

    int generate(const double* duals, double time_limit_s, unsigned int seed, int max_strips_cap) {
        auto t0 = std::chrono::steady_clock::now();
        auto elapsed = [&]() {
            return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        };

        std::mt19937_64 rng(seed);
        int num_cols = (int)cols.size();
        if (num_cols == 0 || anchor_heights.empty()) return (int)strips.size();

        std::vector<long long> width_scores(num_cols, 0);
        std::vector<long long> area_scores(num_cols, 0);
        for (int c = 0; c < num_cols; ++c) {
            width_scores[c] = cols[c].w;     // EXACT unperturbed width for subset-sum!
            area_scores[c] = cols[c].area;   // Exact area for max-density shelves
        }

        if (duals == nullptr) {
            // Round 1A: Exact deterministic sweeps across singleton & stack heights (area & width)
            std::vector<int> s_order = singleton_ids;
            std::sort(s_order.begin(), s_order.end(), [&](int a, int b) {
                return cols[a].h > cols[b].h;
            });

            const double det_rhos[4] = {0.70, 0.85, 0.95, 1.0};
            for (int h : singleton_heights) {
                if (elapsed() >= time_limit_s * 0.20 || (int)strips.size() >= max_strips_cap) break;
                for (double rho : det_rhos) {
                    int min_h = (int)std::ceil(h * rho - 1e-9);
                    run_dp_pass(h, min_h, s_order, width_scores, 0);
                    if (allow_stacks) {
                        run_dp_pass(h, min_h, s_order, area_scores, 0);
                    }
                }
            }

            // Round 1B: Randomized UNPERTURBED width subset-sum (mirroring subset_width at C++ speed!)
            // plus controlled height-matched 2/3-stack mixes when allow_stacks == 1.
            const double rand_rhos[4] = {0.70, 0.85, 0.95, 1.0};
            std::vector<int> shuf_singletons = singleton_ids;
            std::vector<int> mixed_order;
            mixed_order.reserve(num_cols);

            const std::vector<int>& h_pool = (!allow_stacks || singleton_heights.empty())
                ? singleton_heights : anchor_heights;
            int hn = (int)h_pool.size();

            while (elapsed() < time_limit_s * 0.96 && (int)strips.size() < max_strips_cap) {
                double rho = rand_rhos[rng() & 3];
                bool use_stacks = allow_stacks && !stack_ids.empty() && ((rng() % 3) != 0);

                if (!use_stacks) {
                    int h = singleton_heights[rng() % singleton_heights.size()];
                    int min_h = (int)std::ceil(h * rho - 1e-9);
                    std::shuffle(shuf_singletons.begin(), shuf_singletons.end(), rng);
                    run_dp_pass(h, min_h, shuf_singletons, width_scores, 0);
                } else {
                    int h = h_pool[rng() % hn];
                    int min_h = (int)std::ceil(h * rho - 1e-9);
                    mixed_order.clear();
                    // Collect stacks in [0.90*h, h], shuffle them, put at most 1..3 ahead of shuffled singletons
                    for (int sid : stack_ids) {
                        if (cols[sid].h <= h && cols[sid].h * 100 >= h * 90) {
                            mixed_order.push_back(sid);
                        }
                    }
                    std::shuffle(mixed_order.begin(), mixed_order.end(), rng);
                    std::shuffle(shuf_singletons.begin(), shuf_singletons.end(), rng);
                    for (int cid : shuf_singletons) mixed_order.push_back(cid);

                    int stack_cap = 1 + (int)(rng() % 3); // 1, 2, or 3 stacks per strip
                    const auto& sc = ((rng() & 1) == 0) ? area_scores : width_scores;
                    run_dp_pass(h, min_h, mixed_order, sc, stack_cap);
                }
            }
        } else {
            // Round 2: LP Shadow-Price Dual-Guided Pricing!
            std::vector<double> col_dual(num_cols, 0.0);
            std::vector<long long> dual_scores(num_cols, 0);
            for (int c = 0; c < num_cols; ++c) {
                double v = 0.0;
                for (int k = 0; k < cols[c].n_items; ++k) v += duals[cols[c].items[k]];
                col_dual[c] = v;
                dual_scores[c] = std::max(1LL, (long long)std::llround(std::max(1e-4, v) * 10000.0));
            }

            std::vector<int> singletons_dual = singleton_ids;
            std::sort(singletons_dual.begin(), singletons_dual.end(), [&](int a, int b) {
                return (col_dual[a] / cols[a].w) > (col_dual[b] / cols[b].w);
            });

            std::vector<int> mixed_dual;
            mixed_dual.reserve(num_cols);
            for (int sid : stack_ids) mixed_dual.push_back(sid);
            for (int cid : singleton_ids) mixed_dual.push_back(cid);
            std::sort(mixed_dual.begin(), mixed_dual.end(), [&](int a, int b) {
                return (col_dual[a] / cols[a].w) > (col_dual[b] / cols[b].w);
            });

            int ah_n = (int)anchor_heights.size();
            int step2 = std::max(1, ah_n / 180);
            std::vector<int> sweep_h;
            for (int i = ah_n - 1; i >= 0; i -= step2) sweep_h.push_back(anchor_heights[i]);
            if (sweep_h.back() != anchor_heights.front()) sweep_h.push_back(anchor_heights.front());

            // Pass 2A: Unperturbed exact dual pricing across height spectrum
            for (int h : sweep_h) {
                if (elapsed() >= time_limit_s * 0.45 || (int)strips.size() >= max_strips_cap) break;
                run_dp_pass(h, (int)(h * 0.70), singletons_dual, dual_scores, 0);
                run_dp_pass(h, 0, singletons_dual, dual_scores, 0);
                if (allow_stacks && !stack_ids.empty()) {
                    run_dp_pass(h, (int)(h * 0.75), mixed_dual, dual_scores, 3);
                }
            }

            // Pass 2B: Perturbed dual pricing + exact width pricing on high-dual items
            std::uniform_real_distribution<double> dual_noise(0.93, 1.07);
            const double rhos[4] = {0.70, 0.85, 0.93, 0.98};
            std::vector<long long> pert_scores(num_cols, 0);
            std::vector<int> dyn_order;
            while (elapsed() < time_limit_s * 0.95 && (int)strips.size() < max_strips_cap) {
                int h = anchor_heights[rng() % ah_n];
                double rho = rhos[rng() & 3];
                bool use_stacks = allow_stacks && !stack_ids.empty() && ((rng() & 1) != 0);
                dyn_order = use_stacks ? mixed_dual : singletons_dual;
                for (int c : dyn_order) {
                    double pv = std::max(1e-4, col_dual[c]) * dual_noise(rng);
                    pert_scores[c] = std::max(1LL, (long long)std::llround(pv * 10000.0));
                }
                std::sort(dyn_order.begin(), dyn_order.end(), [&](int a, int b) {
                    return ((double)pert_scores[a] / cols[a].w) > ((double)pert_scores[b] / cols[b].w);
                });
                run_dp_pass(h, (int)(h * rho), dyn_order, pert_scores, use_stacks ? 3 : 0);
            }
        }

        ensure_all_items_covered(rng);
        return (int)strips.size();
    }
};

} // namespace

extern "C" {

void* colstrip_create(int m, const int* w, const int* h, const int* copies,
                      int sheet_w, int sheet_h, long long waste_budget, int allow_stacks) {
    return new Pool(m, w, h, copies, sheet_w, sheet_h, waste_budget, allow_stacks);
}

void colstrip_destroy(void* handle) {
    delete static_cast<Pool*>(handle);
}

int colstrip_generate(void* handle, const double* duals, double time_limit_s,
                      unsigned int seed, int max_strips_cap) {
    Pool* p = static_cast<Pool*>(handle);
    return p->generate(duals, time_limit_s, seed, max_strips_cap);
}

void colstrip_counts(void* handle, int* out_num_strips, long long* out_nnz, int* out_num_cand_cols) {
    Pool* p = static_cast<Pool*>(handle);
    *out_num_strips = (int)p->strips.size();
    *out_nnz = p->total_nnz;
    *out_num_cand_cols = (int)p->cols.size();
}

void colstrip_export_lp(void* handle,
                        int* out_height,
                        long long* out_area,
                        long long* out_waste,
                        int* out_ub,
                        int* out_nz_row,
                        int* out_nz_col,
                        double* out_nz_val) {
    Pool* p = static_cast<Pool*>(handle);
    int ns = (int)p->strips.size();
    long long pos = 0;
    for (int j = 0; j < ns; ++j) {
        const StripRec& s = p->strips[j];
        out_height[j] = s.height;
        out_area[j] = s.area;
        out_waste[j] = s.waste;
        out_ub[j] = s.max_ub;
        for (const auto& kv : s.item_counts) {
            out_nz_row[pos] = kv.first;
            out_nz_col[pos] = j;
            out_nz_val[pos] = (double)kv.second;
            ++pos;
        }
    }
}

int colstrip_get_strip_columns(void* handle, int strip_idx, int* out_cols6, int max_cols) {
    Pool* p = static_cast<Pool*>(handle);
    if (strip_idx < 0 || strip_idx >= (int)p->strips.size()) return 0;
    const StripRec& s = p->strips[strip_idx];
    int nc = std::min((int)s.col_ids.size(), max_cols);
    for (int i = 0; i < nc; ++i) {
        const CandCol& c = p->cols[s.col_ids[i]];
        out_cols6[i * 6 + 0] = c.w;
        out_cols6[i * 6 + 1] = c.h;
        out_cols6[i * 6 + 2] = c.n_items;
        out_cols6[i * 6 + 3] = c.items[0];
        out_cols6[i * 6 + 4] = c.items[1];
        out_cols6[i * 6 + 5] = c.items[2];
    }
    return nc;
}

} // extern "C"
