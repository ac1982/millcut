#include "mill_cxx23.hpp"

#include <algorithm>
#include <bit>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <numeric>
#include <random>
#include <ranges>
#include <sstream>
#include <unordered_map>
#include <utility>

namespace mill {

namespace {

constexpr int DP_MAX_CAP = 4096;
constexpr int MAX_DP_ITEMS = 320;

inline uint64_t splitmix64(uint64_t x) noexcept {
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

uint64_t hash_item_counts(std::span<const std::pair<int, int>> ic) noexcept {
    uint64_t h = 0x243f6a8885a308d3ULL ^ static_cast<uint64_t>(ic.size());
    for (const auto& [item_idx, mult] : ic) {
        uint64_t v = (static_cast<uint64_t>(static_cast<uint32_t>(item_idx)) << 32) |
                     static_cast<uint32_t>(mult);
        h = splitmix64(h ^ splitmix64(v));
    }
    return h;
}

std::string_view trim_sv(std::string_view sv) noexcept {
    while (!sv.empty() && (sv.front() == ' ' || sv.front() == '\t' || sv.front() == '\r' || sv.front() == '\n')) {
        sv.remove_prefix(1);
    }
    while (!sv.empty() && (sv.back() == ' ' || sv.back() == '\t' || sv.back() == '\r' || sv.back() == '\n')) {
        sv.remove_suffix(1);
    }
    return sv;
}

std::vector<std::string_view> split_csv_line(std::string_view line) {
    std::vector<std::string_view> cols;
    std::size_t start = 0;
    while (start <= line.size()) {
        std::size_t comma = line.find(',', start);
        if (comma == std::string_view::npos) {
            cols.push_back(trim_sv(line.substr(start)));
            break;
        }
        cols.push_back(trim_sv(line.substr(start, comma - start)));
        start = comma + 1;
    }
    return cols;
}

std::expected<int, ErrorInfo> parse_int_sv(std::string_view sv, std::string_view field_name) {
    sv = trim_sv(sv);
    if (sv.empty()) {
        return std::unexpected(ErrorInfo{"CSV_EMPTY_INT", "Empty integer in field: " + std::string(field_name)});
    }
    int val = 0;
    auto [ptr, ec] = std::from_chars(sv.data(), sv.data() + sv.size(), val);
    if (ec != std::errc{} || ptr != sv.data() + sv.size()) {
        return std::unexpected(ErrorInfo{"CSV_BAD_INT", "Invalid integer '" + std::string(sv) + "' in " + std::string(field_name)});
    }
    return val;
}

double compute_cv(std::span<const double> vals) noexcept {
    if (vals.size() < 2) return 0.0;
    double mean = std::accumulate(vals.begin(), vals.end(), 0.0) / static_cast<double>(vals.size());
    if (mean <= 1e-12) return 0.0;
    double sq = 0.0;
    for (double v : vals) {
        double d = v - mean;
        sq += d * d;
    }
    return std::sqrt(sq / static_cast<double>(vals.size())) / mean;
}

int count_plates(std::span<const PlatePlan> plates) noexcept {
    int total = 0;
    for (const auto& p : plates) total += p.copies;
    return total;
}

// ---------------------------------------------------------------------------
// Candidate Columns & Strip Pool (Pure C++23 with FlatGrid2D)
// ---------------------------------------------------------------------------

struct CandCol {
    int w = 0;
    int h = 0;
    long long area = 0;
    int n_items = 1;
    std::array<int, 3> items{{-1, -1, -1}};
    double eff = 1.0;
};

struct StripRec {
    int height = 0;
    long long area = 0;
    long long waste = 0;
    int max_ub = 1;
    bool emergency = false;
    std::vector<std::pair<int, int>> item_counts;
    std::vector<int> col_ids;
};

class ColStripPool {
public:
    int M = 0;
    int W = 0;
    int H = 0;
    long long waste_budget = 0;
    bool allow_stacks = false;
    int dp_cap = 0;

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

    std::vector<long long> dp_val;
    std::vector<uint8_t> dp_take_buf;
    std::vector<int> scratch_counts;
    std::vector<int> scratch_avail;

    ColStripPool(const OrderSpec& ord, long long w_budget, bool stacks)
        : M(static_cast<int>(ord.items.size())),
          W(ord.sheet_w),
          H(ord.sheet_h),
          waste_budget(w_budget),
          allow_stacks(stacks),
          iw(M), ih(M), icopies(M),
          item_freq(M, 0),
          scratch_counts(M, 0),
          scratch_avail(M, 0) {
        for (int i = 0; i < M; ++i) {
            iw[i] = ord.items[i].w;
            ih[i] = ord.items[i].h;
            icopies[i] = ord.items[i].copies;
        }
        dp_cap = (W <= DP_MAX_CAP) ? W : DP_MAX_CAP;
        dp_val.assign(static_cast<std::size_t>(dp_cap + 1), 0);
        dp_take_buf.assign(static_cast<std::size_t>(MAX_DP_ITEMS) * static_cast<std::size_t>(dp_cap + 1), 0);
        build_columns();
    }

    [[nodiscard]] int scale_w(int w_actual) const noexcept {
        if (W <= DP_MAX_CAP) return w_actual;
        long long num = static_cast<long long>(w_actual) * DP_MAX_CAP + W - 1;
        return std::max(1, static_cast<int>(num / W));
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
            c.area = static_cast<long long>(iw[t]) * ih[t];
            c.n_items = 1;
            c.items = {t, -1, -1};
            c.eff = 1.0;
            singleton_ids.push_back(static_cast<int>(cols.size()));
            singleton_heights.push_back(c.h);
            cols.push_back(c);
        }
        std::ranges::sort(singleton_heights);
        auto u1 = std::ranges::unique(singleton_heights);
        singleton_heights.erase(u1.begin(), u1.end());

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
                    long long ac = static_cast<long long>(iw[i]) * ih[i] + static_cast<long long>(iw[j]) * ih[j];
                    long long box = static_cast<long long>(wc) * hc;
                    if (ac * 100 >= box * 82) {
                        CandCol c;
                        c.w = wc;
                        c.h = hc;
                        c.area = ac;
                        c.n_items = 2;
                        c.items = {i, j, -1};
                        c.eff = static_cast<double>(ac) / static_cast<double>(box);
                        stacks2.push_back(c);
                    }
                }
            }
            std::ranges::sort(stacks2, [](const CandCol& a, const CandCol& b) {
                if (std::abs(a.eff - b.eff) > 1e-9) return a.eff > b.eff;
                return a.area > b.area;
            });
            if (stacks2.size() > 1400) stacks2.resize(1400);

            std::vector<CandCol> stacks3;
            int lim2 = std::min(static_cast<int>(stacks2.size()), 180);
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
                        long long ac = base.area + static_cast<long long>(iw[k]) * ih[k];
                        long long box = static_cast<long long>(wc) * hc;
                        if (ac * 100 >= box * 95) {
                            CandCol c;
                            c.w = wc;
                            c.h = hc;
                            c.area = ac;
                            c.n_items = 3;
                            c.items = {i, j, k};
                            c.eff = static_cast<double>(ac) / static_cast<double>(box);
                            stacks3.push_back(c);
                        }
                    }
                }
                std::ranges::sort(stacks3, [](const CandCol& a, const CandCol& b) {
                    if (std::abs(a.eff - b.eff) > 1e-9) return a.eff > b.eff;
                    return a.area > b.area;
                });
                if (stacks3.size() > 300) stacks3.resize(300);
            }

            for (const auto& c : stacks2) {
                stack_ids.push_back(static_cast<int>(cols.size()));
                cols.push_back(c);
            }
            for (const auto& c : stacks3) {
                stack_ids.push_back(static_cast<int>(cols.size()));
                cols.push_back(c);
            }
        }

        anchor_heights.clear();
        for (const auto& c : cols) anchor_heights.push_back(c.h);
        std::ranges::sort(anchor_heights);
        auto u2 = std::ranges::unique(anchor_heights);
        anchor_heights.erase(u2.begin(), u2.end());
    }

    [[nodiscard]] bool can_use_col(const CandCol& c, std::span<const int> avail) const noexcept {
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
        long long waste = static_cast<long long>(W) * max_h - area;
        long long max_allowed = relax_waste ? (static_cast<long long>(W) * H) : waste_budget;
        if (waste > max_allowed) {
            for (int t : touched) scratch_counts[t] = 0;
            return false;
        }

        std::ranges::sort(touched);
        std::vector<std::pair<int, int>> ic;
        ic.reserve(touched.size());
        int ub = 1000000000;
        for (int t : touched) {
            int mult = scratch_counts[t];
            scratch_counts[t] = 0;
            ic.emplace_back(t, mult);
            ub = std::min(ub, icopies[t] / mult);
        }
        if (ub <= 0) return false;

        uint64_t hval = hash_item_counts(ic);
        if (auto it = multiset_map.find(hval); it != multiset_map.end()) {
            for (int idx : it->second) {
                if (strips[idx].item_counts == ic) {
                    if (max_h < strips[idx].height) {
                        strips[idx].height = max_h;
                        strips[idx].area = area;
                        strips[idx].waste = waste;
                        strips[idx].emergency = (waste > waste_budget);
                        strips[idx].col_ids = chosen_cols;
                        return true;
                    }
                    return false;
                }
            }
        }

        int new_idx = static_cast<int>(strips.size());
        StripRec rec;
        rec.height = max_h;
        rec.area = area;
        rec.waste = waste;
        rec.max_ub = ub;
        rec.emergency = (waste > waste_budget);
        rec.item_counts = std::move(ic);
        rec.col_ids = chosen_cols;
        for (const auto& [t, _] : rec.item_counts) item_freq[t] += 1;
        strips.push_back(std::move(rec));
        multiset_map[hval].push_back(new_idx);
        return true;
    }

    void greedy_complete(std::vector<int>& chosen, std::span<const int> order, int min_h_cap, int max_h_cap) {
        long long used_w = 0;
        std::ranges::copy(icopies, scratch_avail.begin());
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

    void run_dp_pass(int anchor_h, int min_h, std::span<const int> col_order,
                     std::span<const long long> col_scores, int max_stacks_admit = 4) {
        std::ranges::copy(icopies, scratch_avail.begin());
        std::array<int, MAX_DP_ITEMS> dp_col_ids{};
        std::array<int, MAX_DP_ITEMS> dp_cw{};
        std::array<long long, MAX_DP_ITEMS> dp_cval{};
        int K = 0;
        int admitted_stacks = 0;

        for (int cid : col_order) {
            if (K >= MAX_DP_ITEMS) break;
            const CandCol& c = cols[cid];
            if (c.h > anchor_h || c.h < min_h) continue;
            if (c.n_items > 1) {
                if (admitted_stacks >= max_stacks_admit) continue;
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

        const std::size_t stride = static_cast<std::size_t>(dp_cap + 1);
        FlatGrid2D<uint8_t> dp_take(dp_take_buf.data(), static_cast<std::size_t>(K), stride);
        std::ranges::fill(dp_val, 0LL);

        for (int idx = 0; idx < K; ++idx) {
            auto rspan = dp_take.row(static_cast<std::size_t>(idx));
            std::ranges::fill(rspan, uint8_t{0});
            int sw = dp_cw[idx];
            long long sv = dp_cval[idx];
            for (int w = dp_cap; w >= sw; --w) {
                long long cand = dp_val[w - sw] + sv;
                if (cand > dp_val[w]) {
                    dp_val[w] = cand;
                    dp_take[static_cast<std::size_t>(idx), static_cast<std::size_t>(w)] = 1;
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
            if (dp_take[static_cast<std::size_t>(idx), static_cast<std::size_t>(cur_w)]) {
                int cid = dp_col_ids[idx];
                chosen.push_back(cid);
                sum_actual_w += cols[cid].w;
                max_chosen_h = std::max(max_chosen_h, cols[cid].h);
                cur_w -= dp_cw[idx];
            }
        }
        if (chosen.empty()) return;

        add_strip(chosen, false);
        if ((W > DP_MAX_CAP || admitted_stacks > 0) && sum_actual_w < W) {
            std::vector<int> comp = chosen;
            greedy_complete(comp, col_order, min_h, max_chosen_h);
            if (comp.size() > chosen.size()) {
                add_strip(comp, false);
            }
        }
    }

    void ensure_all_items_covered(std::mt19937_64& rng) {
        std::vector<int> order = singleton_ids;
        std::ranges::sort(order, [&](int a, int b) { return cols[a].h > cols[b].h; });

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
                int min_h = static_cast<int>(anchor_h * 0.75);
                if (attempts % 2 == 0 && !singleton_heights.empty()) {
                    auto it = std::ranges::lower_bound(singleton_heights, anchor_h);
                    int idx = static_cast<int>(it - singleton_heights.begin());
                    int span = std::min(static_cast<int>(singleton_heights.size()) - idx, 6);
                    if (span > 1) anchor_h = singleton_heights[idx + static_cast<int>(rng() % span)];
                    min_h = static_cast<int>(anchor_h * 0.75);
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

    int generate_round1(double time_limit_s, uint32_t seed, int max_strips_cap) {
        auto t0 = std::chrono::steady_clock::now();
        auto elapsed = [&]() {
            return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        };
        std::mt19937_64 rng(seed);
        int num_cols = static_cast<int>(cols.size());
        if (num_cols == 0 || anchor_heights.empty()) return static_cast<int>(strips.size());

        std::vector<long long> width_scores(num_cols, 0);
        std::vector<long long> area_scores(num_cols, 0);
        for (int c = 0; c < num_cols; ++c) {
            width_scores[c] = cols[c].w;
            area_scores[c] = cols[c].area;
        }

        std::vector<int> s_order = singleton_ids;
        std::ranges::sort(s_order, [&](int a, int b) { return cols[a].h > cols[b].h; });

        constexpr std::array<double, 4> det_rhos{0.70, 0.85, 0.95, 1.0};
        for (int h : singleton_heights) {
            if (elapsed() >= time_limit_s * 0.22 || static_cast<int>(strips.size()) >= max_strips_cap) break;
            for (double rho : det_rhos) {
                int min_h = static_cast<int>(std::ceil(h * rho - 1e-9));
                run_dp_pass(h, min_h, s_order, width_scores, 0);
                if (allow_stacks) {
                    run_dp_pass(h, min_h, s_order, area_scores, 0);
                }
            }
        }

        std::vector<int> shuf_singletons = singleton_ids;
        std::vector<int> mixed_order;
        mixed_order.reserve(num_cols);
        const auto& h_pool = (!allow_stacks || singleton_heights.empty()) ? singleton_heights : anchor_heights;
        int hn = static_cast<int>(h_pool.size());

        while (elapsed() < time_limit_s * 0.96 && static_cast<int>(strips.size()) < max_strips_cap) {
            double rho = det_rhos[rng() & 3];
            bool use_st = allow_stacks && !stack_ids.empty() && ((rng() % 3) != 0);
            if (!use_st) {
                int h = singleton_heights[rng() % singleton_heights.size()];
                int min_h = static_cast<int>(std::ceil(h * rho - 1e-9));
                std::shuffle(shuf_singletons.begin(), shuf_singletons.end(), rng);
                run_dp_pass(h, min_h, shuf_singletons, width_scores, 0);
            } else {
                int h = h_pool[rng() % hn];
                int min_h = static_cast<int>(std::ceil(h * rho - 1e-9));
                mixed_order.clear();
                for (int sid : stack_ids) {
                    if (cols[sid].h <= h && cols[sid].h * 100 >= h * 90) {
                        mixed_order.push_back(sid);
                    }
                }
                std::shuffle(mixed_order.begin(), mixed_order.end(), rng);
                std::shuffle(shuf_singletons.begin(), shuf_singletons.end(), rng);
                for (int cid : shuf_singletons) mixed_order.push_back(cid);
                int stack_cap = 1 + static_cast<int>(rng() % 3);
                const auto& sc = ((rng() & 1) == 0) ? area_scores : width_scores;
                run_dp_pass(h, min_h, mixed_order, sc, stack_cap);
            }
        }

        ensure_all_items_covered(rng);
        return static_cast<int>(strips.size());
    }

    int generate_round2_dual(std::span<const double> duals, double time_limit_s, uint32_t seed, int max_strips_cap) {
        auto t0 = std::chrono::steady_clock::now();
        auto elapsed = [&]() {
            return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        };
        std::mt19937_64 rng(seed);
        int num_cols = static_cast<int>(cols.size());
        if (num_cols == 0 || anchor_heights.empty()) return static_cast<int>(strips.size());

        std::vector<double> col_dual(num_cols, 0.0);
        std::vector<long long> dual_scores(num_cols, 0);
        for (int c = 0; c < num_cols; ++c) {
            double v = 0.0;
            for (int k = 0; k < cols[c].n_items; ++k) v += duals[cols[c].items[k]];
            col_dual[c] = v;
            dual_scores[c] = std::max(1LL, static_cast<long long>(std::llround(std::max(1e-4, v) * 10000.0)));
        }

        std::vector<int> singletons_dual = singleton_ids;
        std::ranges::sort(singletons_dual, [&](int a, int b) {
            return (col_dual[a] / cols[a].w) > (col_dual[b] / cols[b].w);
        });

        std::vector<int> mixed_dual;
        mixed_dual.reserve(num_cols);
        for (int sid : stack_ids) mixed_dual.push_back(sid);
        for (int cid : singleton_ids) mixed_dual.push_back(cid);
        std::ranges::sort(mixed_dual, [&](int a, int b) {
            return (col_dual[a] / cols[a].w) > (col_dual[b] / cols[b].w);
        });

        int ah_n = static_cast<int>(anchor_heights.size());
        int step2 = std::max(1, ah_n / 180);
        std::vector<int> sweep_h;
        for (int i = ah_n - 1; i >= 0; i -= step2) sweep_h.push_back(anchor_heights[i]);
        if (sweep_h.back() != anchor_heights.front()) sweep_h.push_back(anchor_heights.front());

        for (int h : sweep_h) {
            if (elapsed() >= time_limit_s * 0.45 || static_cast<int>(strips.size()) >= max_strips_cap) break;
            run_dp_pass(h, static_cast<int>(h * 0.70), singletons_dual, dual_scores, 0);
            run_dp_pass(h, 0, singletons_dual, dual_scores, 0);
            if (allow_stacks && !stack_ids.empty()) {
                run_dp_pass(h, static_cast<int>(h * 0.75), mixed_dual, dual_scores, 3);
            }
        }

        std::uniform_real_distribution<double> dual_noise(0.93, 1.07);
        constexpr std::array<double, 4> rhos{0.70, 0.85, 0.93, 0.98};
        std::vector<long long> pert_scores(num_cols, 0);
        std::vector<int> dyn_order;
        while (elapsed() < time_limit_s * 0.95 && static_cast<int>(strips.size()) < max_strips_cap) {
            int h = anchor_heights[rng() % ah_n];
            double rho = rhos[rng() & 3];
            bool use_st = allow_stacks && !stack_ids.empty() && ((rng() & 1) != 0);
            dyn_order = use_st ? mixed_dual : singletons_dual;
            for (int c : dyn_order) {
                double pv = std::max(1e-4, col_dual[c]) * dual_noise(rng);
                pert_scores[c] = std::max(1LL, static_cast<long long>(std::llround(pv * 10000.0)));
            }
            std::ranges::sort(dyn_order, [&](int a, int b) {
                return (static_cast<double>(pert_scores[a]) / cols[a].w) >
                       (static_cast<double>(pert_scores[b]) / cols[b].w);
            });
            run_dp_pass(h, static_cast<int>(h * rho), dyn_order, pert_scores, use_st ? 3 : 0);
        }

        ensure_all_items_covered(rng);
        return static_cast<int>(strips.size());
    }
};

// ---------------------------------------------------------------------------
// Self-Contained Pure C++23 Lagrangian Polyak Subgradient Dual Optimizer
// Rigorous Mathematical Dual Lower Bound:
//   L(pi) = sum_i d_i pi_i + sum_j u_j min(0, c_j - sum_i a_ij pi_i) <= LP*
// ---------------------------------------------------------------------------

struct DualResult {
    double best_dual_lb = 0.0;
    std::vector<double> duals;
    std::vector<double> reduced_costs;
};

DualResult solve_lagrangian_duals(const ColStripPool& pool, double upper_bound_h) {
    const int M = pool.M;
    const int S = static_cast<int>(pool.strips.size());
    DualResult out;
    out.duals.assign(M, 0.0);
    out.reduced_costs.assign(S, 0.0);
    if (S == 0 || M == 0) return out;

    std::vector<double> c_cost(S, 0.0);
    for (int j = 0; j < S; ++j) {
        c_cost[j] = static_cast<double>(pool.strips[j].height) +
                    (pool.strips[j].emergency ? 10.0 * pool.H : 0.0);
    }

    // Initialize duals proportional to item area / sheet width (= area-equivalent height)
    std::vector<double> pi(M, 0.0);
    for (int i = 0; i < M; ++i) {
        pi[i] = (static_cast<double>(pool.iw[i]) * pool.ih[i]) / std::max(1, pool.W);
    }

    std::vector<double> best_pi = pi;
    double best_L = -1e18;
    std::vector<double> subgrad(M, 0.0);
    std::vector<double> velocity(M, 0.0);
    double lambda = 1.6;
    int no_improve = 0;

    for (int iter = 0; iter < 360; ++iter) {
        for (int i = 0; i < M; ++i) subgrad[i] = static_cast<double>(pool.icopies[i]);

        double L = 0.0;
        for (int i = 0; i < M; ++i) L += pool.icopies[i] * pi[i];

        for (int j = 0; j < S; ++j) {
            const auto& st = pool.strips[j];
            double rc = c_cost[j];
            for (const auto& [t, mult] : st.item_counts) {
                rc -= mult * pi[t];
            }
            if (rc < 0.0) {
                L += st.max_ub * rc;
                for (const auto& [t, mult] : st.item_counts) {
                    subgrad[t] -= static_cast<double>(st.max_ub * mult);
                }
            }
        }

        if (L > best_L) {
            best_L = L;
            best_pi = pi;
            no_improve = 0;
        } else {
            ++no_improve;
            if (no_improve >= 28) {
                lambda *= 0.65;
                no_improve = 0;
            }
        }

        double norm2 = 0.0;
        for (double g : subgrad) norm2 += g * g;
        if (norm2 < 1e-9 || lambda < 1e-4) break;

        double target_ub = std::max(upper_bound_h, best_L + 0.02 * pool.H);
        double step = lambda * (target_ub - L) / norm2;
        for (int i = 0; i < M; ++i) {
            velocity[i] = 0.35 * velocity[i] + step * subgrad[i];
            pi[i] = std::max(0.0, pi[i] + velocity[i]);
        }
    }

    out.best_dual_lb = std::max(0.0, best_L);
    out.duals = std::move(best_pi);
    for (int j = 0; j < S; ++j) {
        double rc = c_cost[j];
        for (const auto& [t, mult] : pool.strips[j].item_counts) {
            rc -= mult * out.duals[t];
        }
        out.reduced_costs[j] = rc;
    }
    return out;
}

// ---------------------------------------------------------------------------
// Bit-Parallel 256-bit Helper (C++23 <bit>: std::popcount & std::countr_zero)
// ---------------------------------------------------------------------------

struct BitSet256 {
    std::array<uint64_t, 4> w{{0, 0, 0, 0}};

    constexpr void set(int bit) noexcept {
        w[bit >> 6] |= (uint64_t{1} << (bit & 63));
    }
    constexpr void reset(int bit) noexcept {
        w[bit >> 6] &= ~(uint64_t{1} << (bit & 63));
    }
    [[nodiscard]] constexpr bool test(int bit) const noexcept {
        return (w[bit >> 6] >> (bit & 63)) & 1ULL;
    }
    [[nodiscard]] constexpr bool empty() const noexcept {
        return (w[0] | w[1] | w[2] | w[3]) == 0ULL;
    }
    [[nodiscard]] constexpr int count() const noexcept {
        return std::popcount(w[0]) + std::popcount(w[1]) + std::popcount(w[2]) + std::popcount(w[3]);
    }
};

// ---------------------------------------------------------------------------
// Pure C++23 Exact Strip Cover + Simultaneous 1D Bin-Packing Branch-and-Bound
// Uses exact waste-slack identity + MRV item selection + conflict-driven Luby restarts!
// ---------------------------------------------------------------------------

struct StripPlacement {
    int orig_strip_idx = 0;
    int plate_idx = 0;
};

struct ExactBnBResult {
    bool found = false;
    bool proven_infeasible = false;
    long long nodes_explored = 0;
    std::vector<StripPlacement> placements;
};

ExactBnBResult solve_exact_cover_bnb(
    const ColStripPool& pool,
    std::span<const int> retained_indices,
    int target_bins,
    std::chrono::steady_clock::time_point deadline,
    uint32_t seed) {
    ExactBnBResult res;
    const int M = pool.M;
    const int W = pool.W;
    const int H = pool.H;

    long long total_item_area = 0;
    int total_pieces = 0;
    for (int t = 0; t < M; ++t) {
        total_item_area += static_cast<long long>(pool.iw[t]) * pool.ih[t] * pool.icopies[t];
        total_pieces += pool.icopies[t];
    }
    const long long max_total_waste = static_cast<long long>(target_bins) * W * H - total_item_area;
    if (max_total_waste < 0) {
        res.proven_infeasible = true;
        return res;
    }

    struct CandStrip {
        int orig_idx = 0;
        int h = 0;
        long long area = 0;
        long long base_waste = 0;
        int max_ub = 1;
        BitSet256 mask{};
        std::vector<std::pair<int, int>> item_counts;
    };

    const bool use_bitmask = (M <= 256);
    std::vector<CandStrip> cands;
    cands.reserve(retained_indices.size());
    int min_strip_h = H + 1;

    for (int orig_idx : retained_indices) {
        const auto& s = pool.strips[orig_idx];
        // Any strip whose internal waste alone exceeds max_total_waste can NEVER appear in a valid target_bins cover!
        if (s.waste > max_total_waste) continue;
        CandStrip cs;
        cs.orig_idx = orig_idx;
        cs.h = s.height;
        cs.area = s.area;
        cs.base_waste = s.waste;
        cs.max_ub = s.max_ub;
        cs.item_counts = s.item_counts;
        if (use_bitmask) {
            for (const auto& [t, _] : s.item_counts) cs.mask.set(t);
        }
        min_strip_h = std::min(min_strip_h, cs.h);
        cands.push_back(std::move(cs));
    }

    const int S = static_cast<int>(cands.size());
    if (S == 0) {
        res.proven_infeasible = true;
        return res;
    }

    // Build item -> candidate strip adjacency list
    std::vector<std::vector<int>> item_to_strips(M);
    for (int si = 0; si < S; ++si) {
        for (const auto& [t, _] : cands[si].item_counts) {
            item_to_strips[t].push_back(si);
        }
    }
    for (int t = 0; t < M; ++t) {
        if (pool.icopies[t] > 0 && item_to_strips[t].empty()) {
            // Retained + waste-filtered pool leaves item t uncovered -> infeasible on this pool
            return res;
        }
    }

    std::vector<long long> item_area(M, 0);
    for (int t = 0; t < M; ++t) {
        item_area[t] = static_cast<long long>(pool.iw[t]) * pool.ih[t];
    }

    std::vector<double> conflict_weight(M, 0.0);
    std::vector<int> rem = pool.icopies;
    std::vector<int> strip_used(S, 0);
    std::vector<int> bin_h(target_bins, 0);
    std::vector<StripPlacement> current_sol;
    current_sol.reserve(target_bins * 8);

    std::mt19937_64 rng(seed ^ 0x5bd1e995U);
    std::vector<double> strip_jitter(S, 0.0);

    bool timed_out = false;
    long long restart_limit = 3500;
    int restart_idx = 0;

    while (!res.found && std::chrono::steady_clock::now() < deadline) {
        if (restart_idx == 0) {
            std::ranges::fill(strip_jitter, 0.0);
        } else {
            std::uniform_real_distribution<double> dist(0.0, 0.03 * W);
            for (int si = 0; si < S; ++si) strip_jitter[si] = dist(rng);
        }

        long long local_nodes = 0;
        bool hit_restart_cap = false;
        std::ranges::copy(pool.icopies, rem.begin());
        std::ranges::fill(strip_used, 0);
        std::ranges::fill(bin_h, 0);
        current_sol.clear();

        auto dfs = [&](auto& self, int rem_pieces, long long accum_waste) -> bool {
            if (rem_pieces == 0) {
                res.found = true;
                res.placements = current_sol;
                return true;
            }
            ++res.nodes_explored;
            ++local_nodes;
            if ((local_nodes & 127LL) == 0) {
                if (std::chrono::steady_clock::now() >= deadline) {
                    timed_out = true;
                    return false;
                }
            }
            if (local_nodes >= restart_limit) {
                hit_restart_cap = true;
                return false;
            }

            // Compute max remaining free height across all bins & locked dead-top waste
            int max_free_h = 0;
            long long dead_top_waste = 0;
            for (int b = 0; b < target_bins; ++b) {
                int free_h = H - bin_h[b];
                if (free_h > max_free_h) max_free_h = free_h;
                if (free_h < min_strip_h) {
                    dead_top_waste += static_cast<long long>(W) * free_h;
                }
            }
            if (accum_waste + dead_top_waste > max_total_waste) {
                return false;
            }

            // Tallest uncovered item must fit in max_free_h
            int tallest_rem = 0;
            for (int t = 0; t < M; ++t) {
                if (rem[t] > 0 && pool.ih[t] > tallest_rem) tallest_rem = pool.ih[t];
            }
            if (tallest_rem > max_free_h) {
                return false;
            }

            // MRV + Conflict-Driven Pivot Item Selection
            int pivot_t = -1;
            double best_mrv_score = 1e30;
            for (int t = 0; t < M; ++t) {
                if (rem[t] <= 0) continue;
                int feasible_deg = 0;
                for (int si : item_to_strips[t]) {
                    const auto& cs = cands[si];
                    if (strip_used[si] >= cs.max_ub || cs.h > max_free_h) continue;
                    // Optimistic waste check using cs.base_waste
                    if (accum_waste + cs.base_waste + dead_top_waste <= max_total_waste) {
                        ++feasible_deg;
                    }
                }
                if (feasible_deg == 0) {
                    conflict_weight[t] += 1.0;
                    return false;
                }
                double sc = static_cast<double>(feasible_deg) /
                            (1.0 + 0.12 * conflict_weight[t] + 0.25 * (static_cast<double>(pool.ih[t]) / H));
                if (sc < best_mrv_score) {
                    best_mrv_score = sc;
                    pivot_t = t;
                    if (feasible_deg == 1) break; // Forced unit propagation!
                }
            }
            if (pivot_t < 0) return false;

            // Gather and rank valid strips covering pivot_t
            struct Choice {
                int si;
                long long step_waste;
                int gain_pieces;
                double rank_key;
            };
            std::vector<Choice> choices;
            choices.reserve(item_to_strips[pivot_t].size());

            for (int si : item_to_strips[pivot_t]) {
                const auto& cs = cands[si];
                if (strip_used[si] >= cs.max_ub || cs.h > max_free_h) continue;
                long long gain_area = 0;
                int gain_p = 0;
                for (const auto& [it_idx, mult] : cs.item_counts) {
                    int take = std::min(rem[it_idx], mult);
                    if (take > 0) {
                        gain_area += take * item_area[it_idx];
                        gain_p += take;
                    }
                }
                if (gain_p <= 0) continue;
                long long sw = static_cast<long long>(W) * cs.h - gain_area;
                if (accum_waste + sw > max_total_waste) continue;
                double rkey = static_cast<double>(sw) + strip_jitter[si] - 0.05 * cs.h;
                choices.push_back(Choice{si, sw, gain_p, rkey});
            }

            std::ranges::sort(choices, [](const Choice& a, const Choice& b) {
                return a.rank_key < b.rank_key;
            });

            // Branch on candidate strips & distinct Best-Fit bins
            std::vector<std::pair<int, int>> undo_rem;
            undo_rem.reserve(16);

            for (const auto& ch : choices) {
                const auto& cs = cands[ch.si];

                // Order candidate bins by Best-Fit (largest bin_h[b] that fits cs.h), skipping symmetric bin_h values!
                std::array<int, 16> bin_order{};
                int nb = 0;
                for (int b = 0; b < target_bins && nb < 16; ++b) {
                    if (bin_h[b] + cs.h <= H) {
                        bin_order[nb++] = b;
                    }
                }
                if (nb == 0) continue;
                std::sort(bin_order.begin(), bin_order.begin() + nb, [&](int a, int b) {
                    return bin_h[a] > bin_h[b];
                });

                // Apply item demand reductions once for strip ch.si
                undo_rem.clear();
                for (const auto& [it_idx, mult] : cs.item_counts) {
                    int take = std::min(rem[it_idx], mult);
                    if (take > 0) {
                        rem[it_idx] -= take;
                        undo_rem.emplace_back(it_idx, take);
                    }
                }
                strip_used[ch.si] += 1;

                int prev_bh = -1;
                for (int bi = 0; bi < nb; ++bi) {
                    int b = bin_order[bi];
                    if (bin_h[b] == prev_bh) continue; // Symmetry break identical bins!
                    prev_bh = bin_h[b];

                    bin_h[b] += cs.h;
                    current_sol.push_back(StripPlacement{cs.orig_idx, b});

                    if (self(self, rem_pieces - ch.gain_pieces, accum_waste + ch.step_waste)) {
                        return true;
                    }

                    current_sol.pop_back();
                    bin_h[b] -= cs.h;
                    if (timed_out || hit_restart_cap) break;
                }

                strip_used[ch.si] -= 1;
                for (const auto& [it_idx, take] : undo_rem) rem[it_idx] += take;

                if (timed_out || hit_restart_cap) return false;
            }

            conflict_weight[pivot_t] += 0.5;
            return false;
        };

        dfs(dfs, total_pieces, 0LL);

        if (res.found) break;
        if (!hit_restart_cap && !timed_out) {
            // Exhausted entire search tree on the first un-truncated pass -> proven infeasible for this pool!
            res.proven_infeasible = true;
            break;
        }
        if (timed_out) break;

        ++restart_idx;
        restart_limit = static_cast<long long>(restart_limit * 1.65) + 1500;
    }

    return res;
}

// Reconstruct concrete 3-stage PlatePlan vector from pool strip placements
std::vector<PlatePlan> reconstruct_plates_from_placements(
    const OrderSpec& order,
    const ColStripPool& pool,
    int target_bins,
    std::span<const StripPlacement> placements) {
    std::vector<int> rem(pool.M);
    for (int t = 0; t < pool.M; ++t) rem[t] = order.items[t].copies;

    std::vector<std::vector<int>> bin_strips(target_bins);
    for (const auto& pl : placements) {
        bin_strips[pl.plate_idx].push_back(pl.orig_strip_idx);
    }

    std::vector<PlatePlan> plates;
    plates.reserve(target_bins);

    for (int b = 0; b < target_bins; ++b) {
        std::ranges::sort(bin_strips[b], [&](int sa, int sb) {
            return pool.strips[sa].height > pool.strips[sb].height;
        });

        PlatePlan plate;
        plate.plate_id = static_cast<int>(plates.size());
        plate.copies = 1;
        int cur_y = 0;

        for (int s_idx : bin_strips[b]) {
            const auto& srec = pool.strips[s_idx];
            StripPlan sp;
            sp.y = cur_y;
            int cur_x = 0;
            int actual_strip_h = 0;

            for (int cid : srec.col_ids) {
                const auto& col = pool.cols[cid];
                std::vector<int> active;
                for (int k = 0; k < col.n_items; ++k) {
                    int t = col.items[k];
                    if (rem[t] > 0) {
                        rem[t] -= 1;
                        active.push_back(t);
                    }
                }
                if (active.empty()) continue;

                // Sort stacked items widest first at bottom for clean aesthetics
                std::ranges::sort(active, [&](int a, int b2) { return order.items[a].w > order.items[b2].w; });
                ColumnPlan cp;
                cp.x = cur_x;
                cp.y = cur_y;
                cp.w = 0;
                int stack_y = cur_y;
                for (int t : active) {
                    cp.w = std::max(cp.w, order.items[t].w);
                    cp.slices.push_back(PlacedItem{
                        .item_idx = t,
                        .item_id = order.items[t].id,
                        .x = cur_x,
                        .y = stack_y,
                        .w = order.items[t].w,
                        .h = order.items[t].h,
                    });
                    stack_y += order.items[t].h;
                }
                actual_strip_h = std::max(actual_strip_h, stack_y - cur_y);
                cur_x += cp.w;
                sp.columns.push_back(std::move(cp));
            }

            if (!sp.columns.empty()) {
                sp.h = actual_strip_h;
                for (auto& cp : sp.columns) cp.h = actual_strip_h;
                cur_y += actual_strip_h;
                plate.strips.push_back(std::move(sp));
            }
        }

        if (!plate.strips.empty()) {
            plates.push_back(std::move(plate));
        }
    }
    return plates;
}

// ---------------------------------------------------------------------------
// Pure C++23 3-Stage NonExact Horizontal Guillotine Shelf & Plate-DP Search
// Provides instant baseline (`BaselineGreedy`) + multi-plate / tight-shelf search (`TightShelf` & `Default`)
// ---------------------------------------------------------------------------

std::vector<PlatePlan> pack_strips_best_fit(std::vector<StripPlan> strips, int H) {
    // Sort strips tallest-first for Best-Fit Decreasing 1D bin packing into plates of height H
    std::ranges::sort(strips, [](const StripPlan& a, const StripPlan& b) {
        if (a.h != b.h) return a.h > b.h;
        int wa = 0, wb = 0;
        for (const auto& c : a.columns) wa += c.w;
        for (const auto& c : b.columns) wb += c.w;
        return wa > wb;
    });

    struct OpenPlate {
        int used_h = 0;
        std::vector<StripPlan> strips;
    };
    std::vector<OpenPlate> open;

    for (auto& sp : strips) {
        int best_p = -1;
        int best_rem = H + 1;
        for (int p = 0; p < static_cast<int>(open.size()); ++p) {
            int rem = H - open[p].used_h;
            if (sp.h <= rem && (rem - sp.h) < best_rem) {
                best_rem = rem - sp.h;
                best_p = p;
            }
        }
        if (best_p < 0) {
            best_p = static_cast<int>(open.size());
            open.push_back(OpenPlate{});
        }
        int base_y = open[best_p].used_h;
        int dy = base_y - sp.y;
        sp.y = base_y;
        for (auto& col : sp.columns) {
            col.y = base_y;
            col.h = sp.h;
            for (auto& sl : col.slices) sl.y += dy;
        }
        open[best_p].used_h += sp.h;
        open[best_p].strips.push_back(std::move(sp));
    }

    std::vector<PlatePlan> out;
    out.reserve(open.size());
    for (int i = 0; i < static_cast<int>(open.size()); ++i) {
        out.push_back(PlatePlan{
            .plate_id = i,
            .copies = 1,
            .strips = std::move(open[i].strips),
        });
    }
    return out;
}

// Classical FFDH sequential shelf constructor (used for BaselineGreedy & guaranteed <1ms fallback)
std::vector<PlatePlan> solve_ffdh_baseline(const OrderSpec& order, bool use_best_fit_plates) {
    struct Piece {
        int item_idx;
        int id;
        int w;
        int h;
    };
    std::vector<Piece> pieces;
    for (int i = 0; i < static_cast<int>(order.items.size()); ++i) {
        const auto& it = order.items[i];
        for (int c = 0; c < it.copies; ++c) {
            pieces.push_back(Piece{i, it.id, it.w, it.h});
        }
    }
    std::ranges::sort(pieces, [](const Piece& a, const Piece& b) {
        if (a.h != b.h) return a.h > b.h;
        return a.w > b.w;
    });

    struct ActiveShelf {
        int used_w = 0;
        int h = 0;
        StripPlan plan;
    };

    if (!use_best_fit_plates) {
        // Classic sequential plate-by-plate FFDH
        std::vector<PlatePlan> plates;
        int cur_y = order.sheet_h + 1;
        std::vector<ActiveShelf> shelves_on_plate;

        auto flush_plate = [&]() {
            if (shelves_on_plate.empty()) return;
            PlatePlan pp;
            pp.plate_id = static_cast<int>(plates.size());
            pp.copies = 1;
            int y = 0;
            for (auto& sh : shelves_on_plate) {
                sh.plan.y = y;
                sh.plan.h = sh.h;
                for (auto& col : sh.plan.columns) {
                    col.y = y;
                    col.h = sh.h;
                    for (auto& sl : col.slices) sl.y = y;
                }
                y += sh.h;
                pp.strips.push_back(std::move(sh.plan));
            }
            plates.push_back(std::move(pp));
            shelves_on_plate.clear();
        };

        for (const auto& pc : pieces) {
            int placed_s = -1;
            for (int s = 0; s < static_cast<int>(shelves_on_plate.size()); ++s) {
                if (pc.h <= shelves_on_plate[s].h && shelves_on_plate[s].used_w + pc.w <= order.sheet_w) {
                    placed_s = s;
                    break;
                }
            }
            if (placed_s < 0) {
                if (cur_y + pc.h > order.sheet_h) {
                    flush_plate();
                    cur_y = 0;
                }
                ActiveShelf ns;
                ns.h = pc.h;
                ns.used_w = 0;
                placed_s = static_cast<int>(shelves_on_plate.size());
                shelves_on_plate.push_back(std::move(ns));
                cur_y += pc.h;
            }
            auto& sh = shelves_on_plate[placed_s];
            int x = sh.used_w;
            ColumnPlan col;
            col.x = x;
            col.y = 0;
            col.w = pc.w;
            col.h = sh.h;
            col.slices.push_back(PlacedItem{pc.item_idx, pc.id, x, 0, pc.w, pc.h});
            sh.used_w += pc.w;
            sh.plan.columns.push_back(std::move(col));
        }
        flush_plate();
        return plates;
    }

    // BFDH shelves + Best-Fit Decreasing plate packing
    std::vector<ActiveShelf> shelves;
    for (const auto& pc : pieces) {
        int best_s = -1;
        int best_rem_w = order.sheet_w + 1;
        for (int s = 0; s < static_cast<int>(shelves.size()); ++s) {
            if (pc.h <= shelves[s].h && shelves[s].used_w + pc.w <= order.sheet_w) {
                int rw = order.sheet_w - (shelves[s].used_w + pc.w);
                if (rw < best_rem_w) {
                    best_rem_w = rw;
                    best_s = s;
                }
            }
        }
        if (best_s < 0) {
            ActiveShelf ns;
            ns.h = pc.h;
            ns.plan.h = pc.h;
            best_s = static_cast<int>(shelves.size());
            shelves.push_back(std::move(ns));
        }
        auto& sh = shelves[best_s];
        int x = sh.used_w;
        ColumnPlan col;
        col.x = x;
        col.y = 0;
        col.w = pc.w;
        col.h = sh.h;
        col.slices.push_back(PlacedItem{pc.item_idx, pc.id, x, 0, pc.w, pc.h});
        sh.used_w += pc.w;
        sh.plan.columns.push_back(std::move(col));
    }

    std::vector<StripPlan> strips;
    strips.reserve(shelves.size());
    for (auto& sh : shelves) {
        sh.plan.h = sh.h;
        strips.push_back(std::move(sh.plan));
    }
    return pack_strips_best_fit(std::move(strips), order.sheet_h);
}

// High-strength Iterative 3-Stage NonExact Guillotine Strip-Peeling + 2-Stack + Subset-Sum Search
std::vector<PlatePlan> solve_guillotine_iterative(
    const OrderSpec& order,
    std::vector<PlatePlan> best_plates,
    int lower_bound,
    bool tight_guide_mode,
    std::chrono::steady_clock::time_point deadline,
    uint32_t seed) {
    const int M = static_cast<int>(order.items.size());
    const int W = order.sheet_w;
    const int H = order.sheet_h;
    int best_bins = count_plates(best_plates);
    if (best_bins <= lower_bound) return best_plates;

    // Also try BFDH + BFD plate packing immediately (<0.5ms)
    {
        auto bfd_cand = solve_ffdh_baseline(order, true);
        int nb = count_plates(bfd_cand);
        if (nb < best_bins) {
            best_bins = nb;
            best_plates = std::move(bfd_cand);
            if (best_bins <= lower_bound) return best_plates;
        }
    }

    const int dp_cap = std::min(W, 1024);
    auto scale_w = [&](int w_actual) -> int {
        if (W <= dp_cap) return w_actual;
        long long num = static_cast<long long>(w_actual) * dp_cap + W - 1;
        return std::max(1, static_cast<int>(num / W));
    };

    std::mt19937_64 rng(seed ^ 0x9e3779b9U);
    std::vector<int> rem(M, 0);
    std::vector<long long> dp_val(dp_cap + 1, 0);
    std::vector<uint8_t> dp_take_buf(static_cast<std::size_t>(256) * static_cast<std::size_t>(dp_cap + 1), 0);

    struct ColUnit {
        int w;
        int h;
        long long area;
        int n;
        int i0;
        int i1;
    };

    int pass = 0;
    while (best_bins > lower_bound && std::chrono::steady_clock::now() < deadline) {
        ++pass;
        for (int t = 0; t < M; ++t) rem[t] = order.items[t].copies;
        int rem_total = 0;
        for (int v : rem) rem_total += v;

        const bool use_stacks = (pass % 3 != 0);
        const bool score_by_width = tight_guide_mode ? (pass % 2 == 0) : (pass % 4 == 0);
        std::uniform_real_distribution<double> noise(0.94, 1.06);

        std::vector<StripPlan> built_strips;
        built_strips.reserve(static_cast<std::size_t>(best_bins * 4));

        while (rem_total > 0) {
            // Build candidate columns from currently remaining items
            std::vector<ColUnit> units;
            units.reserve(static_cast<std::size_t>(M * 2));
            int max_rem_h = 0;
            std::vector<int> cand_heights;

            for (int t = 0; t < M; ++t) {
                if (rem[t] <= 0) continue;
                const auto& it = order.items[t];
                max_rem_h = std::max(max_rem_h, it.h);
                cand_heights.push_back(it.h);
                units.push_back(ColUnit{it.w, it.h, static_cast<long long>(it.w) * it.h, 1, t, -1});
            }

            if (use_stacks && M <= 180) {
                for (int i = 0; i < M; ++i) {
                    if (rem[i] <= 0) continue;
                    for (int j = i; j < M; ++j) {
                        if (i == j && rem[i] < 2) continue;
                        if (rem[j] <= 0) continue;
                        int hc = order.items[i].h + order.items[j].h;
                        if (hc > H) continue;
                        int wc = std::max(order.items[i].w, order.items[j].w);
                        long long ac = static_cast<long long>(order.items[i].w) * order.items[i].h +
                                       static_cast<long long>(order.items[j].w) * order.items[j].h;
                        if (ac * 100 >= static_cast<long long>(wc) * hc * 90) {
                            units.push_back(ColUnit{wc, hc, ac, 2, i, j});
                            cand_heights.push_back(hc);
                        }
                    }
                }
            }

            std::ranges::sort(cand_heights);
            auto uh = std::ranges::unique(cand_heights);
            cand_heights.erase(uh.begin(), uh.end());

            // Evaluate up to 4 candidate anchor heights and pick the highest-efficiency strip!
            std::vector<int> test_anchors = {max_rem_h};
            if (cand_heights.size() >= 2) {
                test_anchors.push_back(cand_heights[rng() % cand_heights.size()]);
                if (tight_guide_mode) {
                    test_anchors.push_back(cand_heights[cand_heights.size() / 2]);
                }
            }

            double best_strip_eff = -1.0;
            std::vector<ColUnit> best_chosen;

            for (int anchor_h : test_anchors) {
                std::vector<int> u_idx;
                for (int u = 0; u < static_cast<int>(units.size()); ++u) {
                    if (units[u].h <= anchor_h && units[u].h * 100 >= anchor_h * (pass % 2 == 0 ? 65 : 0)) {
                        u_idx.push_back(u);
                    }
                }
                if (u_idx.empty()) {
                    for (int u = 0; u < static_cast<int>(units.size()); ++u) {
                        if (units[u].h <= anchor_h) u_idx.push_back(u);
                    }
                }
                if (pass > 2) {
                    std::shuffle(u_idx.begin(), u_idx.end(), rng);
                } else {
                    std::ranges::sort(u_idx, [&](int a, int b2) { return units[a].area > units[b2].area; });
                }

                std::vector<int> local_rem = rem;
                std::array<int, 256> dp_u{};
                std::array<int, 256> dp_sw{};
                std::array<long long, 256> dp_sv{};
                int K = 0;

                for (int u : u_idx) {
                    if (K >= 256) break;
                    const auto& cu = units[u];
                    auto can_take = [&]() {
                        if (cu.n == 1) return local_rem[cu.i0] >= 1;
                        if (cu.i0 == cu.i1) return local_rem[cu.i0] >= 2;
                        return local_rem[cu.i0] >= 1 && local_rem[cu.i1] >= 1;
                    };
                    int max_fit = W / cu.w;
                    int cnt = 0;
                    while (cnt < max_fit && K < 256 && can_take()) {
                        local_rem[cu.i0] -= 1;
                        if (cu.n == 2) local_rem[cu.i1] -= 1;
                        dp_u[K] = u;
                        dp_sw[K] = scale_w(cu.w);
                        double base_v = score_by_width ? static_cast<double>(cu.w * 1000) : static_cast<double>(cu.area);
                        if (pass > 2) base_v *= noise(rng);
                        dp_sv[K] = std::max(1LL, static_cast<long long>(base_v));
                        ++K;
                        ++cnt;
                    }
                }
                if (K == 0) continue;

                const std::size_t stride = static_cast<std::size_t>(dp_cap + 1);
                FlatGrid2D<uint8_t> dp_take(dp_take_buf.data(), static_cast<std::size_t>(K), stride);
                std::ranges::fill(dp_val, 0LL);

                for (int idx = 0; idx < K; ++idx) {
                    std::ranges::fill(dp_take.row(static_cast<std::size_t>(idx)), uint8_t{0});
                    int sw = dp_sw[idx];
                    long long sv = dp_sv[idx];
                    for (int w = dp_cap; w >= sw; --w) {
                        long long cand = dp_val[w - sw] + sv;
                        if (cand > dp_val[w]) {
                            dp_val[w] = cand;
                            dp_take[static_cast<std::size_t>(idx), static_cast<std::size_t>(w)] = 1;
                        }
                    }
                }

                int bw = 0;
                long long bv = 0;
                for (int w = 1; w <= dp_cap; ++w) {
                    if (dp_val[w] > bv) {
                        bv = dp_val[w];
                        bw = w;
                    }
                }
                if (bv <= 0) continue;

                std::vector<ColUnit> chosen;
                int cw = bw;
                int max_ch = 0;
                long long sum_area = 0;
                int sum_w = 0;
                for (int idx = K - 1; idx >= 0; --idx) {
                    if (dp_take[static_cast<std::size_t>(idx), static_cast<std::size_t>(cw)]) {
                        const auto& cu = units[dp_u[idx]];
                        chosen.push_back(cu);
                        max_ch = std::max(max_ch, cu.h);
                        sum_area += cu.area;
                        sum_w += cu.w;
                        cw -= dp_sw[idx];
                    }
                }
                // Greedy residual fill if W > dp_cap left a gap
                if (W > dp_cap && sum_w < W && max_ch > 0) {
                    std::vector<int> tmp_rem = rem;
                    for (const auto& cu : chosen) {
                        tmp_rem[cu.i0] -= 1;
                        if (cu.n == 2) tmp_rem[cu.i1] -= 1;
                    }
                    for (int u : u_idx) {
                        const auto& cu = units[u];
                        if (cu.h > max_ch || sum_w + cu.w > W) continue;
                        while (sum_w + cu.w <= W) {
                            bool ok = (cu.n == 1) ? (tmp_rem[cu.i0] >= 1)
                                    : (cu.i0 == cu.i1 ? tmp_rem[cu.i0] >= 2 : (tmp_rem[cu.i0] >= 1 && tmp_rem[cu.i1] >= 1));
                            if (!ok) break;
                            tmp_rem[cu.i0] -= 1;
                            if (cu.n == 2) tmp_rem[cu.i1] -= 1;
                            chosen.push_back(cu);
                            sum_w += cu.w;
                            sum_area += cu.area;
                        }
                    }
                }

                if (!chosen.empty() && max_ch > 0) {
                    double eff = static_cast<double>(sum_area) / (static_cast<double>(W) * max_ch);
                    if (eff > best_strip_eff) {
                        best_strip_eff = eff;
                        best_chosen = std::move(chosen);
                    }
                }
            }

            if (best_chosen.empty()) break;

            // Materialize StripPlan and deduct from rem
            StripPlan sp;
            int cur_x = 0;
            int sh = 0;
            for (const auto& cu : best_chosen) {
                ColumnPlan cp;
                cp.x = cur_x;
                cp.y = 0;
                if (cu.n == 1) {
                    if (rem[cu.i0] <= 0) continue;
                    rem[cu.i0] -= 1;
                    --rem_total;
                    const auto& it = order.items[cu.i0];
                    cp.w = it.w;
                    cp.slices.push_back(PlacedItem{cu.i0, it.id, cur_x, 0, it.w, it.h});
                    sh = std::max(sh, it.h);
                } else {
                    int a = cu.i0, b2 = cu.i1;
                    if (a == b2 ? rem[a] < 2 : (rem[a] < 1 || rem[b2] < 1)) continue;
                    rem[a] -= 1;
                    rem[b2] -= 1;
                    rem_total -= 2;
                    if (order.items[a].w < order.items[b2].w) std::swap(a, b2);
                    const auto& ita = order.items[a];
                    const auto& itb = order.items[b2];
                    cp.w = std::max(ita.w, itb.w);
                    cp.slices.push_back(PlacedItem{a, ita.id, cur_x, 0, ita.w, ita.h});
                    cp.slices.push_back(PlacedItem{b2, itb.id, cur_x, ita.h, itb.w, itb.h});
                    sh = std::max(sh, ita.h + itb.h);
                }
                cur_x += cp.w;
                sp.columns.push_back(std::move(cp));
            }
            sp.h = sh;
            for (auto& cp : sp.columns) cp.h = sh;
            built_strips.push_back(std::move(sp));
        }

        if (rem_total == 0 && !built_strips.empty()) {
            auto cand_plates = pack_strips_best_fit(std::move(built_strips), H);
            int nb = count_plates(cand_plates);
            if (nb < best_bins) {
                best_bins = nb;
                best_plates = std::move(cand_plates);
            }
        }
    }

    return best_plates;
}

} // namespace

// ---------------------------------------------------------------------------
// Public API Implementations
// ---------------------------------------------------------------------------

std::expected<OrderSpec, ErrorInfo> parse_order_csvs(
    std::string_view bins_csv,
    std::string_view items_csv) {
    OrderSpec ord;

    // Parse bins.csv
    {
        std::size_t pos = 0;
        bool got_header = false;
        bool got_bin = false;
        while (pos < bins_csv.size()) {
            std::size_t nl = bins_csv.find('\n', pos);
            std::string_view line = (nl == std::string_view::npos)
                ? bins_csv.substr(pos)
                : bins_csv.substr(pos, nl - pos);
            pos = (nl == std::string_view::npos) ? bins_csv.size() : nl + 1;
            line = trim_sv(line);
            if (line.empty()) continue;

            auto cols = split_csv_line(line);
            if (!got_header) {
                got_header = true;
                // Detect header vs headerless
                if (!cols.empty() && (cols[0] == "ID" || cols[0] == "id" || cols[0] == "WIDTH")) {
                    continue;
                }
            }
            if (cols.size() < 3) {
                return std::unexpected(ErrorInfo{"BINS_BAD_ROW", "bins.csv requires ID,WIDTH,HEIGHT"});
            }
            auto w = parse_int_sv(cols[1], "bins.WIDTH");
            if (!w) return std::unexpected(w.error());
            auto h = parse_int_sv(cols[2], "bins.HEIGHT");
            if (!h) return std::unexpected(h.error());
            if (*w <= 0 || *h <= 0) {
                return std::unexpected(ErrorInfo{"BINS_NONPOS", "Sheet dimensions must be positive"});
            }
            ord.sheet_w = *w;
            ord.sheet_h = *h;
            got_bin = true;
            break;
        }
        if (!got_bin) {
            return std::unexpected(ErrorInfo{"BINS_EMPTY", "No sheet row found in bins.csv"});
        }
    }

    // Parse items.csv
    {
        std::size_t pos = 0;
        bool got_header = false;
        int w_col = 1, h_col = 2, c_col = 3, id_col = 0;
        while (pos < items_csv.size()) {
            std::size_t nl = items_csv.find('\n', pos);
            std::string_view line = (nl == std::string_view::npos)
                ? items_csv.substr(pos)
                : items_csv.substr(pos, nl - pos);
            pos = (nl == std::string_view::npos) ? items_csv.size() : nl + 1;
            line = trim_sv(line);
            if (line.empty()) continue;

            auto cols = split_csv_line(line);
            if (!got_header) {
                got_header = true;
                bool is_hdr = false;
                for (int idx = 0; idx < static_cast<int>(cols.size()); ++idx) {
                    if (cols[idx] == "ID") { id_col = idx; is_hdr = true; }
                    else if (cols[idx] == "WIDTH") { w_col = idx; is_hdr = true; }
                    else if (cols[idx] == "HEIGHT") { h_col = idx; is_hdr = true; }
                    else if (cols[idx] == "COPIES") { c_col = idx; is_hdr = true; }
                }
                if (is_hdr) continue;
            }
            if (static_cast<int>(cols.size()) <= std::max({id_col, w_col, h_col})) {
                return std::unexpected(ErrorInfo{"ITEMS_BAD_ROW", "items.csv row missing columns"});
            }
            auto id_v = parse_int_sv(cols[id_col], "items.ID");
            if (!id_v) return std::unexpected(id_v.error());
            auto w_v = parse_int_sv(cols[w_col], "items.WIDTH");
            if (!w_v) return std::unexpected(w_v.error());
            auto h_v = parse_int_sv(cols[h_col], "items.HEIGHT");
            if (!h_v) return std::unexpected(h_v.error());
            int copies = 1;
            if (c_col < static_cast<int>(cols.size())) {
                auto c_v = parse_int_sv(cols[c_col], "items.COPIES");
                if (!c_v) return std::unexpected(c_v.error());
                copies = *c_v;
            }
            if (*w_v <= 0 || *h_v <= 0 || copies < 0) {
                return std::unexpected(ErrorInfo{"ITEMS_INVALID_DIM", "Invalid item dimensions or negative copies"});
            }
            if (*w_v > ord.sheet_w || *h_v > ord.sheet_h) {
                return std::unexpected(ErrorInfo{"ITEM_EXCEEDS_SHEET", "Item exceeds parent sheet dimensions (no rotation)"});
            }
            if (copies > 0) {
                ord.items.push_back(ItemSpec{*id_v, *w_v, *h_v, copies});
            }
        }
        if (ord.items.empty()) {
            return std::unexpected(ErrorInfo{"ITEMS_EMPTY", "No positive-demand items in items.csv"});
        }
    }

    return ord;
}

Features extract_features(const OrderSpec& order) {
    Features f;
    const int W = order.sheet_w;
    const int H = order.sheet_h;
    const double sheet_area = static_cast<double>(W) * H;

    // Canonicalize identical (w, h) pairs for split-demand invariance
    std::vector<std::pair<std::pair<int, int>, int>> wh_counts;
    long long total_area = 0;
    int total_copies = 0;
    for (const auto& it : order.items) {
        total_copies += it.copies;
        total_area += static_cast<long long>(it.w) * it.h * it.copies;
        bool merged = false;
        for (auto& kv : wh_counts) {
            if (kv.first.first == it.w && kv.first.second == it.h) {
                kv.second += it.copies;
                merged = true;
                break;
            }
        }
        if (!merged) wh_counts.push_back({{it.w, it.h}, it.copies});
    }

    f.items = total_copies;
    f.distinct_shapes = static_cast<int>(wh_counts.size());
    f.repeat_fraction = 1.0 - static_cast<double>(f.distinct_shapes) / std::max(1, total_copies);

    long long sa_ll = static_cast<long long>(W) * H;
    f.area_lower_bound = static_cast<int>(std::max(1LL, (total_area + sa_ll - 1) / sa_ll));
    f.lower_bound_fill = static_cast<double>(total_area) / (f.area_lower_bound * sheet_area);

    std::vector<int> uniq_w, uniq_h;
    std::vector<double> w_norm, h_norm, a_norm;
    int scale_rep = std::max(1, total_copies / 512);

    for (const auto& [wh, c] : wh_counts) {
        uniq_w.push_back(wh.first);
        uniq_h.push_back(wh.second);
        int rep = (scale_rep > 1) ? std::max(1, c / scale_rep) : c;
        double wn = static_cast<double>(wh.first) / W;
        double hn = static_cast<double>(wh.second) / H;
        double an = (static_cast<double>(wh.first) * wh.second) / sheet_area;
        w_norm.insert(w_norm.end(), rep, wn);
        h_norm.insert(h_norm.end(), rep, hn);
        a_norm.insert(a_norm.end(), rep, an);
    }
    std::ranges::sort(uniq_w);
    uniq_w.erase(std::ranges::unique(uniq_w).begin(), uniq_w.end());
    std::ranges::sort(uniq_h);
    uniq_h.erase(std::ranges::unique(uniq_h).begin(), uniq_h.end());

    f.unique_width_fraction = static_cast<double>(uniq_w.size()) / std::max(1, total_copies);
    f.shape_unique_width_fraction = static_cast<double>(uniq_w.size()) / std::max(1, f.distinct_shapes);
    f.unique_height_fraction = static_cast<double>(uniq_h.size()) / std::max(1, total_copies);
    f.shape_unique_height_fraction = static_cast<double>(uniq_h.size()) / std::max(1, f.distinct_shapes);

    f.width_cv = compute_cv(w_norm);
    f.height_cv = compute_cv(h_norm);
    f.area_cv = compute_cv(a_norm);

    int stackable = 0;
    for (const auto& [wh, c] : wh_counts) {
        if (wh.second <= 0.70 * H) {
            for (const auto& [wh2, c2] : wh_counts) {
                if (wh.second + wh2.second > H) continue;
                if (wh == wh2 && c2 < 2) continue;
                if (std::min(wh.first, wh2.first) >= 0.85 * std::max(wh.first, wh2.first)) {
                    stackable += c;
                    break;
                }
            }
        }
    }
    f.stack2_partner_fraction = static_cast<double>(stackable) / std::max(1, total_copies);
    return f;
}

std::string route_order(const Features& feat) {
    // Exact frozen v2 3-branch geometric router (`v2_tri_cs6_f0.9_0.985_flt4_g1_8_def`)
    if (feat.area_lower_bound <= 6 &&
        feat.items >= 25 &&
        feat.items <= 160 &&
        feat.lower_bound_fill >= 0.90 &&
        feat.lower_bound_fill <= 0.985 &&
        feat.repeat_fraction <= 0.20 &&
        feat.height_cv >= 0.22) {
        return "colstrip";
    }
    if (feat.area_lower_bound <= 8) {
        return "guide1";
    }
    return "default";
}

std::expected<SolveOutput, ErrorInfo> solve_order(
    const OrderSpec& order,
    SolveMode mode,
    double budget_s,
    uint32_t seed) {
    auto t0 = std::chrono::steady_clock::now();
    auto deadline = t0 + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
        std::chrono::duration<double>(std::max(0.02, budget_s - 0.02))
    );

    SolveOutput out;
    out.order = order;
    out.features = extract_features(order);
    out.stats.lower_bound = out.features.area_lower_bound;

    if (mode == SolveMode::BaselineGreedy) {
        out.stats.branch = "baseline_ffdh";
        out.stats.gate_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        out.plates = solve_ffdh_baseline(order, false);
        out.stats.initial_bins = count_plates(out.plates);
        out.stats.final_bins = out.stats.initial_bins;
        out.stats.winning_engine = "baseline_sequential_ffdh";
        out.stats.bnb_status = "SKIPPED";
        out.cut_tree_csv = build_cut_tree_csv(order, out.plates);
        auto ver = verify_cut_tree_csv(order, out.cut_tree_csv);
        if (!ver) return std::unexpected(ver.error());
        out.verification = *ver;
        out.stats.wall_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        return out;
    }

    if (mode == SolveMode::ColStripOnly) {
        out.stats.branch = "colstrip";
    } else if (mode == SolveMode::TightShelf) {
        out.stats.branch = "guide1";
    } else {
        out.stats.branch = route_order(out.features);
    }
    out.stats.gate_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();

    // Stage 0: Fast constructive + brief iterative guillotine incumbent (< 60ms)
    std::vector<PlatePlan> best_plates = solve_ffdh_baseline(order, true);
    int best_bins = count_plates(best_plates);
    out.stats.initial_bins = best_bins;
    out.stats.winning_engine = "initial_bfdh_shelf";

    auto quick_probe_dl = std::min(
        deadline,
        std::chrono::steady_clock::now() + std::chrono::milliseconds(
            out.stats.branch == "colstrip" ? 80 : 350
        )
    );
    if (best_bins > out.stats.lower_bound) {
        best_plates = solve_guillotine_iterative(
            order, std::move(best_plates), out.stats.lower_bound,
            out.stats.branch != "default", quick_probe_dl, seed
        );
        int nb = count_plates(best_plates);
        if (nb < best_bins) {
            best_bins = nb;
            out.stats.initial_bins = best_bins;
            out.stats.winning_engine = "probe_guillotine_shelf";
        }
    }

    // Stage 1-3: Two-Tier Column/Strip Generation + Exact Lagrangian Duals + Bit-Parallel MRV BnB
    // Note: Also invoke ColStrip opportunistically on guide1/default when order is small/medium & still above lower bound!
    const bool run_colstrip = (out.stats.branch == "colstrip") ||
                              (best_bins > out.stats.lower_bound && out.features.items <= 160 && best_bins <= 8);

    if (run_colstrip && best_bins > out.stats.lower_bound && std::chrono::steady_clock::now() < deadline) {
        long long total_area = 0;
        for (const auto& it : order.items) {
            total_area += static_cast<long long>(it.w) * it.h * it.copies;
        }

        auto run_tier = [&](bool tier_stacks, double r1_frac, double r2_frac, double bnb_max_s, uint32_t t_seed) -> std::string {
            auto now = std::chrono::steady_clock::now();
            double rem_s = std::chrono::duration<double>(deadline - now).count();
            if (rem_s < 0.06 || best_bins <= out.stats.lower_bound) return "BUDGET_LOW";

            int target = best_bins - 1;
            long long tot_waste_slack = static_cast<long long>(target) * order.sheet_w * order.sheet_h - total_area;
            long long per_strip_budget = std::max<long long>(
                static_cast<long long>(0.24 * order.sheet_w * order.sheet_h),
                static_cast<long long>(tot_waste_slack * 0.90)
            );

            ColStripPool pool(order, per_strip_budget, tier_stacks);
            out.stats.candidate_columns = static_cast<int>(pool.cols.size());

            double r1_s = std::min(1.10, rem_s * r1_frac);
            int n_r1 = pool.generate_round1(r1_s, t_seed, 11000);
            out.stats.generated_strips_r1 = n_r1;

            auto dual_res = solve_lagrangian_duals(pool, static_cast<double>(best_bins) * order.sheet_h);
            out.stats.dual_lower_bound_height = dual_res.best_dual_lb;

            int dual_bins_lb = static_cast<int>(std::ceil((dual_res.best_dual_lb - 1e-5) / order.sheet_h));
            if (dual_bins_lb > out.stats.lower_bound && !tier_stacks) {
                // Note: single-layer dual LB only bounds non-stacked strips, not 2-stacked layouts;
                // only bail out of Tier 1 if dual_bins_lb >= best_bins!
                if (dual_bins_lb >= best_bins) {
                    return "LP_LOWER_BOUND_BAILOUT";
                }
            }

            if (r2_frac > 0.0) {
                double rem2 = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
                if (rem2 > 0.20) {
                    double r2_s = std::min(0.70, rem2 * r2_frac);
                    int n_r2 = pool.generate_round2_dual(dual_res.duals, r2_s, t_seed + 104729U, 15000);
                    out.stats.generated_strips_r2 = n_r2;
                    dual_res = solve_lagrangian_duals(pool, static_cast<double>(best_bins) * order.sheet_h);
                    out.stats.dual_lower_bound_height = dual_res.best_dual_lb;
                }
            } else {
                out.stats.generated_strips_r2 = n_r1;
            }

            // Retain rich elite strips by reduced cost + top-32 per item (exact waste filter prunes loose strips in BnB)
            const int S = static_cast<int>(pool.strips.size());
            std::vector<int> all_idx(S);
            std::iota(all_idx.begin(), all_idx.end(), 0);
            std::ranges::sort(all_idx, [&](int a, int b2) {
                double ka = dual_res.reduced_costs[a] + (0.20 * pool.strips[a].waste) / std::max(1, order.sheet_w);
                double kb = dual_res.reduced_costs[b2] + (0.20 * pool.strips[b2].waste) / std::max(1, order.sheet_w);
                return ka < kb;
            });

            std::vector<uint8_t> keep(S, 0);
            int max_keep = 1800;
            for (int i = 0; i < std::min(S, max_keep); ++i) keep[all_idx[i]] = 1;

            std::vector<int> per_item_cnt(pool.M, 0);
            for (int idx : all_idx) {
                for (const auto& [t, _] : pool.strips[idx].item_counts) {
                    if (per_item_cnt[t] < 32) {
                        keep[idx] = 1;
                        per_item_cnt[t] += 1;
                    }
                }
            }

            std::vector<int> retained;
            retained.reserve(2000);
            for (int j = 0; j < S; ++j) {
                if (keep[j]) retained.push_back(j);
            }
            out.stats.retained_strips = static_cast<int>(retained.size());

            std::string last_st = "UNKNOWN";
            while (target >= out.stats.lower_bound) {
                double rem_bnb = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
                if (rem_bnb <= 0.04) break;
                double step_s = std::min(bnb_max_s, rem_bnb - (out.stats.branch == "colstrip" ? 0.15 : 0.35));
                if (step_s <= 0.03) step_s = rem_bnb * 0.85;
                auto bnb_dl = std::chrono::steady_clock::now() +
                              std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                                  std::chrono::duration<double>(step_s));

                auto bnb = solve_exact_cover_bnb(pool, retained, target, bnb_dl, t_seed + static_cast<uint32_t>(target * 31));
                out.stats.bnb_nodes += bnb.nodes_explored;
                if (bnb.found) {
                    auto cand_plates = reconstruct_plates_from_placements(order, pool, target, bnb.placements);
                    auto cand_csv = build_cut_tree_csv(order, cand_plates);
                    auto ver = verify_cut_tree_csv(order, cand_csv);
                    if (ver && ver->sheets_used <= target) {
                        best_bins = ver->sheets_used;
                        best_plates = std::move(cand_plates);
                        out.stats.winning_engine = tier_stacks ? "cxx23_colstrip_bnb_stack" : "cxx23_colstrip_bnb_shelf";
                        last_st = "OPTIMAL";
                        target = best_bins - 1;
                        continue;
                    }
                }
                last_st = bnb.proven_infeasible ? "INFEASIBLE" : "UNKNOWN";
                break;
            }
            return last_st;
        };

        auto t1_st = run_tier(false, 0.16, 0.10, 5.0, seed);
        out.stats.bnb_status = t1_st;

        if (best_bins > out.stats.lower_bound) {
            double rem_after_t1 = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
            if (rem_after_t1 > 0.60) {
                auto t2_st = run_tier(true, 0.24, 0.14, 3.2, seed + 7919U);
                if (t2_st == "OPTIMAL") out.stats.bnb_status = "OPTIMAL_STACK";
            }
        }
    }

    // Stage 4: Remaining-budget iterative 3-stage guillotine search
    if (best_bins > out.stats.lower_bound && std::chrono::steady_clock::now() < deadline) {
        auto cand_plates = solve_guillotine_iterative(
            order, best_plates, out.stats.lower_bound,
            out.stats.branch != "default", deadline, seed + 4099U
        );
        int nb = count_plates(cand_plates);
        if (nb < best_bins) {
            best_bins = nb;
            best_plates = std::move(cand_plates);
            out.stats.winning_engine = (out.stats.branch == "guide1")
                ? "cxx23_tight_shelf_search"
                : "cxx23_guillotine_beam";
        }
    }

    out.stats.final_bins = best_bins;
    out.plates = std::move(best_plates);
    out.cut_tree_csv = build_cut_tree_csv(order, out.plates);
    auto ver = verify_cut_tree_csv(order, out.cut_tree_csv);
    if (!ver) return std::unexpected(ver.error());
    out.verification = *ver;
    out.stats.wall_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return out;
}

// ---------------------------------------------------------------------------
// PackingSolver-Compatible 3-Stage NonExact Horizontal Cut-Tree CSV Builder
// ---------------------------------------------------------------------------

std::string build_cut_tree_csv(
    const OrderSpec& order,
    std::span<const PlatePlan> plates) {
    std::ostringstream oss;
    oss << "PLATE_ID,NODE_ID,X,Y,WIDTH,HEIGHT,TYPE,CUT,PARENT,COPIES\n";
    const int W = order.sheet_w;
    const int H = order.sheet_h;
    int nid = 0;

    auto emit = [&](int pid, int node_id, int x, int y, int w, int h, int type, int cut, int parent, int copies) {
        oss << pid << ',' << node_id << ',' << x << ',' << y << ',' << w << ',' << h << ','
            << type << ',' << cut << ',';
        if (parent >= 0) oss << parent;
        oss << ',' << copies << '\n';
    };

    for (std::size_t p_idx = 0; p_idx < plates.size(); ++p_idx) {
        const auto& plate = plates[p_idx];
        const bool is_last_plate = (p_idx + 1 == plates.size());
        const int pid = static_cast<int>(p_idx);
        const int copies = std::max(1, plate.copies);

        int root_id = nid++;
        emit(pid, root_id, 0, 0, W, H, plate.strips.empty() ? -1 : -2, 0, -1, copies);

        int cur_y = 0;
        for (const auto& sp : plate.strips) {
            if (sp.y > cur_y) {
                emit(pid, nid++, 0, cur_y, W, sp.y - cur_y, -1, 1, root_id, copies);
                cur_y = sp.y;
            }
            int strip_id = nid++;
            emit(pid, strip_id, 0, cur_y, W, sp.h, -2, 1, root_id, copies);

            int cur_x = 0;
            for (const auto& col : sp.columns) {
                if (col.x > cur_x) {
                    emit(pid, nid++, cur_x, cur_y, col.x - cur_x, sp.h, -1, 2, strip_id, copies);
                    cur_x = col.x;
                }
                if (col.slices.size() == 1 && col.slices[0].w == col.w && col.slices[0].h == sp.h) {
                    emit(pid, nid++, cur_x, cur_y, col.w, sp.h, col.slices[0].item_idx, 2, strip_id, copies);
                } else {
                    int col_id = nid++;
                    emit(pid, col_id, cur_x, cur_y, col.w, sp.h, -2, 2, strip_id, copies);
                    int sy = cur_y;
                    for (const auto& sl : col.slices) {
                        if (sl.w == col.w) {
                            emit(pid, nid++, cur_x, sy, col.w, sl.h, sl.item_idx, 3, col_id, copies);
                        } else {
                            int slice_id = nid++;
                            emit(pid, slice_id, cur_x, sy, col.w, sl.h, -2, 3, col_id, copies);
                            emit(pid, nid++, cur_x, sy, sl.w, sl.h, sl.item_idx, 4, slice_id, copies);
                            emit(pid, nid++, cur_x + sl.w, sy, col.w - sl.w, sl.h, -1, 4, slice_id, copies);
                        }
                        sy += sl.h;
                    }
                    if (sy < cur_y + sp.h) {
                        emit(pid, nid++, cur_x, sy, col.w, (cur_y + sp.h) - sy, -1, 3, col_id, copies);
                    }
                }
                cur_x += col.w;
            }
            if (cur_x < W) {
                emit(pid, nid++, cur_x, cur_y, W - cur_x, sp.h, -1, 2, strip_id, copies);
            }
            cur_y += sp.h;
        }
        if (cur_y < H) {
            int rem_type = is_last_plate ? -3 : -1;
            emit(pid, nid++, 0, cur_y, W, H - cur_y, rem_type, 1, root_id, copies);
        }
    }
    return oss.str();
}

// ---------------------------------------------------------------------------
// Standalone Pure C++23 Recursive 3-Stage NonExact Horizontal Verifier
// ---------------------------------------------------------------------------

std::expected<VerifyReport, ErrorInfo> verify_cut_tree_csv(
    const OrderSpec& order,
    std::string_view cut_tree_csv) {
    struct Node {
        int plate_id = 0;
        int node_id = 0;
        int x = 0;
        int y = 0;
        int w = 0;
        int h = 0;
        int type = -1;
        int cut = 0;
        int parent = -1;
        int copies = 1;
    };

    std::vector<Node> all_nodes;
    std::size_t pos = 0;
    bool got_header = false;
    int idx_pid = 0, idx_nid = 1, idx_x = 2, idx_y = 3, idx_w = 4, idx_h = 5, idx_type = 6, idx_cut = 7, idx_par = 8, idx_cop = 9;
    while (pos < cut_tree_csv.size()) {
        std::size_t nl = cut_tree_csv.find('\n', pos);
        std::string_view line = (nl == std::string_view::npos)
            ? cut_tree_csv.substr(pos)
            : cut_tree_csv.substr(pos, nl - pos);
        pos = (nl == std::string_view::npos) ? cut_tree_csv.size() : nl + 1;
        line = trim_sv(line);
        if (line.empty()) continue;

        auto cols = split_csv_line(line);
        if (!got_header) {
            got_header = true;
            if (!cols.empty() && cols[0] == "PLATE_ID") {
                idx_cop = -1;
                for (int c_i = 0; c_i < static_cast<int>(cols.size()); ++c_i) {
                    if (cols[c_i] == "PLATE_ID") idx_pid = c_i;
                    else if (cols[c_i] == "NODE_ID") idx_nid = c_i;
                    else if (cols[c_i] == "X") idx_x = c_i;
                    else if (cols[c_i] == "Y") idx_y = c_i;
                    else if (cols[c_i] == "WIDTH") idx_w = c_i;
                    else if (cols[c_i] == "HEIGHT") idx_h = c_i;
                    else if (cols[c_i] == "TYPE") idx_type = c_i;
                    else if (cols[c_i] == "CUT") idx_cut = c_i;
                    else if (cols[c_i] == "PARENT") idx_par = c_i;
                    else if (cols[c_i] == "COPIES") idx_cop = c_i;
                }
                continue;
            }
        }
        if (cols.size() < 9) {
            return std::unexpected(ErrorInfo{"CERT_BAD_ROW", "cut_tree.csv row has fewer than 9 columns"});
        }
        Node nd;
        auto p0 = parse_int_sv(cols[idx_pid], "PLATE_ID"); if (!p0) return std::unexpected(p0.error()); nd.plate_id = *p0;
        auto p1 = parse_int_sv(cols[idx_nid], "NODE_ID");  if (!p1) return std::unexpected(p1.error()); nd.node_id = *p1;
        auto p2 = parse_int_sv(cols[idx_x], "X");          if (!p2) return std::unexpected(p2.error()); nd.x = *p2;
        auto p3 = parse_int_sv(cols[idx_y], "Y");          if (!p3) return std::unexpected(p3.error()); nd.y = *p3;
        auto p4 = parse_int_sv(cols[idx_w], "WIDTH");      if (!p4) return std::unexpected(p4.error()); nd.w = *p4;
        auto p5 = parse_int_sv(cols[idx_h], "HEIGHT");     if (!p5) return std::unexpected(p5.error()); nd.h = *p5;
        auto p6 = parse_int_sv(cols[idx_type], "TYPE");    if (!p6) return std::unexpected(p6.error()); nd.type = *p6;
        if (nd.type == -4) continue;
        auto p7 = parse_int_sv(cols[idx_cut], "CUT");      if (!p7) return std::unexpected(p7.error()); nd.cut = *p7;
        if (idx_par >= 0 && idx_par < static_cast<int>(cols.size()) && !cols[idx_par].empty()) {
            auto p8 = parse_int_sv(cols[idx_par], "PARENT");
            if (!p8) return std::unexpected(p8.error());
            nd.parent = *p8;
        }
        if (idx_cop >= 0 && idx_cop < static_cast<int>(cols.size()) && !cols[idx_cop].empty()) {
            auto p9 = parse_int_sv(cols[idx_cop], "COPIES");
            if (!p9) return std::unexpected(p9.error());
            nd.copies = *p9;
        }
        all_nodes.push_back(nd);
    }

    if (all_nodes.empty()) {
        return std::unexpected(ErrorInfo{"CERT_EMPTY", "Empty cut_tree.csv"});
    }

    const int W = order.sheet_w;
    const int H = order.sheet_h;
    const int M = static_cast<int>(order.items.size());

    std::vector<int> plate_ids;
    for (const auto& n : all_nodes) plate_ids.push_back(n.plate_id);
    std::ranges::sort(plate_ids);
    plate_ids.erase(std::ranges::unique(plate_ids).begin(), plate_ids.end());

    std::vector<int> produced(M, 0);
    int total_sheets = 0;
    int max_eff_stages = 0;
    int last_residual_h = 0;

    for (std::size_t pi = 0; pi < plate_ids.size(); ++pi) {
        int pid = plate_ids[pi];
        std::vector<Node> pnodes;
        for (const auto& n : all_nodes) {
            if (n.plate_id == pid) pnodes.push_back(n);
        }

        std::unordered_map<int, Node> by_id;
        std::unordered_map<int, std::vector<Node>> children;
        std::vector<Node> roots;

        for (const auto& n : pnodes) {
            by_id[n.node_id] = n;
            if (n.parent < 0) roots.push_back(n);
            else children[n.parent].push_back(n);
        }
        if (roots.size() != 1) {
            return std::unexpected(ErrorInfo{"CERT_ROOT", "Plate must have exactly one root node"});
        }
        const Node& root = roots[0];
        if (root.x != 0 || root.y != 0 || root.w != W || root.h != H || root.cut != 0) {
            return std::unexpected(ErrorInfo{"CERT_ROOT_DIM", "Root dimensions/cut do not match parent sheet"});
        }
        if (root.copies < 1) {
            return std::unexpected(ErrorInfo{"CERT_COPIES", "Plate copies must be >= 1"});
        }
        total_sheets += root.copies;

        int max_real_depth = 0;
        struct Rect { int x, y, w, h; };
        std::vector<Rect> placed;

        for (const auto& n : pnodes) {
            if (n.w < 0 || n.h < 0 || n.x < 0 || n.y < 0 || n.x + n.w > W || n.y + n.h > H) {
                return std::unexpected(ErrorInfo{"CERT_OOB", "Node exceeds parent sheet bounds"});
            }
            if (n.parent >= 0) {
                auto pit = by_id.find(n.parent);
                if (pit == by_id.end()) {
                    return std::unexpected(ErrorInfo{"CERT_ORPHAN", "Missing parent node"});
                }
                const Node& par = pit->second;
                if (n.cut != par.cut + 1) {
                    return std::unexpected(ErrorInfo{"CERT_DEPTH_JUMP", "Cut depth must increment by 1"});
                }
                if (n.x != par.x || n.y != par.y || n.w != par.w || n.h != par.h) {
                    max_real_depth = std::max(max_real_depth, n.cut);
                }
            }

            auto cit = children.find(n.node_id);
            if (cit != children.end() && !cit->second.empty()) {
                auto& ch = cit->second;
                int next_d = n.cut + 1;
                if (next_d % 2 == 1) {
                    // Horizontal cut stage: partition along Y
                    std::ranges::sort(ch, [](const Node& a, const Node& b2) { return a.y < b2.y; });
                    int cy = n.y;
                    for (const auto& c : ch) {
                        if (c.x != n.x || c.w != n.w || c.y != cy) {
                            return std::unexpected(ErrorInfo{"CERT_HCUT", "Horizontal cut partition mismatch"});
                        }
                        cy += c.h;
                    }
                    if (cy > n.y + n.h) {
                        return std::unexpected(ErrorInfo{"CERT_HCUT_OVER", "Horizontal children exceed parent height"});
                    }
                } else {
                    // Vertical cut stage: partition along X
                    std::ranges::sort(ch, [](const Node& a, const Node& b2) { return a.x < b2.x; });
                    int cx = n.x;
                    for (const auto& c : ch) {
                        if (c.y != n.y || c.h != n.h || c.x != cx) {
                            return std::unexpected(ErrorInfo{"CERT_VCUT", "Vertical cut partition mismatch"});
                        }
                        cx += c.w;
                    }
                    if (cx > n.x + n.w) {
                        return std::unexpected(ErrorInfo{"CERT_VCUT_OVER", "Vertical children exceed parent width"});
                    }
                }
            } else {
                // Leaf node
                if (n.type >= 0 && n.node_id != root.node_id) {
                    if (n.type >= M) {
                        return std::unexpected(ErrorInfo{"CERT_BAD_TYPE", "Unknown item TYPE index"});
                    }
                    const auto& spec = order.items[n.type];
                    if (n.w != spec.w || n.h != spec.h) {
                        return std::unexpected(ErrorInfo{"CERT_ITEM_DIM", "Leaf item dimensions do not match order item (no rotation allowed)"});
                    }
                    produced[n.type] += root.copies;
                    placed.push_back(Rect{n.x, n.y, n.w, n.h});
                } else if (n.type == -3 && (pi + 1 == plate_ids.size()) && n.cut == 1) {
                    last_residual_h = n.h;
                }
            }
        }

        // Pairwise non-overlap check
        for (std::size_t i = 0; i < placed.size(); ++i) {
            for (std::size_t j = i + 1; j < placed.size(); ++j) {
                const auto& a = placed[i];
                const auto& b2 = placed[j];
                bool disjoint = (a.x + a.w <= b2.x) || (b2.x + b2.w <= a.x) ||
                                (a.y + a.h <= b2.y) || (b2.y + b2.h <= a.y);
                if (!disjoint) {
                    return std::unexpected(ErrorInfo{"CERT_OVERLAP", "Overlapping items detected on plate"});
                }
            }
        }

        // Exact mirror of PackingSolver Solution::number_of_stages() for NonExact guillotine
        int eff_stages = max_real_depth;
        if (max_real_depth > 0) {
            bool all_non_exact = true;
            bool found = false;
            for (const auto& n : pnodes) {
                if (n.type == -2 && n.cut == max_real_depth - 1) {
                    found = true;
                    int non_res = 0, item_ch = 0;
                    if (auto it = children.find(n.node_id); it != children.end()) {
                        for (const auto& c : it->second) {
                            if (c.type != -3) {
                                ++non_res;
                                if (c.type >= 0) ++item_ch;
                            }
                        }
                    }
                    if (!(item_ch == 1 && non_res <= 2)) {
                        all_non_exact = false;
                        break;
                    }
                }
            }
            if (found && all_non_exact) eff_stages = max_real_depth - 1;
        }
        if (eff_stages > 3) {
            return std::unexpected(ErrorInfo{"CERT_STAGES", "Exceeded 3-stage NonExact guillotine limit"});
        }
        max_eff_stages = std::max(max_eff_stages, eff_stages);
    }

    long long total_item_area = 0;
    for (int t = 0; t < M; ++t) {
        if (produced[t] != order.items[t].copies) {
            return std::unexpected(ErrorInfo{
                "CERT_DEMAND",
                "Item demand mismatch at index " + std::to_string(t) +
                ": produced=" + std::to_string(produced[t]) +
                " expected=" + std::to_string(order.items[t].copies)
            });
        }
        total_item_area += static_cast<long long>(order.items[t].w) * order.items[t].h * order.items[t].copies;
    }

    long long total_sheet_area = static_cast<long long>(total_sheets) * W * H;
    long long waste_area = total_sheet_area - total_item_area;

    VerifyReport rep;
    rep.valid = true;
    rep.sheets_used = total_sheets;
    rep.distinct_plates = static_cast<int>(plate_ids.size());
    rep.max_effective_stages = max_eff_stages;
    rep.sheet_w = W;
    rep.sheet_h = H;
    rep.total_item_area = total_item_area;
    rep.total_sheet_area = total_sheet_area;
    rep.waste_area = waste_area;
    rep.utilization_pct = std::round((1000000.0 * total_item_area) / std::max(1LL, total_sheet_area)) / 10000.0;
    rep.waste_pct = std::round((1000000.0 * waste_area) / std::max(1LL, total_sheet_area)) / 10000.0;
    rep.last_plate_residual_height = last_residual_h;
    return rep;
}

// ---------------------------------------------------------------------------
// Rich JSON Serializers (Zero External JSON Library Needed)
// ---------------------------------------------------------------------------

namespace {

std::string json_escape(std::string_view sv) {
    std::string out;
    out.reserve(sv.size() + 8);
    for (char ch : sv) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out.push_back(ch); break;
        }
    }
    return out;
}

std::string fmt_f4(double v) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.4f", v);
    return buf;
}

} // namespace

std::string serialize_verify_json(const VerifyReport& rep, std::string_view order_name) {
    std::ostringstream o;
    o << "{"
      << "\"valid\":" << (rep.valid ? "true" : "false") << ","
      << "\"order_name\":\"" << json_escape(order_name) << "\","
      << "\"sheets_used\":" << rep.sheets_used << ","
      << "\"distinct_plates\":" << rep.distinct_plates << ","
      << "\"max_effective_stages\":" << rep.max_effective_stages << ","
      << "\"cut_type\":\"NonExact\","
      << "\"first_stage_orientation\":\"Horizontal\","
      << "\"oriented_no_rotation\":true,"
      << "\"kerf_thickness\":0,"
      << "\"sheet_width\":" << rep.sheet_w << ","
      << "\"sheet_height\":" << rep.sheet_h << ","
      << "\"total_item_area\":" << rep.total_item_area << ","
      << "\"total_sheet_area\":" << rep.total_sheet_area << ","
      << "\"waste_area\":" << rep.waste_area << ","
      << "\"utilization_pct\":" << fmt_f4(rep.utilization_pct) << ","
      << "\"waste_pct\":" << fmt_f4(rep.waste_pct) << ","
      << "\"last_plate_residual_height\":" << rep.last_plate_residual_height
      << "}";
    return o.str();
}

std::string serialize_solve_json(const SolveOutput& out, std::string_view order_name) {
    std::ostringstream o;
    o << "{"
      << "\"ok\":true,"
      << "\"engine_standard\":\"C++23\","
      << "\"order_name\":\"" << json_escape(order_name) << "\","
      << "\"sheet_width\":" << out.order.sheet_w << ","
      << "\"sheet_height\":" << out.order.sheet_h << ","
      << "\"sheets_used\":" << out.stats.final_bins << ","
      << "\"initial_bins\":" << out.stats.initial_bins << ","
      << "\"lower_bound\":" << out.stats.lower_bound << ","
      << "\"proven_optimal\":" << (out.stats.final_bins == out.stats.lower_bound ? "true" : "false") << ","
      << "\"branch\":\"" << json_escape(out.stats.branch) << "\","
      << "\"winning_engine\":\"" << json_escape(out.stats.winning_engine) << "\","
      << "\"bnb_status\":\"" << json_escape(out.stats.bnb_status) << "\","
      << "\"bnb_nodes\":" << out.stats.bnb_nodes << ","
      << "\"generated_strips_r1\":" << out.stats.generated_strips_r1 << ","
      << "\"generated_strips_r2\":" << out.stats.generated_strips_r2 << ","
      << "\"retained_strips\":" << out.stats.retained_strips << ","
      << "\"candidate_columns\":" << out.stats.candidate_columns << ","
      << "\"gate_ms\":" << fmt_f4(out.stats.gate_ms) << ","
      << "\"wall_ms\":" << fmt_f4(out.stats.wall_ms) << ","
      << "\"utilization_pct\":" << fmt_f4(out.verification.utilization_pct) << ","
      << "\"waste_pct\":" << fmt_f4(out.verification.waste_pct) << ","
      << "\"waste_area\":" << out.verification.waste_area << ","
      << "\"total_item_area\":" << out.verification.total_item_area << ","
      << "\"total_sheet_area\":" << out.verification.total_sheet_area << ","
      << "\"features\":{"
      << "\"items\":" << out.features.items << ","
      << "\"distinct_shapes\":" << out.features.distinct_shapes << ","
      << "\"repeat_fraction\":" << fmt_f4(out.features.repeat_fraction) << ","
      << "\"area_lower_bound\":" << out.features.area_lower_bound << ","
      << "\"lower_bound_fill\":" << fmt_f4(out.features.lower_bound_fill) << ","
      << "\"height_cv\":" << fmt_f4(out.features.height_cv) << ","
      << "\"width_cv\":" << fmt_f4(out.features.width_cv) << ","
      << "\"stack2_partner_fraction\":" << fmt_f4(out.features.stack2_partner_fraction)
      << "},"
      << "\"verification\":" << serialize_verify_json(out.verification, order_name) << ","
      << "\"cut_tree_csv\":\"" << json_escape(out.cut_tree_csv) << "\","
      << "\"plates\":[";

    for (std::size_t pi = 0; pi < out.plates.size(); ++pi) {
        if (pi > 0) o << ",";
        const auto& pl = out.plates[pi];
        long long used_a = 0;
        int n_parts = 0;
        for (const auto& sp : pl.strips) {
            for (const auto& cp : sp.columns) {
                for (const auto& sl : cp.slices) {
                    used_a += static_cast<long long>(sl.w) * sl.h;
                    ++n_parts;
                }
            }
        }
        double p_util = (100.0 * used_a) / (static_cast<double>(out.order.sheet_w) * out.order.sheet_h);
        o << "{"
          << "\"plate_id\":" << pl.plate_id << ","
          << "\"copies\":" << pl.copies << ","
          << "\"sheet_width\":" << out.order.sheet_w << ","
          << "\"sheet_height\":" << out.order.sheet_h << ","
          << "\"part_count\":" << n_parts << ","
          << "\"used_area\":" << used_a << ","
          << "\"utilization_pct\":" << fmt_f4(p_util) << ","
          << "\"strips\":[";
        for (std::size_t si = 0; si < pl.strips.size(); ++si) {
            if (si > 0) o << ",";
            const auto& sp = pl.strips[si];
            o << "{\"y\":" << sp.y << ",\"h\":" << sp.h << ",\"columns\":[";
            for (std::size_t ci = 0; ci < sp.columns.size(); ++ci) {
                if (ci > 0) o << ",";
                const auto& cp = sp.columns[ci];
                o << "{\"x\":" << cp.x << ",\"y\":" << cp.y << ",\"w\":" << cp.w << ",\"h\":" << cp.h << ",\"slices\":[";
                for (std::size_t li = 0; li < cp.slices.size(); ++li) {
                    if (li > 0) o << ",";
                    const auto& sl = cp.slices[li];
                    o << "{\"item_idx\":" << sl.item_idx
                      << ",\"item_id\":" << sl.item_id
                      << ",\"x\":" << sl.x
                      << ",\"y\":" << sl.y
                      << ",\"w\":" << sl.w
                      << ",\"h\":" << sl.h << "}";
                }
                o << "]}";
            }
            o << "]}";
        }
        o << "]}";
    }
    o << "]}";
    return o.str();
}

// ---------------------------------------------------------------------------
// Bi-Objective Factory Cost & Pattern-Minimization Pareto Engine (Pure C++23)
// ---------------------------------------------------------------------------

namespace {

int count_plate_strokes_exact(int W, int H, const PlatePlan& plate) noexcept {
    int cuts = 0;
    int root_children = 0;
    int cur_y = 0;
    for (const auto& sp : plate.strips) {
        if (sp.y > cur_y) {
            ++root_children;
            cur_y = sp.y;
        }
        ++root_children;

        int strip_children = 0;
        int cur_x = 0;
        for (const auto& col : sp.columns) {
            if (col.x > cur_x) {
                ++strip_children;
                cur_x = col.x;
            }
            ++strip_children;
            if (!(col.slices.size() == 1 && col.slices[0].w == col.w && col.slices[0].h == sp.h)) {
                int col_children = 0;
                int sy = cur_y;
                for (const auto& sl : col.slices) {
                    ++col_children;
                    if (sl.w < col.w) {
                        cuts += 1; // Stage-4 vertical trim
                    }
                    sy += sl.h;
                }
                if (sy < cur_y + sp.h) {
                    ++col_children;
                }
                if (col_children >= 2) cuts += (col_children - 1);
            }
            cur_x += col.w;
        }
        if (cur_x < W) ++strip_children;
        if (strip_children >= 2) cuts += (strip_children - 1);
        cur_y += sp.h;
    }
    if (cur_y < H) ++root_children;
    if (root_children >= 2) cuts += (root_children - 1);
    return cuts;
}

std::string canonical_plate_key(const PlatePlan& plate) {
    std::ostringstream oss;
    for (const auto& sp : plate.strips) {
        oss << "S:" << sp.y << ':' << sp.h << '[';
        for (const auto& col : sp.columns) {
            oss << "C:" << col.x << ':' << col.w << '(';
            for (const auto& sl : col.slices) {
                oss << sl.item_idx << ',' << sl.w << ',' << sl.h << ';';
            }
            oss << ')';
        }
        oss << ']';
    }
    return oss.str();
}

std::vector<PlatePlan> merge_identical_plates(std::span<const PlatePlan> plates) {
    std::vector<PlatePlan> merged;
    std::vector<std::string> keys;
    for (const auto& pl : plates) {
        if (pl.copies <= 0 || pl.strips.empty()) continue;
        std::string k = canonical_plate_key(pl);
        bool found = false;
        for (std::size_t i = 0; i < keys.size(); ++i) {
            if (keys[i] == k) {
                merged[i].copies += pl.copies;
                found = true;
                break;
            }
        }
        if (!found) {
            PlatePlan copy_pl = pl;
            copy_pl.plate_id = static_cast<int>(merged.size());
            merged.push_back(std::move(copy_pl));
            keys.push_back(std::move(k));
        }
    }
    for (std::size_t i = 0; i < merged.size(); ++i) {
        merged[i].plate_id = static_cast<int>(i);
    }
    return merged;
}

PlanEconomics compute_plan_economics(
    int W, int H,
    std::span<const PlatePlan> plates,
    const CostModel& cost) noexcept {
    PlanEconomics ec;
    const int s_max = std::max(1, cost.max_stack_sheets);
    ec.distinct_plates = static_cast<int>(plates.size());
    for (const auto& pl : plates) {
        int c = std::max(1, pl.copies);
        int stacks = (c + s_max - 1) / s_max;
        int c_strokes = count_plate_strokes_exact(W, H, pl);
        ec.sheets_used += c;
        ec.clamp_stacks += stacks;
        ec.total_strokes += stacks * c_strokes;
        ec.unstacked_strokes += c * c_strokes;
    }
    double rate_min = cost.machine_rate_rmb_per_hour / 60.0;
    ec.setup_time_min = ec.distinct_plates * cost.setup_minutes_per_pattern;
    ec.cut_time_min = ec.total_strokes * (cost.stroke_seconds / 60.0);
    ec.total_time_min = ec.setup_time_min + ec.cut_time_min;
    ec.paper_cost_rmb = ec.sheets_used * cost.sheet_cost_rmb;
    ec.setup_cost_rmb = ec.setup_time_min * rate_min;
    ec.cut_cost_rmb = ec.cut_time_min * rate_min;
    ec.total_cost_rmb = ec.paper_cost_rmb + ec.setup_cost_rmb + ec.cut_cost_rmb;
    return ec;
}

PlatePlan pack_single_plate_weighted(
    const OrderSpec& order,
    std::span<const int> avail,
    std::span<const double> weights) {
    const int W = order.sheet_w;
    const int H = order.sheet_h;
    const int M = static_cast<int>(order.items.size());

    std::vector<int> cand_hs;
    for (int i = 0; i < M; ++i) {
        if (avail[i] > 0 && order.items[i].h <= H) {
            cand_hs.push_back(order.items[i].h);
        }
    }
    std::ranges::sort(cand_hs, std::greater<int>{});
    cand_hs.erase(std::ranges::unique(cand_hs).begin(), cand_hs.end());

    PlatePlan out_plate;
    out_plate.plate_id = 0;
    out_plate.copies = 1;
    if (cand_hs.empty()) return out_plate;

    std::vector<int> rem(avail.begin(), avail.end());
    int cur_y = 0;

    while (cur_y < H) {
        int rem_h = H - cur_y;
        double best_score = -1.0;
        StripPlan best_sp;
        std::vector<int> best_rem;

        int step = std::max(1, static_cast<int>(cand_hs.size()) / 16);
        for (std::size_t hi = 0; hi < cand_hs.size(); hi += static_cast<std::size_t>(step)) {
            int sh = cand_hs[hi];
            if (sh > rem_h) continue;

            struct Unit {
                double density;
                int idx;
                int stack_k;
                int w;
                int h;
            };
            std::vector<Unit> units;
            for (int i = 0; i < M; ++i) {
                if (rem[i] <= 0 || order.items[i].w > W || order.items[i].h > sh) continue;
                int max_k = std::min({rem[i], sh / order.items[i].h, 2});
                for (int k = 1; k <= max_k; ++k) {
                    double val = k * weights[i];
                    units.push_back(Unit{val / order.items[i].w, i, k, order.items[i].w, k * order.items[i].h});
                }
            }
            if (units.empty()) continue;
            std::ranges::sort(units, [](const Unit& a, const Unit& b2) {
                if (std::abs(a.density - b2.density) > 1e-9) return a.density > b2.density;
                return a.w > b2.w;
            });

            std::vector<int> loc_rem = rem;
            int cur_x = 0;
            int actual_sh = 0;
            double strip_val = 0.0;
            StripPlan sp;
            sp.y = cur_y;

            for (const auto& u : units) {
                while (loc_rem[u.idx] >= u.stack_k && cur_x + u.w <= W) {
                    ColumnPlan cp;
                    cp.x = cur_x;
                    cp.y = cur_y;
                    cp.w = u.w;
                    int sy = cur_y;
                    for (int s = 0; s < u.stack_k; ++s) {
                        const auto& spec = order.items[u.idx];
                        cp.slices.push_back(PlacedItem{u.idx, spec.id, cur_x, sy, spec.w, spec.h});
                        sy += spec.h;
                    }
                    loc_rem[u.idx] -= u.stack_k;
                    cur_x += u.w;
                    actual_sh = std::max(actual_sh, u.h);
                    strip_val += u.stack_k * weights[u.idx];
                    sp.columns.push_back(std::move(cp));
                }
            }
            if (!sp.columns.empty() && actual_sh > 0) {
                sp.h = actual_sh;
                for (auto& cp : sp.columns) cp.h = actual_sh;
                double sc = strip_val / (static_cast<double>(W) * actual_sh);
                if (sc > best_score) {
                    best_score = sc;
                    best_sp = std::move(sp);
                    best_rem = std::move(loc_rem);
                }
            }
        }

        if (best_score < 0.0 || best_sp.columns.empty()) break;
        cur_y += best_sp.h;
        rem = std::move(best_rem);
        out_plate.strips.push_back(std::move(best_sp));
    }

    return out_plate;
}

void serialize_plates_array(std::ostringstream& o, const OrderSpec& order, std::span<const PlatePlan> plates) {
    o << "[";
    for (std::size_t pi = 0; pi < plates.size(); ++pi) {
        if (pi > 0) o << ",";
        const auto& pl = plates[pi];
        long long used_a = 0;
        int n_parts = 0;
        for (const auto& sp : pl.strips) {
            for (const auto& cp : sp.columns) {
                for (const auto& sl : cp.slices) {
                    used_a += static_cast<long long>(sl.w) * sl.h;
                    ++n_parts;
                }
            }
        }
        double p_util = (100.0 * used_a) / (static_cast<double>(order.sheet_w) * order.sheet_h);
        o << "{"
          << "\"plate_id\":" << pl.plate_id << ","
          << "\"copies\":" << pl.copies << ","
          << "\"sheet_width\":" << order.sheet_w << ","
          << "\"sheet_height\":" << order.sheet_h << ","
          << "\"part_count\":" << n_parts << ","
          << "\"used_area\":" << used_a << ","
          << "\"cuts_per_stack\":" << count_plate_strokes_exact(order.sheet_w, order.sheet_h, pl) << ","
          << "\"utilization_pct\":" << fmt_f4(p_util) << ","
          << "\"strips\":[";
        for (std::size_t si = 0; si < pl.strips.size(); ++si) {
            if (si > 0) o << ",";
            const auto& sp = pl.strips[si];
            o << "{\"y\":" << sp.y << ",\"h\":" << sp.h << ",\"columns\":[";
            for (std::size_t ci = 0; ci < sp.columns.size(); ++ci) {
                if (ci > 0) o << ",";
                const auto& cp = sp.columns[ci];
                o << "{\"x\":" << cp.x << ",\"y\":" << cp.y << ",\"w\":" << cp.w << ",\"h\":" << cp.h << ",\"slices\":[";
                for (std::size_t li = 0; li < cp.slices.size(); ++li) {
                    if (li > 0) o << ",";
                    const auto& sl = cp.slices[li];
                    o << "{\"item_idx\":" << sl.item_idx
                      << ",\"item_id\":" << sl.item_id
                      << ",\"x\":" << sl.x
                      << ",\"y\":" << sl.y
                      << ",\"w\":" << sl.w
                      << ",\"h\":" << sl.h << "}";
                }
                o << "]}";
            }
            o << "]}";
        }
        o << "]}";
    }
    o << "]";
}

} // namespace

std::expected<ParetoOutput, ErrorInfo> solve_pareto_order(
    const OrderSpec& order,
    const CostModel& cost,
    double budget_s,
    uint32_t seed) {
    auto t0 = std::chrono::steady_clock::now();
    auto deadline = t0 + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
        std::chrono::duration<double>(std::max(0.10, budget_s - 0.03))
    );

    ParetoOutput out;
    out.order = order;
    out.features = extract_features(order);
    out.cost_model = cost;

    const int W = order.sheet_w;
    const int H = order.sheet_h;
    const int M = static_cast<int>(order.items.size());
    const double sheet_area = static_cast<double>(W) * H;

    int max_copies = 0;
    std::vector<int> demands(M, 0);
    for (int i = 0; i < M; ++i) {
        demands[i] = order.items[i].copies;
        max_copies = std::max(max_copies, demands[i]);
    }

    struct CandPat {
        PlatePlan plate; // copies == 1 template
        std::vector<int> counts;
        int strokes = 0;
        long long used_area = 0;
    };
    std::vector<CandPat> pool;

    auto register_plate = [&](const PlatePlan& pl) -> int {
        std::vector<int> cnt(M, 0);
        long long ua = 0;
        for (const auto& sp : pl.strips) {
            for (const auto& cp : sp.columns) {
                for (const auto& sl : cp.slices) {
                    if (sl.item_idx >= 0 && sl.item_idx < M) {
                        cnt[sl.item_idx] += 1;
                        ua += static_cast<long long>(sl.w) * sl.h;
                    }
                }
            }
        }
        bool any = false;
        for (int i = 0; i < M; ++i) {
            if (cnt[i] > demands[i]) return -1;
            if (cnt[i] > 0) any = true;
        }
        if (!any) return -1;

        int st = count_plate_strokes_exact(W, H, pl);
        for (std::size_t idx = 0; idx < pool.size(); ++idx) {
            if (pool[idx].counts == cnt) {
                if (st < pool[idx].strokes) {
                    PlatePlan tpl = pl;
                    tpl.copies = 1;
                    pool[idx].plate = std::move(tpl);
                    pool[idx].strokes = st;
                }
                return static_cast<int>(idx);
            }
        }
        PlatePlan tpl = pl;
        tpl.copies = 1;
        pool.push_back(CandPat{std::move(tpl), std::move(cnt), st, ua});
        return static_cast<int>(pool.size() - 1);
    };

    std::vector<std::vector<PlatePlan>> candidate_solutions;
    auto register_solution = [&](std::span<const PlatePlan> raw_plates) {
        auto merged = merge_identical_plates(raw_plates);
        std::vector<int> prod(M, 0);
        for (const auto& pl : merged) {
            int pidx = register_plate(pl);
            if (pidx < 0) return;
            for (int i = 0; i < M; ++i) {
                prod[i] += pool[pidx].counts[i] * pl.copies;
            }
        }
        if (prod == demands) {
            candidate_solutions.push_back(std::move(merged));
        }
    };

    // Stage 1: Strong Min-Paper Anchor
    double s1_budget = std::max(0.08, budget_s * (max_copies == 1 ? 0.90 : 0.48));
    auto anchor = solve_order(order, SolveMode::MillRouter, s1_budget, seed);
    if (!anchor) return std::unexpected(anchor.error());
    register_solution(anchor->plates);

    // Also include fast pure-shelf baseline if it happens to use fewer strokes on singletons
    auto ffdh_sol = solve_order(order, SolveMode::BaselineGreedy, 0.05, seed);
    if (ffdh_sol) register_solution(ffdh_sol->plates);

    // Stage 2: Multi-Multiplicity Batch Peeling & GCD Reduction (when repeated demands exist)
    if (max_copies >= 2 && std::chrono::steady_clock::now() < deadline) {
        // 2a. GCD scaling
        int g = 0;
        for (int d : demands) g = std::gcd(g, d);
        if (g > 1) {
            OrderSpec sub = order;
            for (auto& it : sub.items) it.copies /= g;
            double rem_s = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
            if (rem_s > 0.12) {
                auto sub_res = solve_order(sub, SolveMode::MillRouter, std::min(0.8, rem_s * 0.28), seed + 17U);
                if (sub_res) {
                    auto scaled = merge_identical_plates(sub_res->plates);
                    for (auto& pl : scaled) pl.copies *= g;
                    register_solution(scaled);
                }
            }
        }

        // 2b. Clean 2-stage homogeneous single-SKU plates
        for (int i = 0; i < M; ++i) {
            const auto& spec = order.items[i];
            int nx = W / spec.w;
            int ny = H / spec.h;
            int cap = std::min(demands[i], nx * ny);
            if (cap >= 1) {
                std::vector<int> takes{cap};
                for (int div = 1; div <= std::min(demands[i], 64); ++div) {
                    if (demands[i] % div == 0) {
                        int q = demands[i] / div;
                        if (q >= 1 && q <= cap) takes.push_back(q);
                    }
                }
                std::ranges::sort(takes);
                takes.erase(std::ranges::unique(takes).begin(), takes.end());
                for (int tk : takes) {
                    PlatePlan hp;
                    hp.copies = 1;
                    int rem_tk = tk;
                    int cy = 0;
                    while (rem_tk > 0 && cy + spec.h <= H) {
                        int row_n = std::min(nx, rem_tk);
                        StripPlan sp;
                        sp.y = cy;
                        sp.h = spec.h;
                        for (int c = 0; c < row_n; ++c) {
                            ColumnPlan cp;
                            cp.x = c * spec.w;
                            cp.y = cy;
                            cp.w = spec.w;
                            cp.h = spec.h;
                            cp.slices.push_back(PlacedItem{i, spec.id, c * spec.w, cy, spec.w, spec.h});
                            sp.columns.push_back(std::move(cp));
                        }
                        hp.strips.push_back(std::move(sp));
                        cy += spec.h;
                        rem_tk -= row_n;
                    }
                    if (rem_tk == 0) register_plate(hp);
                }
            }
        }

        // 2c. Multi-schedule High-Multiplicity Batch Peeling
        struct PeelCfg { double min_fill; double div_b; double scar_b; int max_steps; };
        constexpr std::array<PeelCfg, 8> cfgs{{
            {0.91, 0.05, 0.02, 14},
            {0.86, 0.10, 0.04, 14},
            {0.82, 0.18, 0.05, 12},
            {0.70, 0.25, 0.12, 10},
            {0.55, 0.35, 0.18, 8},
            {0.40, 0.45, 0.22, 6},
            {0.26, 0.50, 0.25, 5},
            {0.75, 0.00, 0.30, 10},
        }};

        for (const auto& cfg : cfgs) {
            if (std::chrono::steady_clock::now() >= deadline) break;
            std::vector<int> rem = demands;
            std::vector<PlatePlan> traj;

            for (int step = 0; step < cfg.max_steps; ++step) {
                if (std::chrono::steady_clock::now() >= deadline) break;
                int max_r = 0;
                long long rem_area = 0;
                for (int i = 0; i < M; ++i) {
                    max_r = std::max(max_r, rem[i]);
                    rem_area += static_cast<long long>(rem[i]) * order.items[i].w * order.items[i].h;
                }
                if (max_r < 2 || static_cast<double>(rem_area) <= 1.30 * sheet_area) break;

                std::vector<int> cand_ms;
                for (int i = 0; i < M; ++i) {
                    if (rem[i] >= 2) {
                        cand_ms.push_back(rem[i]);
                        for (int dv : {2, 3, 4, 5}) {
                            if (rem[i] / dv >= 2) cand_ms.push_back(rem[i] / dv);
                        }
                    }
                }
                int est_sh = std::max(1, static_cast<int>(std::llround(static_cast<double>(rem_area) / sheet_area)));
                for (int tk : {1, 2, 3, 4}) {
                    int m_e = est_sh / tk;
                    if (m_e >= 2 && m_e <= max_r) cand_ms.push_back(m_e);
                }
                for (int fm : {200, 100, 50, 25, 20, 15, 10, 8, 6, 5, 4, 3, 2}) {
                    if (fm <= max_r) cand_ms.push_back(fm);
                }
                std::ranges::sort(cand_ms, std::greater<int>{});
                cand_ms.erase(std::ranges::unique(cand_ms).begin(), cand_ms.end());

                double best_sc = -1.0;
                PlatePlan best_pl;
                for (int m : cand_ms) {
                    std::vector<int> avail(M, 0);
                    long long av_area = 0;
                    int sum_av = 0;
                    for (int i = 0; i < M; ++i) {
                        avail[i] = rem[i] / m;
                        sum_av += avail[i];
                        av_area += static_cast<long long>(avail[i]) * order.items[i].w * order.items[i].h;
                    }
                    if (sum_av == 0) continue;
                    if (static_cast<double>(av_area) < cfg.min_fill * sheet_area && m > 2) continue;

                    std::vector<double> wts(M, 0.0);
                    for (int i = 0; i < M; ++i) {
                        double ba = static_cast<double>(order.items[i].w) * order.items[i].h;
                        double db = (avail[i] > 0 && rem[i] % m == 0) ? cfg.div_b : 0.0;
                        double sb = cfg.scar_b * (static_cast<double>(rem[i]) / std::max(1, max_r));
                        wts[i] = ba * (1.0 + db + sb);
                    }

                    PlatePlan cand = pack_single_plate_weighted(order, avail, wts);
                    int pidx = register_plate(cand);
                    if (pidx < 0) continue;
                    double fill = static_cast<double>(pool[pidx].used_area) / sheet_area;
                    if (fill >= cfg.min_fill || m == 2) {
                        int exact_hits = 0;
                        for (int i = 0; i < M; ++i) {
                            if (pool[pidx].counts[i] > 0 && pool[pidx].counts[i] * m == rem[i]) ++exact_hits;
                        }
                        double sc = std::pow(fill, 1.6) * std::log2(static_cast<double>(m) + 1.0) * (1.0 + 0.08 * exact_hits);
                        if (sc > best_sc) {
                            best_sc = sc;
                            best_pl = std::move(cand);
                        }
                    }
                }

                if (best_sc < 0.0 || best_pl.strips.empty()) break;
                int pidx = register_plate(best_pl);
                if (pidx < 0) break;
                int max_m = max_r;
                for (int i = 0; i < M; ++i) {
                    if (pool[pidx].counts[i] > 0) {
                        max_m = std::min(max_m, rem[i] / pool[pidx].counts[i]);
                    }
                }
                if (max_m < 1) break;
                for (int i = 0; i < M; ++i) rem[i] -= max_m * pool[pidx].counts[i];
                PlatePlan step_pl = pool[pidx].plate;
                step_pl.copies = max_m;
                traj.push_back(std::move(step_pl));
            }

            // Cleanup residual items
            int g_rem = 0;
            bool any_rem = false;
            for (int r : rem) {
                if (r > 0) {
                    any_rem = true;
                    g_rem = std::gcd(g_rem, r);
                }
            }
            if (any_rem) {
                g_rem = std::max(1, g_rem);
                OrderSpec sub_ord;
                sub_ord.sheet_w = W;
                sub_ord.sheet_h = H;
                std::vector<int> sub_to_orig;
                for (int i = 0; i < M; ++i) {
                    if (rem[i] > 0) {
                        sub_ord.items.push_back(ItemSpec{order.items[i].id, order.items[i].w, order.items[i].h, rem[i] / g_rem});
                        sub_to_orig.push_back(i);
                    }
                }
                double rem_t = std::max(0.03, std::min(0.18, std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count() * 0.25));
                auto sub_sol = solve_order(sub_ord, SolveMode::TightShelf, rem_t, seed + 97U);
                if (sub_sol) {
                    auto m_sub = merge_identical_plates(sub_sol->plates);
                    for (auto& pl : m_sub) {
                        for (auto& sp : pl.strips) {
                            for (auto& cp : sp.columns) {
                                for (auto& sl : cp.slices) {
                                    sl.item_idx = sub_to_orig[sl.item_idx];
                                    sl.item_id = order.items[sl.item_idx].id;
                                }
                            }
                        }
                        pl.copies *= g_rem;
                        traj.push_back(std::move(pl));
                    }
                }
            }
            register_solution(traj);
        }

        // 2d. Exact MRV Branch-and-Bound Recombination over Pattern Pool for Small Pattern Caps K = 1..6
        const int P = static_cast<int>(pool.size());
        std::vector<std::vector<int>> item_pats(M);
        for (int p = 0; p < P; ++p) {
            for (int i = 0; i < M; ++i) {
                if (pool[p].counts[i] > 0) item_pats[i].push_back(p);
            }
        }
        for (int i = 0; i < M; ++i) {
            std::ranges::sort(item_pats[i], [&](int a, int b2) {
                return pool[a].used_area > pool[b2].used_area;
            });
        }

        for (int cap_k = 1; cap_k <= 7; ++cap_k) {
            if (std::chrono::steady_clock::now() >= deadline) break;
            int best_sh_k = 1'000'000'000;
            for (const auto& cs : candidate_solutions) {
                if (static_cast<int>(cs.size()) <= cap_k) {
                    best_sh_k = std::min(best_sh_k, count_plates(cs));
                }
            }
            std::vector<int> cur_rem = demands;
            std::vector<std::pair<int, int>> chosen;
            std::vector<std::pair<int, int>> best_chosen;
            int nodes = 0;
            constexpr int MAX_NODES = 6000;

            auto dfs = [&](auto& self, int used_sh, long long rem_area) -> void {
                if (++nodes > MAX_NODES) return;
                if (rem_area == 0) {
                    if (used_sh < best_sh_k) {
                        best_sh_k = used_sh;
                        best_chosen = chosen;
                    }
                    return;
                }
                int rem_slots = cap_k - static_cast<int>(chosen.size());
                if (rem_slots <= 0) return;
                int lb_add = static_cast<int>((rem_area + static_cast<long long>(W) * H - 1) / (static_cast<long long>(W) * H));
                if (used_sh + lb_add >= best_sh_k) return;

                // Pick uncovered item with fewest covering patterns (MRV)
                int pivot_i = -1;
                std::size_t best_deg = 1'000'000;
                for (int i = 0; i < M; ++i) {
                    if (cur_rem[i] > 0) {
                        if (item_pats[i].size() < best_deg) {
                            best_deg = item_pats[i].size();
                            pivot_i = i;
                        }
                    }
                }
                if (pivot_i < 0) return;

                int tried = 0;
                for (int p : item_pats[pivot_i]) {
                    int max_m = cur_rem[pivot_i] / pool[p].counts[pivot_i];
                    if (max_m < 1) continue;
                    for (int j = 0; j < M; ++j) {
                        if (pool[p].counts[j] > 0) {
                            max_m = std::min(max_m, cur_rem[j] / pool[p].counts[j]);
                        }
                    }
                    if (max_m < 1) continue;
                    // Prefer full wipeout of pivot_i if divisible, plus max_m
                    if (++tried > 14) break;

                    for (int j = 0; j < M; ++j) cur_rem[j] -= max_m * pool[p].counts[j];
                    chosen.push_back({p, max_m});
                    self(self, used_sh + max_m, rem_area - max_m * pool[p].used_area);
                    chosen.pop_back();
                    for (int j = 0; j < M; ++j) cur_rem[j] += max_m * pool[p].counts[j];
                    if (nodes > MAX_NODES) return;
                }
            };

            long long tot_a = 0;
            for (int i = 0; i < M; ++i) tot_a += static_cast<long long>(demands[i]) * order.items[i].w * order.items[i].h;
            dfs(dfs, 0, tot_a);
            if (!best_chosen.empty()) {
                std::vector<PlatePlan> sol_k;
                for (const auto& [p, m] : best_chosen) {
                    PlatePlan pl = pool[p].plate;
                    pl.copies = m;
                    sol_k.push_back(std::move(pl));
                }
                register_solution(sol_k);
            }
        }
    }

    out.candidate_patterns = static_cast<int>(pool.size());
    out.trajectories_found = static_cast<int>(candidate_solutions.size());

    // Stage 3: Evaluate & Filter Non-Dominated Pareto Frontier
    struct EvalCand {
        std::vector<PlatePlan> plates;
        PlanEconomics econ;
    };
    std::vector<EvalCand> evals;
    for (auto& pls : candidate_solutions) {
        auto ec = compute_plan_economics(W, H, pls, cost);
        evals.push_back(EvalCand{std::move(pls), ec});
    }
    std::ranges::sort(evals, [](const EvalCand& a, const EvalCand& b2) {
        if (a.econ.sheets_used != b2.econ.sheets_used) return a.econ.sheets_used < b2.econ.sheets_used;
        if (a.econ.distinct_plates != b2.econ.distinct_plates) return a.econ.distinct_plates < b2.econ.distinct_plates;
        if (a.econ.total_strokes != b2.econ.total_strokes) return a.econ.total_strokes < b2.econ.total_strokes;
        return a.econ.total_cost_rmb < b2.econ.total_cost_rmb;
    });

    std::vector<EvalCand> pareto;
    for (auto& c : evals) {
        bool dom = false;
        for (const auto& prev : pareto) {
            bool le_all = (prev.econ.sheets_used <= c.econ.sheets_used) &&
                          (prev.econ.distinct_plates <= c.econ.distinct_plates) &&
                          (prev.econ.total_strokes <= c.econ.total_strokes);
            if (le_all) {
                dom = true;
                break;
            }
        }
        if (!dom) pareto.push_back(std::move(c));
    }

    for (std::size_t idx = 0; idx < pareto.size(); ++idx) {
        ParetoPlan pp;
        pp.plan_id = "pareto_" + std::to_string(idx);
        pp.plates = std::move(pareto[idx].plates);
        pp.economics = pareto[idx].econ;
        pp.cut_tree_csv = build_cut_tree_csv(order, pp.plates);
        auto ver = verify_cut_tree_csv(order, pp.cut_tree_csv);
        if (!ver) return std::unexpected(ver.error());
        pp.verification = *ver;
        out.frontier.push_back(std::move(pp));
    }

    if (out.frontier.empty()) {
        return std::unexpected(ErrorInfo{"PARETO_EMPTY", "No valid Pareto plan produced"});
    }

    // Tag named shop-floor choices
    std::size_t idx_paper = 0, idx_cost = 0, idx_setup = 0;
    for (std::size_t i = 1; i < out.frontier.size(); ++i) {
        const auto& e = out.frontier[i].economics;
        if (std::tie(e.sheets_used, e.total_cost_rmb) <
            std::tie(out.frontier[idx_paper].economics.sheets_used, out.frontier[idx_paper].economics.total_cost_rmb)) {
            idx_paper = i;
        }
        if (std::tie(e.total_cost_rmb, e.sheets_used) <
            std::tie(out.frontier[idx_cost].economics.total_cost_rmb, out.frontier[idx_cost].economics.sheets_used)) {
            idx_cost = i;
        }
        if (std::tie(e.distinct_plates, e.total_time_min, e.sheets_used) <
            std::tie(out.frontier[idx_setup].economics.distinct_plates, out.frontier[idx_setup].economics.total_time_min, out.frontier[idx_setup].economics.sheets_used)) {
            idx_setup = i;
        }
    }
    out.min_paper_id = out.frontier[idx_paper].plan_id;
    out.min_cost_id = out.frontier[idx_cost].plan_id;
    out.min_setup_id = out.frontier[idx_setup].plan_id;
    out.wall_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    return out;
}

std::string serialize_pareto_json(const ParetoOutput& out, std::string_view order_name) {
    std::ostringstream o;
    o << "{"
      << "\"ok\":true,"
      << "\"engine_standard\":\"C++23\","
      << "\"order_name\":\"" << json_escape(order_name) << "\","
      << "\"sheet_width\":" << out.order.sheet_w << ","
      << "\"sheet_height\":" << out.order.sheet_h << ","
      << "\"candidate_patterns\":" << out.candidate_patterns << ","
      << "\"trajectories_found\":" << out.trajectories_found << ","
      << "\"wall_ms\":" << fmt_f4(out.wall_ms) << ","
      << "\"recommended\":{"
      << "\"min_paper\":\"" << json_escape(out.min_paper_id) << "\","
      << "\"min_total_cost\":\"" << json_escape(out.min_cost_id) << "\","
      << "\"min_setup_fastest\":\"" << json_escape(out.min_setup_id) << "\""
      << "},"
      << "\"cost_model\":{"
      << "\"sheet_cost_rmb\":" << fmt_f4(out.cost_model.sheet_cost_rmb) << ","
      << "\"machine_rate_rmb_per_hour\":" << fmt_f4(out.cost_model.machine_rate_rmb_per_hour) << ","
      << "\"setup_minutes_per_pattern\":" << fmt_f4(out.cost_model.setup_minutes_per_pattern) << ","
      << "\"stroke_seconds\":" << fmt_f4(out.cost_model.stroke_seconds) << ","
      << "\"max_stack_sheets\":" << out.cost_model.max_stack_sheets
      << "},"
      << "\"frontier\":[";
    for (std::size_t i = 0; i < out.frontier.size(); ++i) {
        if (i > 0) o << ",";
        const auto& pp = out.frontier[i];
        const auto& ec = pp.economics;
        o << "{"
          << "\"plan_id\":\"" << json_escape(pp.plan_id) << "\","
          << "\"sheets_used\":" << ec.sheets_used << ","
          << "\"distinct_patterns\":" << ec.distinct_plates << ","
          << "\"clamp_stacks\":" << ec.clamp_stacks << ","
          << "\"total_strokes\":" << ec.total_strokes << ","
          << "\"unstacked_strokes\":" << ec.unstacked_strokes << ","
          << "\"utilization_pct\":" << fmt_f4(pp.verification.utilization_pct) << ","
          << "\"waste_pct\":" << fmt_f4(pp.verification.waste_pct) << ","
          << "\"setup_time_min\":" << fmt_f4(ec.setup_time_min) << ","
          << "\"cut_time_min\":" << fmt_f4(ec.cut_time_min) << ","
          << "\"total_time_min\":" << fmt_f4(ec.total_time_min) << ","
          << "\"paper_cost_rmb\":" << fmt_f4(ec.paper_cost_rmb) << ","
          << "\"setup_cost_rmb\":" << fmt_f4(ec.setup_cost_rmb) << ","
          << "\"cut_cost_rmb\":" << fmt_f4(ec.cut_cost_rmb) << ","
          << "\"total_cost_rmb\":" << fmt_f4(ec.total_cost_rmb) << ","
          << "\"verification\":" << serialize_verify_json(pp.verification, order_name) << ","
          << "\"cut_tree_csv\":\"" << json_escape(pp.cut_tree_csv) << "\","
          << "\"plates\":";
        serialize_plates_array(o, out.order, pp.plates);
        o << "}";
    }
    o << "]}";
    return o.str();
}

} // namespace mill
