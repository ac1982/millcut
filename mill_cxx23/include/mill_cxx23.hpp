#pragma once
// Pure C++23 Zero-Dependency 3-Stage NonExact Horizontal Guillotine Paper Mill Engine.
// Supports both native x86_64/ARM64 CLI execution and Emscripten WebAssembly (.wasm).

#include <array>
#include <cstdint>
#include <expected>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace mill {

enum class SolveMode : uint8_t {
    MillRouter = 0,     // Scale-invariant 3-branch router (ColStrip + TightShelf + GeneralBeam)
    ColStripOnly = 1,   // Force Two-Tier Column/Strip + Dual Pricing + Bit-Parallel Exact BnB
    BaselineGreedy = 2, // Fast classical FFDH / Shelf baseline for instant live side-by-side comparison
    TightShelf = 3      // Guide-1 style tight-shelf iterative guillotine search
};

struct ErrorInfo {
    std::string code;
    std::string message;
};

struct ItemSpec {
    int id = 0;
    int w = 0;
    int h = 0;
    int copies = 1;
};

struct OrderSpec {
    int sheet_w = 0;
    int sheet_h = 0;
    std::vector<ItemSpec> items;
};

struct Features {
    int items = 0;
    int distinct_shapes = 0;
    double repeat_fraction = 0.0;
    int area_lower_bound = 1;
    double lower_bound_fill = 0.0;
    double unique_width_fraction = 0.0;
    double shape_unique_width_fraction = 0.0;
    double unique_height_fraction = 0.0;
    double shape_unique_height_fraction = 0.0;
    double width_cv = 0.0;
    double height_cv = 0.0;
    double area_cv = 0.0;
    double stack2_partner_fraction = 0.0;
};

struct PlacedItem {
    int item_idx = 0; // index into OrderSpec::items
    int item_id = 0;  // external ItemSpec::id
    int x = 0;
    int y = 0;
    int w = 0;
    int h = 0;
};

struct ColumnPlan {
    int x = 0;
    int y = 0;
    int w = 0;
    int h = 0; // strip height
    std::vector<PlacedItem> slices; // 1..3 stacked items inside this column
};

struct StripPlan {
    int y = 0;
    int h = 0;
    std::vector<ColumnPlan> columns;
};

struct PlatePlan {
    int plate_id = 0;
    int copies = 1;
    std::vector<StripPlan> strips;
};

struct VerifyReport {
    bool valid = false;
    int sheets_used = 0;
    int distinct_plates = 0;
    int max_effective_stages = 0;
    int sheet_w = 0;
    int sheet_h = 0;
    long long total_item_area = 0;
    long long total_sheet_area = 0;
    long long waste_area = 0;
    double utilization_pct = 0.0;
    double waste_pct = 0.0;
    int last_plate_residual_height = 0;
};

struct SolveStats {
    int initial_bins = 0;
    int final_bins = 0;
    int lower_bound = 1;
    int generated_strips_r1 = 0;
    int generated_strips_r2 = 0;
    int retained_strips = 0;
    int candidate_columns = 0;
    double dual_lower_bound_height = 0.0;
    long long bnb_nodes = 0;
    double gate_ms = 0.0;
    double wall_ms = 0.0;
    std::string branch;
    std::string winning_engine;
    std::string bnb_status;
};

struct SolveOutput {
    OrderSpec order;
    Features features;
    SolveStats stats;
    std::vector<PlatePlan> plates;
    std::string cut_tree_csv;
    VerifyReport verification;
};

struct CostModel {
    double sheet_cost_rmb = 2.00;
    double machine_rate_rmb_per_hour = 180.0;
    double setup_minutes_per_pattern = 6.0;
    double stroke_seconds = 9.0;
    int max_stack_sheets = 200;
};

struct PlanEconomics {
    int sheets_used = 0;
    int distinct_plates = 0;
    int clamp_stacks = 0;
    int total_strokes = 0;
    int unstacked_strokes = 0;
    double setup_time_min = 0.0;
    double cut_time_min = 0.0;
    double total_time_min = 0.0;
    double paper_cost_rmb = 0.0;
    double setup_cost_rmb = 0.0;
    double cut_cost_rmb = 0.0;
    double total_cost_rmb = 0.0;
};

struct ParetoPlan {
    std::string plan_id;
    std::vector<PlatePlan> plates;
    std::string cut_tree_csv;
    VerifyReport verification;
    PlanEconomics economics;
};

struct ParetoOutput {
    OrderSpec order;
    Features features;
    CostModel cost_model;
    int candidate_patterns = 0;
    int trajectories_found = 0;
    double wall_ms = 0.0;
    std::string min_paper_id;
    std::string min_cost_id;
    std::string min_setup_id;
    std::vector<ParetoPlan> frontier;
};

// C++23 zero-overhead 2D contiguous matrix view with multidimensional subscript operator[](r, c)
template <typename T>
class FlatGrid2D {
public:
    constexpr FlatGrid2D() = default;
    constexpr FlatGrid2D(T* data, std::size_t rows, std::size_t cols) noexcept
        : data_(data), rows_(rows), cols_(cols) {}

    [[nodiscard]] constexpr T& operator[](std::size_t r, std::size_t c) noexcept {
        return data_[r * cols_ + c];
    }
    [[nodiscard]] constexpr const T& operator[](std::size_t r, std::size_t c) const noexcept {
        return data_[r * cols_ + c];
    }
    [[nodiscard]] constexpr std::span<T> row(std::size_t r) noexcept {
        return {data_ + r * cols_, cols_};
    }
    [[nodiscard]] constexpr std::span<const T> row(std::size_t r) const noexcept {
        return {data_ + r * cols_, cols_};
    }
    [[nodiscard]] constexpr std::size_t rows() const noexcept { return rows_; }
    [[nodiscard]] constexpr std::size_t cols() const noexcept { return cols_; }

private:
    T* data_ = nullptr;
    std::size_t rows_ = 0;
    std::size_t cols_ = 0;
};

// Core API returning std::expected (no C++ exceptions required)
[[nodiscard]] std::expected<OrderSpec, ErrorInfo> parse_order_csvs(
    std::string_view bins_csv,
    std::string_view items_csv);

[[nodiscard]] Features extract_features(const OrderSpec& order);

[[nodiscard]] std::string route_order(const Features& feat);

[[nodiscard]] std::expected<SolveOutput, ErrorInfo> solve_order(
    const OrderSpec& order,
    SolveMode mode = SolveMode::MillRouter,
    double budget_s = 10.0,
    uint32_t seed = 501);

[[nodiscard]] std::string build_cut_tree_csv(
    const OrderSpec& order,
    std::span<const PlatePlan> plates);

[[nodiscard]] std::expected<VerifyReport, ErrorInfo> verify_cut_tree_csv(
    const OrderSpec& order,
    std::string_view cut_tree_csv);

[[nodiscard]] std::string serialize_solve_json(
    const SolveOutput& out,
    std::string_view order_name = "order");

[[nodiscard]] std::string serialize_verify_json(
    const VerifyReport& rep,
    std::string_view order_name = "order");

[[nodiscard]] std::expected<ParetoOutput, ErrorInfo> solve_pareto_order(
    const OrderSpec& order,
    const CostModel& cost = CostModel{},
    double budget_s = 5.0,
    uint32_t seed = 501);

[[nodiscard]] std::string serialize_pareto_json(
    const ParetoOutput& out,
    std::string_view order_name = "order");

} // namespace mill
